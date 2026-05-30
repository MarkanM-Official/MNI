#!/usr/bin/env python3
"""
Prepare a local MNI Automation Manager checkout for development.

This creates safe local config files, prepares the instance directory, and
initializes the database tables without starting background bot workers.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run(command):
    print("$ " + " ".join(command))
    subprocess.check_call(command, cwd=PROJECT_ROOT)


def copy_if_missing(source, target):
    if target.exists():
        print(f"ok: {target.relative_to(PROJECT_ROOT)} already exists")
        return
    shutil.copyfile(source, target)
    print(f"created: {target.relative_to(PROJECT_ROOT)}")


def install_dependencies(include_node):
    run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    if include_node:
        run(["npm", "install"])


def initialize_database():
    os.environ.setdefault("MARKANM_DISABLE_BACKGROUND_WORKERS", "true")
    os.environ.setdefault("FLASK_DEBUG", "False")
    sys.path.insert(0, str(PROJECT_ROOT))

    from backend.app import create_app
    from backend.database import db

    app = create_app()
    with app.app_context():
        db.create_all()
    print("ok: database tables are ready")


def main():
    parser = argparse.ArgumentParser(description="Set up local env and database.")
    parser.add_argument(
        "--install-deps",
        action="store_true",
        help="Install Python dependencies before initializing the app.",
    )
    parser.add_argument(
        "--install-node",
        action="store_true",
        help="Also run npm install. Use with --install-deps for a full setup.",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Only create local config files, do not initialize the database.",
    )
    args = parser.parse_args()

    (PROJECT_ROOT / "instance").mkdir(exist_ok=True)
    copy_if_missing(PROJECT_ROOT / ".env.example", PROJECT_ROOT / ".env")
    copy_if_missing(
        PROJECT_ROOT / "config" / "google_auth.example.json",
        PROJECT_ROOT / "config" / "google_auth.json",
    )

    if args.install_deps:
        install_dependencies(args.install_node)

    if not args.skip_db:
        try:
            initialize_database()
        except ModuleNotFoundError as exc:
            missing = exc.name or "dependency"
            print(f"missing dependency: {missing}")
            print("run: python scripts/setup_local.py --install-deps --install-node")
            raise SystemExit(1) from exc

    print("done: local setup is ready")


if __name__ == "__main__":
    main()
