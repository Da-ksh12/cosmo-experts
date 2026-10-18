"""Start a fine-tuning run through the admin API and stream its real log. Usage: python -m scripts.train_via_api <dataset_version_id> <epochs>"""
import json, sys, time, urllib.request, urllib.error

B = "http://localhost:8000/api"


def call(p, t=None, m="GET", b=None, timeout=60):
    r = urllib.request.Request(B + p, method=m, data=json.dumps(b).encode() if b is not None else None,
                               headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + t} if t else {})})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        return {"_http": e.code, **json.loads(e.read())}


t = call("/auth/login", None, "POST", {"user_id": "admin1", "password": "cosmo-dev"})["token"]
prov = json.load(open(r"F:\Cosmo Experts\backend\data\models\pretrained\resnet18_tv_in1k.provenance.json"))
body = {"task_id": "lesion_classification_research", "dataset_id": "pad-ufes-20", "version_id": sys.argv[1], "architecture": "resnet18", "epochs": int(sys.argv[2]),
        "lr": 5e-4, "batch": 32, "image_size": 224, "finetune": "layer4",
        "weights_path": r"F:\Cosmo Experts\backend\data\models\pretrained\resnet18_tv_in1k.pth",
        "pretrained_note": f"{prov['source']} @ {prov['commit'][:10]} ({','.join(prov['license_tags'])}); {prov['trained_on']}"}
r = call("/admin/train", t, "POST", body)
print("start:", r)
job = r["job_id"]
seen = 0
while True:
    s = call(f"/admin/jobs/{job}", t)
    for line in s.get("log", [])[seen:]:
        print(line, flush=True)
    seen = len(s.get("log", []))
    if s["status"] != "running":
        print("FINAL:", {k: v for k, v in s.items() if k != "log"})
        break
    time.sleep(15)
