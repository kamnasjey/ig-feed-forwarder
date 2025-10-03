from fastapi import FastAPI
import os, requests, time, json, datetime, threading

app = FastAPI()

# --- ENV ---
API_KEY = os.getenv("IG_API_KEY")
IDENTIFIER = os.getenv("IG_IDENTIFIER")
PASSWORD = os.getenv("IG_PASSWORD")
ACCOUNT_ID = os.getenv("IG_ACCOUNT_ID")
BASE = os.getenv("IG_BASE", "https://demo-api.ig.com/gateway/deal")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
EPICS = [e.strip() for e in os.getenv("IG_EPICS", "").split(",") if e.strip()]
POLL_EVERY_SEC = int(os.getenv("POLL_EVERY_SEC", "2"))

# --- IG login ---
def ig_login():
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

# --- IG account set ---
def ig_set_account(cst, xsec):
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

    # 412: аль хэдийн default account → алгасаад үргэлжлүүлнэ
    if r.status_code == 412:
        try:
            body = r.json()
        except Exception:
            body = {}
        if body.get("errorCode") == "error.switch.accountId-must-be-different":
            print("[SET_ACCOUNT] already default, continue")
            return

    if not r.ok:
        print("[SET_ACCOUNT ERROR]", r.status_code, r.text)
        r.raise_for_status()

# --- IG last price ---
def ig_last_price(cst, xsec, epic):
    # 1) Эхлээд /markets/{epic} ашиглаж snapshot авах (хамгийн найдвартай)
    url1 = f"{BASE}/markets/{epic}"
    h = {
        "X-IG-API-KEY": API_KEY,
        "CST": cst,
        "X-SECURITY-TOKEN": xsec,
        "Accept": "application/json; charset=UTF-8",
        "Version": "3",
    }
    r1 = requests.get(url1, headers=h, timeout=15)
    if r1.status_code == 200:
        js = r1.json() or {}
        snap = js.get("snapshot") or {}
        bid = snap.get("bid")
        ask = snap.get("offer")
        ts = snap.get("updateTimeUTC") or datetime.datetime.utcnow().isoformat()+"Z"
        return {"epic": epic, "bid": bid, "ask": ask, "ts": ts}

    # markets 404/бусад үед fallback → /prices/{epic}?resolution=MINUTE&max=1
    if r1.status_code == 404:
        print(f"[404 markets] {epic} -> {r1.text}")

    url2 = f"{BASE}/prices/{epic}"
    params = {"resolution": "MINUTE", "max": 1}
    h2 = {
        "X-IG-API-KEY": API_KEY,
        "CST": cst,
        "X-SECURITY-TOKEN": xsec,
        "Accept": "application/json; charset=UTF-8",
        "Version": "3",
    }
    r2 = requests.get(url2, params=params, headers=h2, timeout=15)
    if r2.status_code == 404:
        print(f"[404 prices] {epic} -> {r2.text}")
        # 404 үед хоосон snapshot буцаагаад loop-г үргэлжлүүлнэ
        return {"epic": epic, "bid": None, "ask": None, "ts": datetime.datetime.utcnow().isoformat()+"Z"}

    r2.raise_for_status()
    js2 = r2.json() or {}
    prices = js2.get("prices") or []
    if not prices:
        return {"epic": epic, "bid": None, "ask": None, "ts": datetime.datetime.utcnow().isoformat()+"Z"}
    p = prices[0]
    ts = p.get("updateTimeUTC") or datetime.datetime.utcnow().isoformat()+"Z"
    return {"epic": epic, "bid": p.get("bid"), "ask": p.get("ask"), "ts": ts}

# --- rate-limit aware sender ---
_LAST_POST = {}  # epic -> last_ts (ms)

def now_ms():
    return int(time.time() * 1000)

MIN_POST_INTERVAL_MS = int(os.getenv("MIN_POST_INTERVAL_MS", "800"))
POST_RETRY_MAX = int(os.getenv("POST_RETRY_MAX", "3"))

def forward_to_webhook(epic, data):
    # debounce: нэг epic-ийг MIN_POST_INTERVAL_MS дотор ахин бүү явуул
    t = now_ms()
    last = _LAST_POST.get(epic, 0)
    if t - last < MIN_POST_INTERVAL_MS:
        return
    _LAST_POST[epic] = t

    backoff = 1.5
    wait = 1.0
    for attempt in range(1, POST_RETRY_MAX + 1):
        try:
            r = requests.post(WEBHOOK_URL, json={"epic": epic, **data}, timeout=10)
            if r.status_code == 429:
                print(f"[POST 429] {epic} throttled; sleeping {int(wait)}s")
                time.sleep(wait); wait = min(wait * backoff, 30)
                continue
            if r.ok:
                print(f"[POST OK] {epic} -> {WEBHOOK_URL}")
                return
            else:
                print(f"[POST FAIL] {epic} {r.status_code} {r.text}")
                return
        except requests.exceptions.ConnectionError as e:
            # webhook.site sometimes resets the connection → retry with backoff
            print(f"[POST ERROR] {epic} connection error: {e}; retry in {int(wait)}s")
            time.sleep(wait); wait = min(wait * backoff, 30)
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
            if code in (401, 403):
                time.sleep(1)
                cst, xsec = ig_login()
                ig_set_account(cst, xsec)
            elif code == 412:
                print("[WARN] 412 from IG, skipping set_account and continuing")
                time.sleep(0.5)
            else:
                print("[ERROR] HTTP:", code, getattr(e.response, "text", ""))
                time.sleep(2)
        except Exception as e:
            print("[ERROR]", e)
            time.sleep(2)

# --- Endpoints ---
@app.get("/")
def root():
    return {"ok": True}

@app.get("/health")
def health():
    return {"ok": True, "epics": EPICS}

# --- Start runner thread ---
threading.Thread(target=runner, daemon=True).start()
