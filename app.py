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
    h = {
        **HEADERS,
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json",
        "Version": "2",
    }
    print("[LOGIN] BASE:", BASE)
    print("[LOGIN] IDENTIFIER:", IDENTIFIER)  # password лог руу хэвлэхгүй!

    r = requests.post(url, json=payload, headers=h, timeout=15)

    if not r.ok:
        # IG яг юугаар унаж байгааг бүрэн харуулах
        try:
            print("IG LOGIN ERROR =>", r.status_code, r.text)
        except Exception:
            print("IG LOGIN ERROR (no text) =>", r.status_code)
        r.raise_for_status()

    # Хэвийн тохиолдолд CST/X-SECURITY-TOKEN header ирнэ
    cst = r.headers.get("CST")
    xsec = r.headers.get("X-SECURITY-TOKEN")
    print("[LOGIN] OK, CST/XSEC received:", bool(cst), bool(xsec))
    return cst, xsec

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
        
def forward_to_webhook(epic, data):
    try:
        r = requests.post(WEBHOOK_URL, json={"epic": epic, **data}, timeout=10)
        if r.ok:
            print(f"[POST OK] {epic} -> {WEBHOOK_URL}")
        else:
            print(f"[POST FAIL] {epic} {r.status_code} {r.text}")
    except Exception as e:
        print(f"[POST ERROR] {epic} {e}")

def runner():
    if not all([API_KEY, IDENTIFIER, PASSWORD, ACCOUNT_ID, WEBHOOK_URL]):
        print("ENV дутуу байна. IG_* болон WEBHOOK_URL-ээ Render дээр оруулна уу.")
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
                    # ⇩⇩ LOG + WEBHOOK тутамд
                    print(f"[POLL] {epic} -> bid={data.get('bid')} ask={data.get('ask')}")
                    forward_to_webhook(epic, {"source": "ig", **data})

            time.sleep(POLL_EVERY_SEC)

        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                time.sleep(1)
                cst, xsec = ig_login()
                ig_set_account(cst, xsec)
            else:
                time.sleep(2)
        except Exception:
            time.sleep(2)

def health():
    return {"ok": True, "epics": EPICS}

# Background thread – server асан даруйд poll хийнэ
t = threading.Thread(target=runner, daemon=True)
t.start()
