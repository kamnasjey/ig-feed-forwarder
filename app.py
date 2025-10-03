from fastapi import FastAPI
import os, requests, time, json, datetime, threading

app = FastAPI()

# --- ENV ---
API_KEY = os.getenv("IG_API_KEY")
IDENTIFIER = os.getenv("IG_IDENTIFIER")          # IG Web API demo username
PASSWORD = os.getenv("IG_PASSWORD")              # IG Web API demo password
ACCOUNT_ID = os.getenv("IG_ACCOUNT_ID")          # Demo account ID
BASE = os.getenv("IG_BASE", "https://demo-api.ig.com/gateway/deal")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
EPICS = [e for e in os.getenv("IG_EPICS", "").split(",") if e.strip()]
POLL_EVERY_SEC = int(os.getenv("POLL_EVERY_SEC", "2"))

# --- IG helpers ---
def ig_login():
    """POST /session — login (Version: 2)"""
    url = f"{BASE}/session"
    h = {
        "X-IG-API-KEY": API_KEY,
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json; charset=UTF-8",
        "Version": "2",
    }
    d = {"identifier": IDENTIFIER, "password": PASSWORD}
    r = requests.post(url, headers=h, json=d, timeout=15)
    if not r.ok:
        print("IG LOGIN ERROR =>", r.status_code, r.text)
        r.raise_for_status()
    cst = r.headers.get("CST")
    xsec = r.headers.get("X-SECURITY-TOKEN")
    print("[LOGIN] OK, CST/XSEC received:", bool(cst), bool(xsec))
    return cst, xsec

def ig_set_account(cst, xsec):
    """PUT /session — set default account (Version: 1)"""
    url = f"{BASE}/session"
    h = {
        "X-IG-API-KEY": API_KEY,
        "CST": cst,
        "X-SECURITY-TOKEN": xsec,
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json; charset=UTF-8",
        "Version": "1",
    }
    d = {"accountId": ACCOUNT_ID, "defaultAccount": True}
    r = requests.put(url, headers=h, json=d, timeout=15)
    if not r.ok:
        print("[SET_ACCOUNT ERROR]", r.status_code, r.text)
        r.raise_for_status()

def ig_last_price(cst, xsec, epic):
    """GET /prices?epic=...&resolution=SECOND&max=1 — snapshot"""
    url = f"{BASE}/prices"
    params = {"epic": epic, "resolution": "SECOND", "max": 1}
    h = {"X-IG-API-KEY": API_KEY, "CST": cst, "X-SECURITY-TOKEN": xsec, "Version": "3"}
    r = requests.get(url, params=params, headers=h, timeout=15)
    r.raise_for_status()
    js = r.json()
    prices = js.get("prices") or []
    if not prices:
        return {"epic": epic, "bid": None, "ask": None, "ts": datetime.datetime.utcnow().isoformat() + "Z"}
    snap = prices[0]
    ts = snap.get("updateTimeUTC") or datetime.datetime.utcnow().isoformat() + "Z"
    return {"epic": epic, "bid": snap.get("bid"), "ask": snap.get("ask"), "ts": ts}

# --- Webhook ---
def forward_to_webhook(epic, data):
    try:
        r = requests.post(WEBHOOK_URL, json={"epic": epic, **data}, timeout=10)
        if r.ok:
            print(f"[POST OK] {epic} -> {WEBHOOK_URL}")
        else:
            print(f"[POST FAIL] {epic} {r.status_code} {r.text}")
    except Exception as e:
        print(f"[POST ERROR] {epic} {e}")

# --- Background runner ---
def runner():
    if not all([API_KEY, IDENTIFIER, PASSWORD, ACCOUNT_ID, WEBHOOK_URL]) or not EPICS:
        print("ENV дутуу байна. IG_* болон WEBHOOK_URL/IG_EPICS-ээ Render дээр шалгаарай.")
        return

    cst, xsec = ig_login()
    ig_set_account(cst, xsec)

    last = {}
    while True:
        try:
            for epic in EPICS:
                data = ig_last_price(cst, xsec, epic)
                key = (data.get("bid"), data.get("ask"))
                if last.get(epic) != key:
                    last[epic] = key
                    print(f"[POLL] {epic} -> bid={data.get('bid')} ask={data.get('ask')}")
                    forward_to_webhook(epic, {"source": "ig", **data})
            time.sleep(POLL_EVERY_SEC)

        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if code in (401, 403, 412):  # refresh tokens / set account again
                time.sleep(1)
                cst, xsec = ig_login()
                ig_set_account(cst, xsec)
            else:
                print("[ERROR] HTTP:", code, getattr(e.response, "text", ""))
                time.sleep(2)
        except Exception as e:
            print("[ERROR]", e)
            time.sleep(2)

# health + root
@app.get("/")
def root():
    return {"ok": True}

@app.get("/health")
def health():
    return {"ok": True, "epics": EPICS}

# start bg thread
threading.Thread(target=runner, daemon=True).start()
