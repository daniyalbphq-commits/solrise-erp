#!/usr/bin/env python3
"""Patch the pinned agritheory/cloud_storage release for the Solrise AWS stack.

Four edits are applied to the copy of the app that the image build installs:

1. ``validate_config()`` tolerates a missing ``access_key``/``secret``. With both
   absent, ``boto3.Session(None, None, region)`` falls back to the default
   credential chain - i.e. the EC2 instance profile - so no long-lived AWS access
   keys have to live in Ansible Vault, in the site config file or on the host.

2. ``write_file()`` no longer keeps ``Data Import`` attachments on the local
   filesystem. Upstream special-cases that doctype, but nothing needs it: the
   importer reads the attachment through ``File.get_content()``
   (``frappe/core/doctype/data_import/importer.py``), which this app already
   serves from S3. Without this the only uploads still landing on the EC2 volume
   are import spreadsheets.

3. ``make_thumbnail()`` is overridden so nothing is ever written to the
   instance's disk behind the storage hook. Core's version resizes the image and
   saves it under ``public/<name>_<suffix>.<ext>``; the only production caller is
   HRMS Daily Work Summary emails. Cloud files are served at full size through
   ``/api/method/retrieve`` instead.

4. The app's ``after_install`` hook stops demanding the Debian ``libreoffice``
   package, which it only uses to render PPT/ODP previews (a feature this
   deployment does not use). Leaving it in would make
   ``bench install-app cloud_storage`` apt-get ~500 MB inside the ephemeral
   create-site container, where there is no sudo and the packages would be
   discarded with the container anyway.

Every edit is exact-match and asserted: if the pinned release no longer looks the
way this script expects, the *image build* fails instead of shipping a
half-configured app. Re-running the script is a no-op, so rebuilding an
unchanged source tree stays byte-identical.

Usage (from the image build in infra/image/Containerfile):
    python3 patch-cloud-storage.py /home/frappe/frappe-bench/apps/cloud_storage
"""

import ast
import re
import sys
from pathlib import Path

# Every line this script adds is tagged with MARKER, which also makes the script
# idempotent: a tagged file is a patched file.
MARKER = "Solrise:"

OVERRIDE_FILE = Path("cloud_storage/cloud_storage/overrides/file.py")
INSTALL_FILE = Path("cloud_storage/install.py")

# config keys whose presence checks must survive the patch, as a sanity check.
KEPT_GUARDS = ("endpoint_url", "region", "bucket")

# -----------------------------------------------------------------------------
# 3. the make_thumbnail override, inserted as the first member of
#    CloudStorageFile. Anchoring on the class header (rather than "before the next
#    module-level def") is what keeps it inside the class: an earlier version of
#    this patch inserted it after `convert_to_pdf_base64`, where the same
#    indentation silently made it a *nested function* - dead code that still
#    compiled. sanity_check() now parses the result and fails the build if that
#    ever happens again.
# -----------------------------------------------------------------------------
THUMBNAIL_METHOD = (
    "\tdef make_thumbnail(\n"
    "\t\tself,\n"
    "\t\tset_as_thumbnail: bool = True,\n"
    "\t\twidth: int = 300,\n"
    "\t\theight: int = 300,\n"
    '\t\tsuffix: str = "small",\n'
    "\t\tcrop: bool = False,\n"
    "\t):\n"
    '\t\t"""{marker} never write a thumbnail to the instance\'s disk.\n'
    "\n"
    "\t\tCore resizes the image and saves it under public/<name>_<suffix>.<ext> on\n"
    "\t\tthe local filesystem, bypassing the storage hook, so the only production\n"
    "\t\tcaller (HRMS Daily Work Summary emails) would leave files on the EC2\n"
    "\t\tvolume. Cloud files are served at full size through /api/method/retrieve,\n"
    "\t\twhich needs no derived object.\n"
    '\t\t"""\n'
    "\t\tif not self.file_url:\n"
    "\t\t\treturn\n"
    "\n"
    "\t\tif set_as_thumbnail:\n"
    '\t\t\tself.db_set("thumbnail_url", self.file_url)\n'
    "\n"
    "\t\treturn self.file_url\n"
    "\n"
    "\n"
).format(marker=MARKER)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    # "w" keeps the existing inode, so the app files stay owned by `frappe`.
    path.write_text(text, encoding="utf-8")


def sub_once(path: Path, pattern: re.Pattern, replacement: str, sentinel: str, label: str) -> str:
    """Apply one edit, or report that it was already applied."""
    src = read(path)
    if sentinel in src:
        return "already patched"

    src, count = pattern.subn(replacement, src)
    if count != 1:
        raise SystemExit(
            f"ERROR: {path}: expected exactly one match for the {label}, got {count}. "
            "The pinned cloud_storage release changed - re-check it and update "
            "infra/image/patch-cloud-storage.py."
        )

    write(path, src)
    return "patched"


def allow_instance_profile_credentials(path: Path) -> str:
    """1. Drop the access_key/secret presence checks from validate_config()."""
    statuses = [
        # `<indent>if not config.get("<key>"):` plus the indented frappe.throw(...)
        # block that follows it. The blank line after the block ends the match.
        sub_once(
            path,
            re.compile(r'(?m)^([ \t]*)if not config\.get\("%s"\):\n(?:[ \t]+\S.*\n)+' % re.escape(key)),
            r"\1# %s when %s is absent, boto3 falls back to its default\n"
            r"\1# credential chain (the EC2 instance profile).\n" % (MARKER, key),
            sentinel="when %s is absent" % key,
            label=f"{key} guard",
        )
        for key in ("access_key", "secret")
    ]
    return "patched" if "patched" in statuses else "already patched"


def send_data_import_attachments_to_s3(path: Path) -> str:
    """2. Remove the Data Import bypass, so those attachments go to the bucket."""
    return sub_once(
        path,
        re.compile(r'(?m)^([ \t]*)if file\.attached_to_doctype == "Data Import":\n(?:[ \t]+\S.*\n)+'),
        r"\1# %s Data Import attachments go to the bucket as well: the importer\n"
        r"\1# reads them through File.get_content() (data_import/importer.py), which this\n"
        r"\1# app serves from S3, so no local copy is needed - or left behind.\n" % MARKER,
        sentinel="Data Import attachments go to the bucket",
        label="Data Import bypass",
    )


def no_local_thumbnails(path: Path) -> str:
    """3. Stop make_thumbnail() writing a derived image to the instance's disk."""
    return sub_once(
        path,
        re.compile(r"(?m)^class CloudStorageFile\(File\):$"),
        "class CloudStorageFile(File):\n" + THUMBNAIL_METHOD,
        sentinel="never write a thumbnail to the instance",
        label="make_thumbnail override",
    )


def drop_libreoffice_requirement(path: Path) -> str:
    """4. Keep libmagic1 as the only system package the install hook checks for."""
    return sub_once(
        path,
        re.compile(r'^DEBIAN_PACKAGES = \["libmagic1", "libreoffice"\]$', re.M),
        'DEBIAN_PACKAGES = ["libmagic1"]  '
        f"# {MARKER} libreoffice (PPT/ODP previews) is unused here",
        sentinel="libreoffice (PPT/ODP previews) is unused here",
        label="DEBIAN_PACKAGES line",
    )


def sanity_check(path: Path) -> None:
    """Fail the build if a patch removed something this deployment relies on."""
    src = read(path)

    for key in KEPT_GUARDS:
        if f'if not config.get("{key}")' not in src:
            raise SystemExit(
                f"ERROR: {path}: the '{key}' guard disappeared - the patch assumptions "
                "are stale, update infra/image/patch-cloud-storage.py."
            )

    # The local-filesystem fallback for a deliberately unconfigured/off site must
    # survive: it is what keeps uploads working if S3 is switched off.
    if '"use_local", False' not in src or "file.save_file_on_filesystem()" not in src:
        raise SystemExit(
            f"ERROR: {path}: the use_local fallback disappeared - the patch assumptions "
            "are stale, update infra/image/patch-cloud-storage.py."
        )

    if "def is_safe_path(path: str) -> bool:" not in src:
        raise SystemExit(
            f"ERROR: {path}: the module-level is_safe_path helper disappeared - the "
            "patch assumptions are stale, update infra/image/patch-cloud-storage.py."
        )

    # The override has to be a *method of CloudStorageFile*: inserting it at the
    # wrong offset yields a nested function instead - valid Python, never called.
    classes = [
        node for node in ast.parse(src).body if isinstance(node, ast.ClassDef) and node.name == "CloudStorageFile"
    ]
    methods = [
        child.name
        for node in classes
        for child in node.body
        if isinstance(child, ast.FunctionDef)
    ]
    for expected in ("make_thumbnail", "get_content"):
        if expected not in methods:
            raise SystemExit(
                f"ERROR: {path}: '{expected}' is not a CloudStorageFile method after patching "
                f"(methods found: {sorted(methods)}). The insertion offset is wrong - fix "
                "infra/image/patch-cloud-storage.py."
            )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    app = Path(argv[1])
    targets = (
        (OVERRIDE_FILE, allow_instance_profile_credentials),
        (OVERRIDE_FILE, send_data_import_attachments_to_s3),
        (OVERRIDE_FILE, no_local_thumbnails),
        (INSTALL_FILE, drop_libreoffice_requirement),
    )

    for relative, patch in targets:
        path = app / relative
        if not path.is_file():
            raise SystemExit(f"ERROR: {path} not found (app layout changed?)")
        print(f"{relative}: {patch.__name__}: {patch(path)}")

    sanity_check(app / OVERRIDE_FILE)
    print(f"cloud_storage patched in {app}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
