"""Manage ScaleEnvironment logins in dbo.ScalerUsers.

    python tools/add_user.py <username>              add a user, or reset their password
    python tools/add_user.py <username> --disable    block a user
    python tools/add_user.py <username> --enable     unblock a user

Reads the AUDIT_SQL_* settings from the environment, falling back to local.settings.json.
"""
import argparse
import getpass
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from shared_code import audit  # noqa: E402


def _load_local_settings():
    path = os.path.join(PROJECT_ROOT, "local.settings.json")
    if os.path.exists(path):
        with open(path) as f:
            for key, value in json.load(f).get("Values", {}).items():
                os.environ.setdefault(key, value)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("username")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--disable", action="store_true")
    group.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    _load_local_settings()

    if args.disable or args.enable:
        changed = audit.set_user_active(args.username, args.enable)
        print(f"{'Enabled' if args.enable else 'Disabled'} '{args.username}'." if changed else f"No user '{args.username}'.")
        return

    if ":" in args.username:
        sys.exit("Username cannot contain ':' (it separates username and password in Basic Auth).")
    password = getpass.getpass(f"Password for {args.username}: ")
    if len(password) < 8:
        sys.exit("Password must be at least 8 characters.")
    if password != getpass.getpass("Confirm password: "):
        sys.exit("Passwords do not match.")
    audit.upsert_user(args.username, password)
    print(f"Saved user '{args.username}'.")


if __name__ == "__main__":
    main()
