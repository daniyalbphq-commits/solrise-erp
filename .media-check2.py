"""Faithful re-check of scripts/configure_s3_media.py.

Difference from the earlier harness: `frappe.installer` is registered as a real
submodule in sys.modules but deliberately NOT attached as an attribute of the
`frappe` module - exactly like real Frappe. The previous harness set
frappe.installer directly, which is why it passed while the host crashed with
"module 'frappe' has no attribute 'installer'".
"""
import importlib.util
import io
import os
import sys
import types
from contextlib import redirect_stdout, redirect_stderr

SCRIPT = "scripts/configure_s3_media.py"
calls = {"update": [], "migrate": []}


def fresh(fail=False, preset=None):
    frappe = types.ModuleType("frappe")
    conf = dict(preset or {})
    frappe.local = types.SimpleNamespace(conf=conf)
    frappe.conf = conf
    frappe.init = lambda site, sites_path: None
    frappe.connect = lambda: None
    frappe.destroy = lambda: None
    sys.modules["frappe"] = frappe

    # A separate module object, importable as frappe.installer but NOT an
    # attribute of `frappe` until something imports it - the real behaviour.
    installer = types.ModuleType("frappe.installer")

    def update_site_config(key, value):
        calls["update"].append(value)
        conf[key] = value

    installer.update_site_config = update_site_config
    sys.modules["frappe.installer"] = installer

    store = {}

    class Client:
        bucket = "stub"

        def put_object(self, Bucket, Key, Body, ContentType=None):
            if fail:
                raise RuntimeError("connection refused")
            store[Key] = Body

        def get_object(self, Bucket, Key):
            return {"Body": io.BytesIO(store[Key])}

        def delete_object(self, Bucket, Key):
            store.pop(Key, None)

    for name in (
        "cloud_storage",
        "cloud_storage.cloud_storage",
        "cloud_storage.cloud_storage.overrides",
        "cloud_storage.cloud_storage.overrides.file",
        "cloud_storage.migration",
    ):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["cloud_storage.cloud_storage.overrides.file"].get_cloud_storage_client = lambda: Client()
    sys.modules["cloud_storage.migration"].migrate_files = lambda **kw: calls["migrate"].append(kw)
    return frappe


def run(name, env, fail=False, preset=None):
    calls["update"].clear()
    calls["migrate"].clear()
    for key in [k for k in os.environ if k.startswith("S3_MEDIA_")]:
        del os.environ[key]
    fresh(fail=fail, preset=preset)
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location("cfg", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.main()
    return code, out.getvalue(), err.getvalue(), calls["update"]


BASE = dict(
    SITE_NAME="erp.example.com",
    S3_MEDIA_BUCKET="solrise-media-123",
    S3_MEDIA_REGION="us-east-1",
    S3_MEDIA_ENDPOINT_URL="https://s3.us-east-1.amazonaws.com",
    S3_MEDIA_FOLDER="solrise",
)

EXPECTED = {
    "region": "us-east-1",
    "endpoint_url": "https://s3.us-east-1.amazonaws.com",
    "bucket": "solrise-media-123",
    "folder": "solrise",
    "expiration": 300,
}

code, out, err, writes = run("fresh", BASE)
assert code == 0, (code, err)
assert "site config: updated" in out, out
assert writes == [EXPECTED], writes
print("fresh run: config written ->", "site config: updated" in out)

# the crash path from the host: no `frappe.installer` attribute set anywhere
assert "AttributeError" not in err, err
print("no AttributeError on frappe.installer")

code, out, err, writes = run("unchanged", BASE, preset={"cloud_storage_settings": EXPECTED})
assert code == 0 and "site config: unchanged" in out and writes == [], (code, out, writes)
print("idempotent re-run: unchanged, no write")

code, out, err, writes = run("probe fails", BASE, fail=True)
assert code == 1 and writes == [] and "round trip failed" in err, (code, err)
print("failed probe: exit 1, nothing written")

code, out, err, writes = run("migrate", {**BASE, "S3_MEDIA_MIGRATE_EXISTING": "1"})
assert calls["migrate"] == [{"dry_run": True, "remove_local": False}], calls["migrate"]
print("migration hook: called with dry_run=True")

print("\nconfigure_s3_media.py faithful re-check passed")
