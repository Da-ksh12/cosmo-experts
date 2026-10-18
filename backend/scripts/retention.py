"""Retention worker (command line).

    python -m scripts.retention plan                                  # what would be removed, per category (counts only); changes nothing
    python -m scripts.retention run                                   # same as plan (dry run is the default)
    python -m scripts.retention run --execute --confirm DELETE        # delete, in bounded batches
    python -m scripts.retention run --execute --confirm DELETE --loop 3600      # repeat every hour (a service manager should supervise this)
    python -m scripts.retention orphans [--delete]                    # local files that no database row references (older than 24 h)

Retention periods come from COSMO_RETENTION_<CATEGORY>_DAYS; categories without one are never touched (see docs/DATA_RETENTION_AND_DELETION.md).
Output contains counts only. Run `plan` first and review it; this tool is never run automatically by the application or by tests against real data."""
from __future__ import annotations

import argparse
import json
import sys
import time

from app import db, retention


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    r = sub.add_parser("run")
    r.add_argument("--execute", action="store_true")
    r.add_argument("--confirm", default="")
    r.add_argument("--batch", type=int, default=100)
    r.add_argument("--max-batches", type=int, default=20)
    r.add_argument("--budget", type=float, default=60.0, help="seconds per run")
    r.add_argument("--loop", type=int, default=0, help="repeat every N seconds")
    o = sub.add_parser("orphans")
    o.add_argument("--delete", action="store_true")
    a = ap.parse_args(argv)
    db.init()
    try:
        if a.cmd == "orphans":
            files = retention.orphan_files()
            n = retention.delete_orphans(files) if a.delete else 0
            print(json.dumps({"orphan_files": len(files), "deleted": n}))
            return 0
        if a.cmd == "run" and a.execute and a.confirm != "DELETE":
            print('refusing: add --confirm DELETE to delete data', file=sys.stderr)
            return 2
        while True:
            rep = retention.run(dry_run=not (a.cmd == "run" and a.execute), batch=getattr(a, "batch", 100), max_batches=getattr(a, "max_batches", 20),
                                time_budget_s=getattr(a, "budget", 60.0))
            print(json.dumps(rep, default=str))
            if not (a.cmd == "run" and a.loop):
                return 0
            time.sleep(a.loop)
    except retention.ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
