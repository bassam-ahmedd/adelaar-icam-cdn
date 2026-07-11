"""Fetch icam images via ZenRows, resize, save into repo. Resumable.
Modes: (a) ITEMS env = JSON [{src,key},...] -> fetch just those (n8n on-demand)
       (b) default -> fetch manifest.json entries not yet present."""
import json, os, sys, time, io, urllib.parse, subprocess
import concurrent.futures as cf
import requests
from PIL import Image

ZR = os.environ["ZENROWS_KEY"]
WORKERS = int(os.environ.get("WORKERS", "8"))
COMMIT_EVERY = 250
MAX_DIM = 2048
TIME_BUDGET = float(os.environ.get("TIME_BUDGET_H", "5.2")) * 3600
t0 = time.time()

items_env = os.environ.get("ITEMS", "").strip()
if items_env:
    manifest = json.loads(items_env)
    mode = "items"
else:
    manifest = json.load(open("manifest.json"))
    mode = "manifest"
todo = [m for m in manifest if not os.path.exists(m["key"])]
print(f"mode={mode} total={len(manifest)} todo={len(todo)}", flush=True)

def sniff(b):
    if b[:3] == b'\xff\xd8\xff': return "jpg"
    if b[:8] == b'\x89PNG\r\n\x1a\n': return "png"
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP': return "webp"
    return None

def fetch_one(m):
    src, key = m["src"], m["key"]
    enc = urllib.parse.quote(src, safe="")
    for attempt in range(4):
        try:
            r = requests.get(f"https://api.zenrows.com/v1/?apikey={ZR}&url={enc}&js_render=true", timeout=75)
            if r.status_code == 200 and sniff(r.content):
                img = Image.open(io.BytesIO(r.content))
                if img.mode in ("RGBA","P","LA"): img = img.convert("RGB")
                if max(img.size) > MAX_DIM:
                    img.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)
                os.makedirs(os.path.dirname(key), exist_ok=True)
                img.save(key, "JPEG", quality=85, optimize=True)
                return True
        except Exception:
            pass
        time.sleep(1 + attempt)
    return False

def commit_push(n_done):
    subprocess.run(["git","add","-A"], check=False)
    subprocess.run(["git","commit","-q","-m",f"images batch (+{n_done})"], check=False)
    for _ in range(3):
        p = subprocess.run(["git","push","-q","origin","main"], capture_output=True)
        if p.returncode == 0: return
        subprocess.run(["git","pull","-q","--rebase","origin","main"], check=False)
    print("PUSH FAILED", flush=True)

done = fail = 0
batch_ct = 0
with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = {ex.submit(fetch_one, m): m for m in todo}
    for f in cf.as_completed(futs):
        ok = f.result()
        done += ok; fail += (not ok); batch_ct += ok
        if batch_ct >= COMMIT_EVERY:
            commit_push(batch_ct); batch_ct = 0
            print(f"progress: {done} done, {fail} failed, {time.time()-t0:.0f}s", flush=True)
        if time.time() - t0 > TIME_BUDGET:
            print("time budget reached, stopping for redispatch", flush=True)
            for fu in futs: fu.cancel()
            break
commit_push(batch_ct)
remaining = 0 if mode == "items" else len([m for m in manifest if not os.path.exists(m["key"])])
print(f"RUN SUMMARY: mode={mode} done={done} failed={fail} remaining={remaining}", flush=True)
with open("remaining.txt","w") as f: f.write(str(remaining))
