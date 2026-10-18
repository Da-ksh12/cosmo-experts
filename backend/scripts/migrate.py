"""PostgreSQL schema migrations for Cosmo Experts AI.

    python -m scripts.migrate status                 # what is applied, pending, drifted
    python -m scripts.migrate apply [--target N] [--dry-run]
    python -m scripts.migrate verify                 # recorded checksums match the files and every expected table exists

The credential comes from COSMO_MIGRATION_DATABASE_URL (or COSMO_MIGRATION_DATABASE_URL_FILE) and must be an OWNER of the application schema. It is never printed:
only host:port/database is shown. The running application never uses this credential."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.dbcore import migrate  # noqa: E402


def _url() -> str:
    url = os.environ.get("COSMO_MIGRATION_DATABASE_URL", "")
    f = os.environ.get("COSMO_MIGRATION_DATABASE_URL_FILE")
    if not url and f:
        url = Path(f).read_text(encoding="utf-8").strip()
    if not url:
        raise SystemExit("set COSMO_MIGRATION_DATABASE_URL (an owner credential for the application schema)")
    return url


def _target(url: str) -> str:
    u = urlparse(url)
    return f"{u.hostname}:{u.port or 5432}{u.path}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    a = sub.add_parser("apply"); a.add_argument("--target", type=int); a.add_argument("--dry-run", action="store_true")
    sub.add_parser("verify")
    args = ap.parse_args(argv)
    url = _url()
    try:
        if args.cmd == "status":
            print(json.dumps({"database": _target(url), **migrate.status(url)}, indent=2))
        elif args.cmd == "apply":
            done = migrate.apply(url, args.target, args.dry_run)
            print(json.dumps({"database": _target(url), "dry_run": args.dry_run, "applied": done}))
        else:
            st = migrate.status(url)
            problems = []
            if st["drift"]:
                problems.append(f"drift in {st['drift']}")
            if st["pending"]:
                problems.append(f"pending {st['pending']}")
            if st["unknown_applied"]:
                problems.append(f"unknown applied {st['unknown_applied']}")
            with migrate.connect_owner(url) as c:
                have = {r["table_name"] for r in c.execute("select table_name from information_schema.tables where table_schema='app'").fetchall()}
            missing = migrate.expected_tables() - have
            if missing:
                problems.append(f"missing tables {sorted(missing)}")
            print(json.dumps({"database": _target(url), "ok": not problems, "problems": problems}))
            return 0 if not problems else 1
    except migrate.MigrationError as e:
        print(f"migration error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
