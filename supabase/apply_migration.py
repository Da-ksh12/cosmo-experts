"""Apply one migration file to a Supabase project through the Management API (recorded in supabase_migrations).
Usage: SUPABASE_ACCESS_TOKEN=... SUPABASE_PROJECT_REF=... python supabase/apply_migration.py supabase/migrations/0001_core.sql"""
import json, os, sys, urllib.request, urllib.error
tok, ref, path = os.environ["SUPABASE_ACCESS_TOKEN"], os.environ["SUPABASE_PROJECT_REF"], sys.argv[1]
name = os.path.basename(path).rsplit(".", 1)[0].split("_", 1)[1]
req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{ref}/database/migrations", method="POST",
                             data=json.dumps({"name": name, "query": open(path, encoding="utf-8").read()}).encode(),
                             headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        print("applied", path, r.status)
except urllib.error.HTTPError as e:
    print("FAILED", e.code, e.read().decode()[:1500]); sys.exit(1)
