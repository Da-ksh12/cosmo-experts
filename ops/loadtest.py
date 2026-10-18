"""Load and soak test for a RUNNING backend (rate limiting off: COSMO_RATE_LIMIT=off). Reports throughput, latency percentiles and errors per scenario and
concurrency level, plus backend process memory growth during a soak. Results describe THIS machine and THIS dataset only: they are not a capacity claim for any
other hardware, and a single-instance deployment is the only supported topology.

    python ops/loadtest.py --base http://127.0.0.1:8000 --levels 1,2,4,8 --seconds 15 [--soak-seconds 180] [--pid <backend pid>] --out docs/runs/loadtest.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request


def call(base, method, path, token=None, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    if token:
        h["Authorization"] = "Bearer " + token
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=h), timeout=timeout) as r:
            r.read()
            return r.status, time.perf_counter() - t
    except urllib.error.HTTPError as e:
        e.read()
        return e.code, time.perf_counter() - t
    except Exception:
        return 0, time.perf_counter() - t


def login(base, user="patient1"):
    req = urllib.request.Request(base + "/api/auth/login", data=json.dumps({"user_id": user, "password": "cosmo-dev"}).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["token"]


def scenarios(base, token):
    cid = json.loads(urllib.request.urlopen(urllib.request.Request(base + "/api/conversations", data=b"{}", method="POST",
                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}), timeout=30).read())["id"]
    return {
        "health_live": lambda: call(base, "GET", "/api/v1/health/live"),
        "history_db_read": lambda: call(base, "GET", "/api/history", token),
        "assistant_urgent_redirect": lambda: call(base, "POST", f"/api/conversations/{cid}/messages", token, {"text": "I have chest pain"}),
        "assistant_retrieval_abstain": lambda: call(base, "POST", f"/api/conversations/{cid}/messages", token, {"text": "What does my ferritin result say?"}),
    }


def run_level(fn, workers, seconds):
    lat, codes, lock, stop = [], {}, threading.Lock(), time.time() + seconds

    def worker():
        while time.time() < stop:
            code, dt = fn()
            with lock:
                lat.append(dt); codes[code] = codes.get(code, 0) + 1
    ts = [threading.Thread(target=worker) for _ in range(workers)]
    t0 = time.time()
    [t.start() for t in ts]; [t.join() for t in ts]
    el = time.time() - t0
    lat.sort()
    pct = lambda p: round(lat[min(len(lat) - 1, int(len(lat) * p))] * 1000, 1) if lat else None  # noqa: E731
    ok = sum(n for c, n in codes.items() if 200 <= c < 300)
    return {"workers": workers, "requests": len(lat), "rps": round(len(lat) / el, 1), "p50_ms": pct(.5), "p95_ms": pct(.95), "p99_ms": pct(.99), "max_ms": round(max(lat) * 1000, 1) if lat else None,
            "status_counts": codes, "error_rate": round(1 - ok / max(len(lat), 1), 4)}


def rss_mb(pid):
    try:
        import psutil
        p = psutil.Process(pid)
        return round(sum(c.memory_info().rss for c in [p] + p.children(recursive=True)) / 1e6, 1)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--levels", default="1,2,4,8")
    ap.add_argument("--seconds", type=int, default=15)
    ap.add_argument("--soak-seconds", type=int, default=0)
    ap.add_argument("--soak-workers", type=int, default=4)
    ap.add_argument("--pid", type=int)
    ap.add_argument("--out", default="docs/runs/loadtest.json")
    a = ap.parse_args()
    token = login(a.base)
    sc = scenarios(a.base, token)
    for fn in sc.values():                      # warm-up (model load, caches)
        fn(); fn()
    out = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "base": a.base, "seconds_per_level": a.seconds, "rss_mb_before": rss_mb(a.pid) if a.pid else None, "scenarios": {}}
    for name, fn in sc.items():
        out["scenarios"][name] = [run_level(fn, int(w), a.seconds) for w in a.levels.split(",")]
        print(name, json.dumps(out["scenarios"][name]))
    if a.soak_seconds:
        samples, t_end, mix = [], time.time() + a.soak_seconds, list(sc.values())
        res = {"codes": {}}
        lock = threading.Lock()

        def soak(i):
            n = 0
            while time.time() < t_end:
                code, _ = mix[n % len(mix)](); n += 1
                with lock:
                    res["codes"][code] = res["codes"].get(code, 0) + 1
        ts = [threading.Thread(target=soak, args=(i,)) for i in range(a.soak_workers)]
        [t.start() for t in ts]
        while time.time() < t_end and a.pid:
            samples.append(rss_mb(a.pid)); time.sleep(max(5, a.soak_seconds // 12))
        [t.join() for t in ts]
        total = sum(res["codes"].values())
        ok = sum(n for c, n in res["codes"].items() if 200 <= c < 300)
        out["soak"] = {"seconds": a.soak_seconds, "workers": a.soak_workers, "requests": total, "error_rate": round(1 - ok / max(total, 1), 4), "status_counts": res["codes"],
                       "rss_mb_samples": samples, "rss_mb_growth": round(samples[-1] - samples[0], 1) if len(samples) > 1 and None not in samples else None}
        print("soak", json.dumps(out["soak"]))
    out["rss_mb_after"] = rss_mb(a.pid) if a.pid else None
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
