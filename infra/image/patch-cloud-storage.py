#!/usr/bin/env python3
"""Patch the pinned agritheory/cloud_storage release for the Solrise AWS stack.

Two edits are applied to the copy of the app that the image build installs:

1. ``validate_config()`` tolerates a missing ``access_key``/``secret``. With both
   absent, ``boto3.Session(None, None, region)`` falls back to the default
   credential chain - i.e. the EC2 instance profile - so no long-lived AWS access
   keys have to live in Ansible Vault, in the site config file or on the host.

2. The app's ``after_install`` hook stops demanding the Debian ``libreoffice``
   package, which it only uses to render PPT/ODP previews (a feature this
   deployment does not use). Leaving it in would make
   ``bench install-app cloud_storage`` apt-get ~500 MB inside the ephemeral
   create-site container, where there is no sudo and the packages would be
   discarded with the container anyway.

Both edits are exact-match and asserted: if the pinned release no longer looks
the way this script expects, the *image build* fails instead of shipping a
half-configured app. Re-running the script is a no-op, so rebuilding an
unchanged source tree stays byte-identical.

Usage (from the image build in infra/image/Containerfile):
    python3 patch-cloud-storage.py /home/frappe/frappe-bench/apps/cloud_storage
"""

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


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    # "w" keeps the existing inode, so the app files stay owned by `frappe`.
    path.write_text(text, encoding="utf-8")


def allow_instance_profile_credentials(path: Path) -> str:
    """Drop the access_key/secret presence checks from validate_config()."""
    src = read(path)
    if MARKER in src:
        return "already patched"

    for key in ("access_key", "secret"):
        # `<indent>if not config.get("<key>"):` plus the indented frappe.throw(...)
        # block that follows it. The blank line after the block ends the match.
        pattern = re.compile(
            r'(?m)^([ \t]*)if not config\.get\("%s"\):\n(?:[ \t]+\S.*\n)+' % re.escape(key)
        )
        replacement = (
            r"\1# %s when %s is absent, boto3 falls back to its default\n"
            r"\1# credential chain (the EC2 instance profile).\n" % (MARKER, key)
        )
        src, count = pattern.subn(replacement, src)
        if count != 1:
            raise SystemExit(
                f"ERROR: {path}: expected exactly one '{key}' guard, patched {count}. "
                "The pinned cloud_storage release changed - re-check it and update "
                "infra/image/patch-cloud-storage.py."
            )

    for key in KEPT_GUARDS:
        if f'if not config.get("{key}")' not in src:
            raise SystemExit(
                f"ERROR: {path}: the '{key}' guard disappeared - the patch assumptions "
                "are stale, update infra/image/patch-cloud-storage.py."
            )

    write(path, src)
    return "patched: instance-profile credentials allowed"


def drop_libreoffice_requirement(path: Path) -> str:
    """Keep libmagic1 as the only system package the install hook checks for."""
    src = read(path)
    if MARKER in src:
        return "already patched"

    pattern = re.compile(r'^DEBIAN_PACKAGES = \["libmagic1", "libreoffice"\]$', re.M)
    replacement = (
        'DEBIAN_PACKAGES = ["libmagic1"]  '
        f"# {MARKER} libreoffice (PPT/ODP previews) is unused here"
    )
    src, count = pattern.subn(replacement, src)
    if count != 1:
        raise SystemExit(
            f"ERROR: {path}: expected exactly one DEBIAN_PACKAGES line, patched {count}. "
            "The pinned cloud_storage release changed - re-check it and update "
            "infra/image/patch-cloud-storage.py."
        )

    write(path, src)
    return "patched: libreoffice no longer required"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    app = Path(argv[1])
    targets = (
        (OVERRIDE_FILE, allow_instance_profile_credentials),
        (INSTALL_FILE, drop_libreoffice_requirement),
    )

    for relative, patch in targets:
        path = app / relative
        if not path.is_file():
            raise SystemExit(f"ERROR: {path} not found (app layout changed?)")
        print(f"{relative}: {patch(path)}")

    print(f"cloud_storage patched in {app}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
