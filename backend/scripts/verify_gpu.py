"""Verify an accelerator (CUDA or Apple MPS) before it is trusted for inference.

    python -m scripts.verify_gpu [--out data/gpu_verification.json]

Runs a small fixed-seed network on the CPU and on the accelerator and compares the outputs. Only when an accelerator is present AND the outputs agree within tolerance is a
verification record written (device name, torch version, driver where available, maximum difference, time). Without that record `app.ai.resources.select_device("auto")` keeps
using the CPU, and an explicitly requested GPU is reported as UNVERIFIED. On a machine with no accelerator this script says so and writes nothing: this repository's
development machine has no GPU, so GPU execution has not been verified here."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ATOL = 1e-3


def verify(out_path: Path, device_override: str | None = None) -> dict:
    """`device_override` exists for the unit tests of this logic only (it lets the CPU stand in for an accelerator in a temporary file)."""
    import torch
    from app.ai import resources
    acc = resources._torch_accelerator() if device_override is None else (device_override, "test device")
    if acc is None:
        return {"passed": False, "written": False, "reason": "No CUDA or MPS device is available on this machine; nothing was verified and no record was written."}
    kind, name = acc
    torch.manual_seed(0)
    net = torch.nn.Sequential(torch.nn.Conv2d(3, 8, 3, padding=1), torch.nn.ReLU(), torch.nn.AdaptiveAvgPool2d(1), torch.nn.Flatten(), torch.nn.Linear(8, 4)).eval()
    x = torch.randn(4, 3, 64, 64)
    with torch.no_grad():
        ref = net(x)
        t0 = time.time()
        dev = net.to(device_override or kind)(x.to(device_override or kind)).cpu()
        seconds = time.time() - t0
    diff = float((ref - dev).abs().max())
    passed = diff <= ATOL
    rec = {"passed": passed, "device_kind": kind, "device_name": name, "torch_version": torch.__version__, "max_abs_diff": diff, "atol": ATOL,
           "seconds": round(seconds, 4), "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "note": "A tiny fixed network matched the CPU reference. This does not validate any model; evaluate each model on the device before relying on it."}
    if passed:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return {**rec, "written": passed}


def main(argv=None) -> int:
    from app.ai.resources import GPU_RECORD
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=GPU_RECORD)
    a = ap.parse_args(argv)
    res = verify(a.out)
    print(json.dumps(res, indent=2))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
