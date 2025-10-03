import os, time, requests, threading, datetime
from fastapi import FastAPI

# ======= ENV =======
BASE = os.getenv("IG_BASE", "https://demo-api.ig.com/gateway/deal")
API_KEY = os.getenv("IG_API_KEY")
IDENTIFIER = os.getenv("IG_IDENTIFIER")
PASSWORD = os.getenv("IG_PASSWORD")
ACCOUNT_ID = os.getenv("IG_ACCOUNT_ID")
EPICS = [e.strip() for e in os.getenv("IG_EPICS","CS.D.EURUSD.MINI.IP").split(",")]
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # ж: https://your-receiver/forex
POLL_EVERY_SEC = int(os.getenv("POLL_EVERY_SEC", "2"))

HEADERS = {"X-IG-API-KEY": API_KEY, "Accept": "application/json"}

def ig_login():
    url = f"{BASE}/session"
    payload = {"identifier": IDENTIFIER, "password": PASSWORD}
    h = {**HEADERS, "Content-Type": "application/json", "Version": "2"}
    r = requests.post(url, json=payload, headers=h, timeout=15)
    r.raise_for_status()
    return r.headers["CST"], r.headers["X-SECURITY-TOKEN"]

def ig_set_account(cst, xsec):
    url = f"{BASE}/session"
    h = {**HEADERS, "Content-Type": "application/json", "Version": "1",
         "CST": cst, "X-SECURITY-TOKEN": xsec}
    try:
        requests.put(url, json={"accountId": ACCOUNT_ID, "defaultAccount": True}, headers=h, timeout=10)
    except Exception:
        pass

def ig_last_price(cst, xsec, epic):
    url = f"{BASE}/prices"
    params = {"epic": epic, "resolution":"SECOND", "max":1}
    h = {**HEADERS, "Version":"3", "CST": cst, "X-SECURITY-TOKEN": xsec}
    r = requests.get(url, params=params, headers=h, timeout=10)
    r.raise_for_status()
    js = r.json()
    snap = js.get("snapshot") or {}
    ts = snap.get("updateTimeUTC") or datetime.datetime.utcnow().isoformat()+"Z"
    return {"epic": epic, "bid": snap.get("bid"), "ask": snap.get("offer"), "ts": ts}

def post_webhook(payload):
    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=10)
    except Exception:
        pass

def runner():
    if not all([API_KEY, IDENTIFIER, PASSWORD, ACCOUNT_ID, WEBHOOK_URL]):
        print("ENV дутаж байна. IG_* болон WEBHOOK_URL-ээ Render дээр оруулна уу.")
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
                    post_webhook({"source":"ig", **data})
            time.sleep(POLL_EVERY_SEC)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401,403):
                time.sleep(1)
                cst, xsec = ig_login()
                ig_set_account(cst, xsec)
            else:
                time.sleep(2)
        except Exception:
            time.sleep(2)

app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True, "epics": EPICS}

# Background thread – server асан даруйд poll хийнэ
t = threading.Thread(target=runner, daemon=True)
t.start()
