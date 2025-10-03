from fastapi import FastAPI
import requests, time, json, datetime, threading
import os

# -----------------------
# FastAPI app үүсгэх
# -----------------------
app = FastAPI()

# ENV хувьсагч авах
API_KEY = os.getenv("IG_API_KEY")
IDENTIFIER = os.getenv("IG_IDENTIFIER")
PASSWORD = os.getenv("IG_PASSWORD")
ACCOUNT_ID = os.getenv("IG_ACCOUNT_ID")
BASE = os.getenv("IG_BASE", "https://demo-api.ig.com/gateway/deal")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
EPICS = os.getenv("IG_EPICS", "").split(",")
POLL_EVERY_SEC = int(os.getenv("POLL_EVERY_SEC", "2"))

# -----------------------
# IG login функц
# -----------------------
def ig_login():
    url = f"{BASE}/session"
    h = {
        "X-IG-API-KEY": API_KEY,
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json; charset=UTF-8"
    }
    d = {"identifier": IDENTIFIER, "password": PASSWORD}
    r = requests.post(url, headers=h, data=json.dumps(d))
    r.raise_for_status()
    return r.headers["CST"], r.headers["X-SECURITY-TOKEN"]

def ig_set_account(cst, xsec):
    url = f"{BASE}/session"
    h = {
        "X-IG-API-KEY": API_KEY,
        "CST": cst,
        "X-SECURITY-TOKEN": xsec,
        "Content-Type": "application/json; charset=UTF-8"
    }
    d = {"accountId": ACCOUNT_ID, "defaultAccount": True}
    r = requests.put(url, headers=h, data=json.dumps(d))
    r.raise_for_status()

def ig_last_price(cst, xsec, epic):
    url = f"{BASE}/prices"
    params = {"epic": epic, "resolution": "SECOND", "max": 1}
    h = {"X-IG-API-KEY": API_KEY, "CST": cst, "X-SECURITY-TOKEN": xsec}
    r = requests.get(url, params=params, headers=h)
    r.raise_for_status()
    js = r.json()
    snap = js.get("prices")[0]
    ts = snap.get("updateTimeUTC") or datetime.datetime.utcnow().isoformat()+"Z"
    return {
        "epic": epic,
        "bid": snap.get("bid"),
        "ask": snap.get("ask"),
        "ts": ts
    }

# -----------------------
# Webhook руу дамжуулах
# -----------------------
def forward_to_webhook(epic, data):
    try:
        r = requests.post(WEBHOOK_URL, json={"epic": epic, **data}, timeout=10)
        if r.ok:
            print(f"[POST OK] {epic} -> {WEBHOOK_URL}")
        else:
            print(f"[POST FAIL] {epic} {r.status_code} {r.text}")
    except Exception as e:
        print(f"[POST ERROR] {epic} {e}")

# -----------------------
# Runner thread
# -----------------------
def runner():
    if not all([API_KEY, IDENTIFIER, PASSWORD, ACCOUNT_ID, WEBHOOK_URL]):
        print("ENV дутуу байна. IG ба WEBHOOK_URL-ээ Render дээр оруулна уу.")
        return

    cst, xsec = ig_login()
    ig_set_account(cst, xsec)
    last = {}

    while True:
        try:
            for epic in EPICS:
                data = ig_last_price(cst, xsec, epic)
                key = (data["bid"], data["ask"])
                if last.get(epic) != key:
                    last[epic] = key
                    print(f"[POLL] {epic} -> bid={data.get('bid')} ask={data.get('ask')}")
                    forward_to_webhook(epic, {"source": "ig", **data})
            time.sleep(POLL_EVERY_SEC)

        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                time.sleep(1)
                cst, xsec = ig_login()
                ig_set_account(cst, xsec)
            else:
                print("[ERROR] HTTP:", e)
                time.sleep(2)
        except Exception as e:
            print("[ERROR]", e)
            time.sleep(2)

# -----------------------
# Health endpoint
# -----------------------
@app.get("/health")
def health():
    return {"ok": True, "epics": EPICS}

# Background thread эхлүүлэх
threading.Thread(target=runner, daemon=True).start()
