"""
Solrise ERP - point Frappe's file storage at the S3 media bucket.

Writes `cloud_storage_settings` into the site's site_config.json - the dict the
cloud_storage app reads on every upload, download and delete - then proves the
credentials work with a real round trip through the bucket, and (opt-in)
migrates the files that are still on the local volume.

Normally run for you by the deploy:
    SITE_ENV=aws ./scripts/setup-media.sh

Inputs come from the environment (rendered by Ansible into .env):
    S3_MEDIA_BUCKET, S3_MEDIA_REGION, S3_MEDIA_ENDPOINT_URL, S3_MEDIA_FOLDER,
    S3_MEDIA_PRESIGNED_EXPIRY, S3_MEDIA_VERIFY, S3_MEDIA_WRITE_LOCAL,
    S3_MEDIA_ACCESS_KEY / S3_MEDIA_SECRET_KEY (optional: providers with no
    instance profile), S3_MEDIA_MIGRATE_EXISTING, S3_MEDIA_MIGRATE_DRY_RUN,
    S3_MEDIA_MIGRATE_REMOVE_LOCAL

Safe to re-run: settings are written only when they changed, the probe object is
deleted again, and an already-offloaded file is never uploaded twice.
"""
import os
import sys

import frappe

SITE = os.environ.get("SITE_NAME", "erp.localhost")
SITES_PATH = "/home/frappe/frappe-bench/sites"

BUCKET = os.environ.get("S3_MEDIA_BUCKET", "").strip()
REGION = os.environ.get("S3_MEDIA_REGION", "").strip()
ENDPOINT_URL = os.environ.get("S3_MEDIA_ENDPOINT_URL", "").strip()
FOLDER = os.environ.get("S3_MEDIA_FOLDER", "").strip().strip("/")
ACCESS_KEY = os.environ.get("S3_MEDIA_ACCESS_KEY", "").strip()
SECRET_KEY = os.environ.get("S3_MEDIA_SECRET_KEY", "").strip()
PROBE_KEY = f"{FOLDER + '/' if FOLDER else ''}_solrise-media-probe.txt"
PROBE_BODY = b"solrise media probe"


def env_flag(name, default="0"):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def env_int(name, default):
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def desired_settings():
    """The `cloud_storage_settings` dict this deployment should have."""
    settings = {
        "region": REGION,
        "endpoint_url": ENDPOINT_URL,
        "bucket": BUCKET,
        # Presigned URLs are what the browser gets for a private file.
        "expiration": env_int("S3_MEDIA_PRESIGNED_EXPIRY", 300),
    }
    if FOLDER:
        settings["folder"] = FOLDER
    if env_flag("S3_MEDIA_WRITE_LOCAL"):
        # Stop *new* uploads going to the bucket (an S3 outage, or a deliberate
        # step back) while files already in the bucket stay readable, because
        # their file_url still resolves through this app.
        settings["use_local"] = 1
    if ACCESS_KEY and SECRET_KEY:
        # Only for S3-compatible providers without an instance profile. On AWS
        # both stay empty and boto3 resolves the instance profile itself.
        settings["access_key"] = ACCESS_KEY
        settings["secret"] = SECRET_KEY
    return settings


def report_settings(settings):
    print(
        "  bucket={bucket} region={region} endpoint_url={endpoint_url} folder={folder}".format(
            bucket=settings["bucket"],
            region=settings["region"],
            endpoint_url=settings["endpoint_url"],
            folder=settings.get("folder", "(none)"),
        )
    )
    print(
        "  credentials="
        + ("explicit access key" if settings.get("access_key") else "instance profile (boto3 default chain)")
    )
    if settings.get("use_local"):
        print("  use_local: new uploads stay on the volume")


def write_settings(settings):
    frappe.installer.update_site_config("cloud_storage_settings", settings)
    # frappe.conf is a proxy for frappe.local.conf, loaded once per process, so
    # refresh it in place: the rest of this run then sees exactly what the next
    # request will read from disk.
    frappe.local.conf["cloud_storage_settings"] = settings


def verify():
    """Upload, read back and delete a probe object using the app's own client."""
    # Imported here (not at module level) so a missing/incomplete app install
    # reports as a clear message rather than a traceback at import time.
    from cloud_storage.cloud_storage.overrides.file import get_cloud_storage_client

    try:
        client = get_cloud_storage_client()
        client.put_object(Bucket=client.bucket, Key=PROBE_KEY, Body=PROBE_BODY, ContentType="text/plain")
        body = client.get_object(Bucket=client.bucket, Key=PROBE_KEY)["Body"].read()
        client.delete_object(Bucket=client.bucket, Key=PROBE_KEY)
    except Exception as exc:  # noqa: BLE001 - report anything, with a way forward
        print(f"ERROR: media bucket round trip failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        print(
            "  Check, in this order:\n"
            "    1. the instance can reach the metadata service from inside the container\n"
            "       (http_put_response_hop_limit = 2 in infra/terraform/compute.tf)\n"
            "    2. the instance role carries the media policy (terraform apply)\n"
            "    3. bucket, region and endpoint_url above match the real bucket\n"
            "  Details and a credentials fallback: docs/15-s3-media-storage.md",
            file=sys.stderr,
        )
        return False

    if body != PROBE_BODY:
        print("ERROR: media bucket returned unexpected content", file=sys.stderr)
        return False

    print(f"bucket round trip: OK (s3://{BUCKET}/{PROBE_KEY}, deleted)")
    return True


def migrate(dry_run, remove_local):
    from cloud_storage.migration import migrate_files

    print(f"migrating files still on disk (dry_run={dry_run}, remove_local={remove_local}) ...")
    migrate_files(dry_run=dry_run, remove_local=remove_local)


def main():
    if not BUCKET or not REGION or not ENDPOINT_URL:
        print("media storage is not configured (S3_MEDIA_BUCKET/REGION/ENDPOINT_URL) - nothing to do")
        return 0

    frappe.init(site=SITE, sites_path=SITES_PATH)
    frappe.connect()
    try:
        settings = desired_settings()
        changed = (frappe.conf.get("cloud_storage_settings") or {}) != settings
        report_settings(settings)

        # Verify against the candidate settings *in memory* before writing
        # anything: a bucket this host cannot use must not replace a working
        # (local) configuration. A failure here leaves the site untouched and
        # fails the deploy with something to act on.
        frappe.local.conf["cloud_storage_settings"] = settings
        if env_flag("S3_MEDIA_VERIFY", "1") and not verify():
            return 1

        if changed:
            write_settings(settings)
            print("site config: updated")
        else:
            print("site config: unchanged")

        if env_flag("S3_MEDIA_MIGRATE_EXISTING"):
            migrate(
                dry_run=env_flag("S3_MEDIA_MIGRATE_DRY_RUN", "1"),
                remove_local=env_flag("S3_MEDIA_MIGRATE_REMOVE_LOCAL"),
            )
    finally:
        frappe.destroy()

    print(f"media storage configured on {SITE}: s3://{BUCKET}/{FOLDER or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
