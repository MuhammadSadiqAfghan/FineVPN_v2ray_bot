# -*- coding: utf-8 -*-
"""FineVPN unified backend: Telegram Mini App + bot + SQLite + panel connectors."""
from __future__ import annotations
import os, json, hmac, hashlib, time, secrets, sqlite3, base64, logging, asyncio
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Optional, Any
from urllib.parse import parse_qsl, urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "finevpn.db"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "FineVPN_v2ray_bot").strip().lstrip("@")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "6891242026").replace(";", ",").split(",") if x.strip().isdigit()}
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "sadiq_AFG1").lstrip("@")
ALLOW_DEV_AUTH = os.getenv("ALLOW_DEV_AUTH", "0").lower() in {"1", "true", "yes"}
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-before-production").encode()
AUTH_MAX_AGE = int(os.getenv("AUTH_MAX_AGE", "86400"))
# The only provisioning backend: the user's own FineVPN VLESS panel.
PANEL_BASE_URL = os.getenv("PANEL_BASE_URL", "http://vpn-panel:8000").strip().rstrip("/")
PANEL_ADMIN_USERNAME = os.getenv("VPN_PANEL_ADMIN_USERNAME", "").strip()
PANEL_ADMIN_PASSWORD = os.getenv("VPN_PANEL_ADMIN_PASSWORD", "")
PANEL_PUBLIC_URL = os.getenv("PANEL_PUBLIC_URL", "").strip().rstrip("/")
PANEL_CONFIG_PORT = int(os.getenv("PANEL_CONFIG_PORT", "443"))
PANEL_SERVER_ADDRESS = os.getenv("PANEL_SERVER_ADDRESS", "").strip()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("finevpn")
app = FastAPI(title="FineVPN API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",")], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(
 id INTEGER PRIMARY KEY, first_name TEXT NOT NULL DEFAULT '', last_name TEXT NOT NULL DEFAULT '', username TEXT NOT NULL DEFAULT '',
 language TEXT NOT NULL DEFAULT 'fa', balance INTEGER NOT NULL DEFAULT 0, points INTEGER NOT NULL DEFAULT 0,
 is_blocked INTEGER NOT NULL DEFAULT 0, ref_by INTEGER, test_used INTEGER NOT NULL DEFAULT 0, last_spin INTEGER DEFAULT 0,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS panels(
 id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, name TEXT NOT NULL, base_url TEXT NOT NULL,
 encrypted_config TEXT NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'unconfigured',
 last_check INTEGER DEFAULT 0, last_error TEXT DEFAULT '', created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS products(
 id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT NOT NULL, volume_gb INTEGER NOT NULL,
 days INTEGER NOT NULL, price INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS orders(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL, panel_id INTEGER,
 volume_gb INTEGER NOT NULL, days INTEGER NOT NULL, ip_limit INTEGER NOT NULL DEFAULT 1, protocol TEXT NOT NULL DEFAULT 'auto', username TEXT NOT NULL,
 price INTEGER NOT NULL, discount_percent INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending_payment', output_url TEXT DEFAULT '', config_url TEXT DEFAULT '', external_id TEXT DEFAULT '',
 error TEXT DEFAULT '', created_at INTEGER NOT NULL, paid_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS services(
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, user_id INTEGER NOT NULL, panel_id INTEGER NOT NULL,
 provider TEXT NOT NULL, external_id TEXT DEFAULT '', username TEXT NOT NULL, subscription_url TEXT NOT NULL, config_url TEXT DEFAULT '', public_url TEXT DEFAULT '',
 volume_gb INTEGER NOT NULL, days INTEGER NOT NULL, ip_limit INTEGER NOT NULL DEFAULT 1, used_bytes INTEGER NOT NULL DEFAULT 0,
 expires_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active', created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL, amount INTEGER NOT NULL,
 currency TEXT NOT NULL DEFAULT 'IRR', txid TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'completed', note TEXT DEFAULT '', created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, subject TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket_messages(
 id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL, sender_id INTEGER NOT NULL, sender_role TEXT NOT NULL,
 body TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS redeem_codes(
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, value INTEGER NOT NULL,
 max_uses INTEGER NOT NULL DEFAULT 1, uses INTEGER NOT NULL DEFAULT 0, expires_at INTEGER DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS redemptions(
 id INTEGER PRIMARY KEY AUTOINCREMENT, code_id INTEGER NOT NULL, user_id INTEGER NOT NULL, created_at INTEGER NOT NULL,
 UNIQUE(code_id,user_id)
);
CREATE TABLE IF NOT EXISTS user_discounts(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, code_id INTEGER NOT NULL,
 percent INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
 is_read INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_logs(
 id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL, action TEXT NOT NULL, details TEXT DEFAULT '', created_at INTEGER NOT NULL
);
"""

DEFAULT_SETTINGS = {
 "config_price_per_gb": "2000", "panel_price_per_gb": "1500", "currency_label": "تومان",
 "points_per_referral": "2", "points_per_spin_min": "1", "points_per_spin_max": "20", "points_per_gb": "10",
 "points_config_gb": "1", "points_config_days": "7", "welcome_points": "0",
 "crypto_usdt": "", "crypto_ton": "", "crypto_trx": "", "crypto_btc": "",
 "support_username": SUPPORT_USERNAME, "maintenance": "0", "min_topup": "5",
 "referral_enabled": "1", "wheel_enabled": "1", "test_enabled": "1", "test_gb": "1", "test_days": "7",
 "app_notice": "به FineVPN خوش آمدید. سرویس خود را با چند لمس مدیریت کنید."
}
MAIN_PANEL_PROVIDER = "finevpn_core"
MAIN_PANEL_NAME = "FineVPN Main Panel"

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def now() -> int: return int(time.time())
def rowdict(row): return dict(row) if row else None

def init_db():
    with db() as c:
        c.executescript(SCHEMA)
        # FineVPN interface is Persian-only. Normalize all existing accounts.
        c.execute("UPDATE users SET language='fa' WHERE language<>'fa'")
        for k,v in DEFAULT_SETTINGS.items(): c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k,v))
        # Safe schema migrations for existing FineVPN installations.
        for table, column, definition in (
            ("orders", "config_url", "TEXT DEFAULT ''"),
            ("orders", "ip_limit", "INTEGER NOT NULL DEFAULT 1"),
            ("services", "config_url", "TEXT DEFAULT ''"),
            ("services", "public_url", "TEXT DEFAULT ''"),
            ("services", "ip_limit", "INTEGER NOT NULL DEFAULT 1"),
        ):
            cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        if c.execute("SELECT COUNT(*) FROM products WHERE kind='config'").fetchone()[0] == 0:
            for title,gb,days in [("Starter 30GB",30,30),("Pro 60GB",60,30),("Ultra 100GB",100,30)]:
                c.execute("INSERT INTO products(kind,title,volume_gb,days,price) VALUES('config',?,?,?,?)", (title,gb,days,gb*int(DEFAULT_SETTINGS['config_price_per_gb'])))
        # Trial terms are fixed by the owner: exactly 1 GB for 7 days.
        c.execute("UPDATE settings SET value='1' WHERE key='test_gb'")
        c.execute("UPDATE settings SET value='7' WHERE key='test_days'")
        # Hide legacy panel-resale products: only customer VLESS configs are sold.
        c.execute("UPDATE products SET enabled=0 WHERE kind='panel'")
        # Disable legacy multi-provider connectors. Only the user's supplied panel is used.
        c.execute("UPDATE panels SET is_active=0 WHERE provider<>?", (MAIN_PANEL_PROVIDER,))
        main = c.execute("SELECT id FROM panels WHERE provider=? ORDER BY id LIMIT 1", (MAIN_PANEL_PROVIDER,)).fetchone()
        if main:
            c.execute("UPDATE panels SET name=?,base_url=?,is_active=1 WHERE id=?", (MAIN_PANEL_NAME, PANEL_BASE_URL, main[0]))
        else:
            c.execute("INSERT INTO panels(provider,name,base_url,encrypted_config,is_active,status,created_at) VALUES(?,?,?,'',1,'unconfigured',?)",
                      (MAIN_PANEL_PROVIDER, MAIN_PANEL_NAME, PANEL_BASE_URL, now()))

def setting(key: str, default: str="") -> str:
    with db() as c:
        r=c.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
        return r[0] if r else default

def set_setting(key: str, value: str):
    with db() as c: c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))

def validate_init_data(raw: str) -> dict:
    if not BOT_TOKEN: raise HTTPException(503, "BOT_TOKEN در سرور تنظیم نشده است")
    try:
        pairs = dict(parse_qsl(raw, keep_blank_values=True))
        received_hash = pairs.pop("hash", "")
        if not received_hash: raise ValueError("hash missing")
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        # Accept the standard bot HMAC variant and the older data shape without the
        # optional third-party Ed25519 signature field.
        candidates = [pairs]
        if "signature" in pairs:
            candidates.append({k:v for k,v in pairs.items() if k!="signature"})
        valid = False
        for candidate in candidates:
            check = "\n".join(f"{k}={v}" for k,v in sorted(candidate.items()))
            calculated = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
            if hmac.compare_digest(calculated, received_hash): valid = True; break
        if not valid: raise ValueError("invalid signature")
        auth_date = int(pairs.get("auth_date", "0"))
        if not auth_date or now()-auth_date > AUTH_MAX_AGE or auth_date > now()+60: raise ValueError("expired auth data")
        user = json.loads(pairs.get("user", "{}"))
        if not user.get("id"): raise ValueError("user missing")
        user["_start_param"] = pairs.get("start_param", "")
        return user
    except HTTPException: raise
    except Exception as e:
        log.warning("Telegram initData validation failed: %s", e)
        raise HTTPException(401, "اعتبار ورود تلگرام تأیید نشد")

async def current_user(x_telegram_init_data: str = Header(default="")) -> dict:
    if x_telegram_init_data:
        u = validate_init_data(x_telegram_init_data)
    elif ALLOW_DEV_AUTH:
        u = {"id": next(iter(ADMIN_IDS), 6891242026), "first_name":"FineVPN Demo", "last_name":"", "username":"", "language_code":"fa"}
    else:
        raise HTTPException(401, "لطفاً مینی‌اپ را از داخل تلگرام باز کنید")
    uid=int(u["id"])
    if uid in ADMIN_IDS: pass
    with db() as c:
        existing=c.execute("SELECT id FROM users WHERE id=?",(uid,)).fetchone()
        ref_by=None
        start_param=str(u.get("_start_param", ""))
        if not existing and start_param.startswith("ref_"):
            try: candidate=int(start_param[4:])
            except ValueError: candidate=0
            if candidate and candidate!=uid and c.execute("SELECT 1 FROM users WHERE id=?",(candidate,)).fetchone():
                ref_by=candidate
        if not existing:
            c.execute("INSERT INTO users(id,first_name,last_name,username,language,ref_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
              (uid,u.get("first_name", ""),u.get("last_name", ""),u.get("username", ""),"fa",ref_by,now(),now()))
            if ref_by:
                reward=int(setting("points_per_referral","2"))
                c.execute("UPDATE users SET points=points+? WHERE id=?",(reward,ref_by))
                c.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(ref_by,"دعوت موفق",f"یک کاربر با لینک شما وارد شد؛ {reward} امتیاز دریافت کردید.",now()))
        else:
            c.execute("UPDATE users SET first_name=?,last_name=?,username=?,language='fa',updated_at=? WHERE id=?",
              (u.get("first_name", ""),u.get("last_name", ""),u.get("username", ""),now(),uid))
        r=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
        user=rowdict(r)
    if user["is_blocked"] and uid not in ADMIN_IDS: raise HTTPException(403,"حساب کاربری شما مسدود شده است")
    user["is_admin"] = uid in ADMIN_IDS
    return user

def require_admin(user=Depends(current_user)):
    if not user.get("is_admin"): raise HTTPException(403,"دسترسی مدیر لازم است")
    return user

async def telegram_send(chat_id: int, text: str):
    if not BOT_TOKEN: return False
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r=await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":chat_id,"text":text,"disable_web_page_preview":True})
            return r.is_success
    except Exception as e:
        log.warning("Telegram notification failed for %s: %s",chat_id,e)
        return False

def audit(admin_id: int, action: str, details: str=""):
    with db() as c: c.execute("INSERT INTO audit_logs(admin_id,action,details,created_at) VALUES(?,?,?,?)",(admin_id,action,details[:1500],now()))

def encrypt_config(data: dict) -> str:
    raw=json.dumps(data,ensure_ascii=False).encode()
    try:
        from cryptography.fernet import Fernet
        key=base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY).digest())
        return Fernet(key).encrypt(raw).decode()
    except Exception as e:
        raise HTTPException(500,"رمزنگاری امن تنظیمات پنل در دسترس نیست؛ cryptography را نصب کنید")

def decrypt_config(value: str) -> dict:
    if not value: return {}
    from cryptography.fernet import Fernet
    key=base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY).digest())
    try: return json.loads(Fernet(key).decrypt(value.encode()).decode())
    except Exception: raise RuntimeError("کلید SECRET_KEY تغییر کرده یا تنظیمات پنل خراب است")

async def main_panel_client() -> httpx.AsyncClient:
    """Create an authenticated session with the user's supplied FineVPN VLESS panel."""
    if not PANEL_ADMIN_USERNAME or not PANEL_ADMIN_PASSWORD:
        raise RuntimeError("اطلاعات ورود پنل اصلی در .env تنظیم نشده است")
    client = httpx.AsyncClient(timeout=20, follow_redirects=True, verify=True)
    try:
        login = await client.post(
            PANEL_BASE_URL + "/api/login",
            json={"username": PANEL_ADMIN_USERNAME, "password": PANEL_ADMIN_PASSWORD},
        )
        login.raise_for_status()
        if not login.json().get("ok"):
            raise RuntimeError("ورود به پنل اصلی تأیید نشد")
        return client
    except Exception:
        await client.aclose()
        raise

async def probe_main_panel() -> dict:
    """Verify readiness, admin login, state persistence and management API."""
    async with httpx.AsyncClient(timeout=12, follow_redirects=True, verify=True) as guest:
        health = await guest.get(PANEL_BASE_URL + "/health")
        health.raise_for_status()
        hdata = health.json()
        if not hdata.get("ready"):
            raise RuntimeError("پنل اصلی آماده نیست یا فضای ذخیره‌سازی قابل نوشتن نیست")
    client = await main_panel_client()
    try:
        system = await client.get(PANEL_BASE_URL + "/api/system")
        system.raise_for_status()
        data = system.json()
        expected_host = PANEL_PUBLIC_URL.replace("https://", "").replace("http://", "").split("/", 1)[0]
        actual_host = str(data.get("public_host", "")).lower()
        if expected_host and actual_host and actual_host != expected_host.lower():
            raise RuntimeError(f"دامنهٔ عمومی پنل اشتباه است: {actual_host}؛ باید {expected_host} باشد")
        return {"version": data.get("version", "?"), "public_host": actual_host,
                "links_count": int(data.get("links_count", 0)),
                "active_links": int(data.get("active_links", 0)),
                "features": data.get("features", [])}
    finally:
        await client.aclose()

async def create_remote_client(panel: dict, username: str, gb: int, days: int, ip_limit: int, telegram_id: int) -> dict:
    """Create one subscription group and its full 24-config compatibility matrix in the core panel."""
    del panel  # This deployment intentionally has one fixed core panel.
    clean_name = " ".join(str(username or "").split())[:60] or f"FineVPN-{telegram_id}"
    payload = {
        "name": clean_name,
        "desc": f"FineVPN Telegram ID {telegram_id}",
        "total_limit_bytes": int(gb) * (1024 ** 3),
        "days": int(days),
        "ip_limit": int(ip_limit),
        "port": PANEL_CONFIG_PORT,
    }
    if PANEL_SERVER_ADDRESS:
        payload["server_address"] = PANEL_SERVER_ADDRESS

    client = await main_panel_client()
    sub_id = ""
    try:
        response = await client.post(PANEL_BASE_URL + "/api/subs/quick", json=payload)
        response.raise_for_status()
        data = response.json()
        sub_id = str(data.get("sub_id", "")).strip()
        sub_url = str(data.get("sub_url", "")).strip()
        public_url = str(data.get("public_url", "")).strip()
        if not sub_id or not sub_url.startswith(("https://", "http://")) or not public_url.startswith(("https://", "http://")):
            raise RuntimeError("پنل اصلی گروه اشتراک معتبر برنگرداند")
        if PANEL_PUBLIC_URL and (not sub_url.startswith(PANEL_PUBLIC_URL) or not public_url.startswith(PANEL_PUBLIC_URL)):
            raise RuntimeError("دامنهٔ لینک اشتراک با PANEL_PUBLIC_URL یکسان نیست")

        # Retrieve the first direct VLESS variant from the group's public status endpoint.
        key = urlsplit(sub_url).path.rstrip("/").split("/")[-1]
        public_client = httpx.AsyncClient(timeout=15, follow_redirects=True, verify=True)
        try:
            public_response = await public_client.get(PANEL_BASE_URL + "/api/public/sub/" + key)
            public_response.raise_for_status()
            public_data = public_response.json()
        finally:
            await public_client.aclose()
        links = public_data.get("links") or []
        config_url = next((str(x.get("vless_link", "")) for x in links if str(x.get("vless_link", "")).startswith("vless://")), "")
        if not config_url:
            raise RuntimeError("گروه ساخته شد اما پنل لینک مستقیم VLESS معتبر برنگرداند")
        return {
            "external_id": sub_id,
            "url": sub_url,
            "subscription_url": sub_url,
            "config_url": config_url,
            "public_url": public_url,
            "links_count": int(data.get("count", len(data.get("links", []))) or len(links) or 24),
            "ip_limit": int(ip_limit),
        }
    except Exception:
        if sub_id:
            try:
                cleanup = await client.delete(PANEL_BASE_URL + "/api/subs/" + sub_id)
                cleanup.raise_for_status()
            except Exception:
                log.exception("Could not remove incomplete subscription group %s", sub_id)
        raise
    finally:
        await client.aclose()


def normalize_group_stats(data: dict) -> dict:
    """Normalize the core panel's group record for the Mini App and bot."""
    data = dict(data or {})
    total = max(0, int(data.get("total_limit_bytes", 0) or 0))
    used = max(0, int(data.get("total_used_bytes", 0) or 0))
    data["remaining_bytes"] = max(0, total - used) if total else None
    data["quota_pct"] = min(100.0, used * 100.0 / total) if total else 0
    data["total_upload_bytes"] = max(0, int(data.get("total_upload_bytes", 0) or 0))
    data["total_download_bytes"] = max(0, int(data.get("total_download_bytes", 0) or 0))
    return data


async def get_remote_group_stats(sub_id: str, client: httpx.AsyncClient | None = None) -> dict:
    """Read current quota, traffic and child-config counts from the core panel."""
    owned = client is None
    panel_client = client or await main_panel_client()
    try:
        response = await panel_client.get(PANEL_BASE_URL + "/api/subs/" + str(sub_id))
        response.raise_for_status()
        return normalize_group_stats(response.json())
    finally:
        if owned:
            await panel_client.aclose()


def service_with_stats(row: dict, stats: dict | None = None) -> dict:
    item = dict(row)
    if stats:
        item.update({
            "used_bytes": max(0, int(stats.get("total_used_bytes", 0) or 0)),
            "total_limit_bytes": max(0, int(stats.get("total_limit_bytes", 0) or 0)),
            "remaining_bytes": stats.get("remaining_bytes"),
            "quota_pct": float(stats.get("quota_pct", 0) or 0),
            "upload_bytes": int(stats.get("total_upload_bytes", 0) or 0),
            "download_bytes": int(stats.get("total_download_bytes", 0) or 0),
            "links_count": int(stats.get("links_count", 0) or 0),
            "active_configs": int(stats.get("active_count", 0) or 0),
            "expires_at_panel": stats.get("expires_at"),
            "public_url": stats.get("public_url") or item.get("public_url", ""),
            "subscription_url": stats.get("sub_url") or item.get("subscription_url", ""),
        })
    else:
        item.setdefault("total_limit_bytes", int(item.get("volume_gb", 0) or 0) * (1024 ** 3))
        item.setdefault("remaining_bytes", max(0, item["total_limit_bytes"] - int(item.get("used_bytes", 0) or 0)))
        item.setdefault("quota_pct", 0)
        item.setdefault("links_count", 24)
        item.setdefault("active_configs", 0)
    return item

class OrderIn(BaseModel):
    kind: str = "config"
    panel_id: int = 1
    volume_gb: int = Field(ge=1, le=10000)
    days: int = Field(ge=1, le=3650)
    ip_limit: int = Field(default=1, ge=1, le=100)
    protocol: str = "auto"
    username: str = Field(min_length=1,max_length=60)
class TopupIn(BaseModel): currency: str; amount: float = Field(gt=0); txid: str = Field(min_length=8,max_length=180)
class RedeemIn(BaseModel): code: str
class TicketIn(BaseModel): subject: str = Field(min_length=3,max_length=120); body: str = Field(min_length=2,max_length=3000)
class ReplyIn(BaseModel): body: str = Field(min_length=1,max_length=3000)
class PanelIn(BaseModel): provider: str; name: str; base_url: str; config: dict = {}
class SettingIn(BaseModel): key: str; value: str
class CodeIn(BaseModel): code: str; kind: str; value: int = Field(gt=0); max_uses: int = Field(default=1,ge=1); expires_at: int = 0
class UserAdjustIn(BaseModel): balance_delta: int = 0; points_delta: int = 0; blocked: Optional[bool] = None

@app.on_event("startup")
async def startup(): init_db()

@app.get("/")
async def index(): return FileResponse(os.path.join(BASE_DIR,"index.html"))
@app.get("/health")
async def health(): return {"ok":True,"service":"FineVPN","version":"2.0.0","time":now()}

@app.get("/api/bootstrap")
async def bootstrap(user=Depends(current_user)):
    with db() as c:
        panels=[dict(id=r["id"],provider="FineVPN",name=MAIN_PANEL_NAME,status=r["status"],is_active=True,last_check=r["last_check"],last_error=r["last_error"]) for r in c.execute("SELECT * FROM panels WHERE provider=? AND is_active=1 LIMIT 1",(MAIN_PANEL_PROVIDER,)).fetchall()]
        products=[rowdict(r) for r in c.execute("SELECT * FROM products WHERE enabled=1 ORDER BY price").fetchall()]
        services=[rowdict(r) for r in c.execute("SELECT * FROM services WHERE user_id=? ORDER BY id DESC LIMIT 10",(user["id"],)).fetchall()]
        orders=[rowdict(r) for r in c.execute("SELECT id,kind,volume_gb,days,price,status,created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 8",(user["id"],)).fetchall()]
        unread=c.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",(user["id"],)).fetchone()[0]
    services=await _enrich_services(services)
    features={"wallet":True,"crypto":True,"wheel":setting("wheel_enabled","1")=="1","referral":setting("referral_enabled","1")=="1","test":setting("test_enabled","1")=="1","support":True,"admin":bool(user["is_admin"])}
    return {"user":user,"panels":panels,"products":products,"services":services,"orders":orders,"unread":unread,"features":features,"main_panel_status":(panels[0]["status"] if panels else "unconfigured"),
       "prices":{"config_per_gb":int(setting("config_price_per_gb","2000")),"panel_per_gb":int(setting("panel_price_per_gb","1500")),"currency":setting("currency_label","تومان")},
       "crypto_addresses":{"USDT":setting("crypto_usdt"),"TON":setting("crypto_ton"),"TRX":setting("crypto_trx"),"BTC":setting("crypto_btc")},
       "notice":setting("app_notice"),"support":"@"+setting("support_username",SUPPORT_USERNAME),"bot_username":BOT_USERNAME}

@app.get("/api/me")
async def me(user=Depends(current_user)):
    return {k:user[k] for k in ("id","first_name","last_name","language","balance","points","created_at","is_admin")}

@app.post("/api/profile/language")
async def profile_language(body:dict,user=Depends(current_user)):
    # The Mini App is intentionally Persian-only.
    with db() as c: c.execute("UPDATE users SET language='fa' WHERE id=?",(user["id"],))
    return {"ok":True,"language":"fa"}

@app.get("/api/crypto-rates")
async def crypto_rates():
    try:
        async with httpx.AsyncClient(timeout=7) as client:
            r=await client.get("https://api.coingecko.com/api/v3/simple/price",params={"ids":"bitcoin,toncoin,tron,tether","vs_currencies":"usd"})
            r.raise_for_status(); data=r.json()
        return {"BTC":data.get("bitcoin",{}).get("usd"),"TON":data.get("toncoin",{}).get("usd"),"TRX":data.get("tron",{}).get("usd"),"USDT":data.get("tether",{}).get("usd"),"source":"CoinGecko"}
    except Exception:
        raise HTTPException(503,"قیمت زنده در حال حاضر در دسترس نیست")

async def _enrich_services(rows: list[dict]) -> list[dict]:
    """Refresh many service cards with one authenticated panel request, not N requests."""
    if not rows:
        return []
    client = None
    group_stats = {}
    panel_error = ""
    try:
        client = await main_panel_client()
        response = await client.get(PANEL_BASE_URL + "/api/subs")
        response.raise_for_status()
        payload = response.json()
        group_stats = {
            str(item.get("sub_id")): normalize_group_stats(item)
            for item in payload.get("subs", [])
            if item.get("sub_id")
        }
    except Exception as exc:
        panel_error = str(exc)[:180]
        log.warning("Could not refresh subscription stats: %s", panel_error)
    result = []
    try:
        for row in rows:
            stats = group_stats.get(str(row.get("external_id", "")))
            if stats:
                with db() as c:
                    c.execute("UPDATE services SET used_bytes=? WHERE id=?", (int(stats.get("total_used_bytes", 0) or 0), row["id"]))
                # Status follows the subscription group from the panel where possible.
                exp = stats.get("expires_at")
                if exp:
                    try:
                        exp_ts = int(datetime.fromisoformat(str(exp).replace("Z", "+00:00")).timestamp())
                        if exp_ts <= now() and row.get("status") == "active":
                            row["status"] = "expired"
                    except (TypeError, ValueError, OverflowError):
                        pass
            elif panel_error:
                row["panel_sync_error"] = panel_error
            else:
                row["panel_sync_error"] = "گروه اشتراک در پنل اصلی پیدا نشد"
            result.append(service_with_stats(row, stats))
    finally:
        if client:
            await client.aclose()
    return result

@app.get("/api/services")
async def my_services(user=Depends(current_user)):
    with db() as c:
        rows=[rowdict(r) for r in c.execute("SELECT * FROM services WHERE user_id=? ORDER BY id DESC",(user["id"],)).fetchall()]
    return await _enrich_services(rows)

@app.get("/api/services/{service_id}")
async def my_service_detail(service_id: int, user=Depends(current_user)):
    with db() as c:
        row=c.execute("SELECT * FROM services WHERE id=? AND user_id=?",(service_id,user["id"])).fetchone()
    if not row:
        raise HTTPException(404,"سرویس پیدا نشد")
    return (await _enrich_services([rowdict(row)]))[0]

@app.post("/api/orders")
async def create_order(body: OrderIn, user=Depends(current_user)):
    if body.kind != "config": raise HTTPException(400,"فقط سفارش کانفیگ از پنل اصلی پشتیبانی می‌شود")
    if not body.username.strip() or any(ord(ch) < 32 for ch in body.username): raise HTTPException(400,"نام اشتراک معتبر نیست")
    body.username = " ".join(body.username.split())
    with db() as c:
        p=c.execute("SELECT * FROM panels WHERE provider=? AND is_active=1 AND status='connected' LIMIT 1",(MAIN_PANEL_PROVIDER,)).fetchone()
        if not p: raise HTTPException(409,"پنل اصلی هنوز متصل و آزمایش نشده است")
        price=int(setting("config_price_per_gb","2000"))*body.volume_gb
        discount=c.execute("SELECT * FROM user_discounts WHERE user_id=? AND used=0 ORDER BY id LIMIT 1",(user["id"],)).fetchone()
        discount_percent=int(discount["percent"]) if discount else 0
        if discount_percent: price=max(0,round(price*(100-discount_percent)/100))
        cur=c.execute("INSERT INTO orders(user_id,kind,panel_id,volume_gb,days,ip_limit,protocol,username,price,discount_percent,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
          (user["id"],"config",p["id"],body.volume_gb,body.days,body.ip_limit,"auto",body.username,price,discount_percent,"pending_payment",now()))
        oid=cur.lastrowid
        if discount: c.execute("UPDATE user_discounts SET used=1 WHERE id=?",(discount["id"],))
    return {"id":oid,"price":price,"currency":setting("currency_label","تومان"),"status":"pending_payment"}

@app.post("/api/orders/{order_id}/pay-wallet")
async def pay_order(order_id: int,user=Depends(current_user)):
    with db() as c:
        order=c.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,user["id"])).fetchone()
        if not order: raise HTTPException(404,"سفارش پیدا نشد")
        if order["status"]!="pending_payment": raise HTTPException(409,"وضعیت سفارش اجازهٔ پرداخت نمی‌دهد")
        if user["balance"]<order["price"]: raise HTTPException(402,"موجودی کیف پول کافی نیست")
        panel=c.execute("SELECT * FROM panels WHERE provider=? AND is_active=1 AND status='connected' LIMIT 1",(MAIN_PANEL_PROVIDER,)).fetchone()
        if not panel: raise HTTPException(409,"پنل اصلی در حال حاضر متصل نیست")
        c.execute("UPDATE users SET balance=balance-? WHERE id=?",(order["price"],user["id"]))
        c.execute("INSERT INTO transactions(user_id,kind,amount,currency,status,note,created_at) VALUES(?,?,?,?,?,?,?)",(user["id"],"purchase",-order["price"],"IRR","completed",f"پرداخت سفارش #{order_id}",now()))
        c.execute("UPDATE orders SET status='provisioning',paid_at=? WHERE id=?",(now(),order_id))
    try:
        result=await create_remote_client(rowdict(panel),order["username"],order["volume_gb"],order["days"],order["ip_limit"],user["id"])
        with db() as c:
            expires=now()+order["days"]*86400
            c.execute("INSERT INTO services(order_id,user_id,panel_id,provider,external_id,username,subscription_url,config_url,public_url,volume_gb,days,ip_limit,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             (order_id,user["id"],panel["id"],"FineVPN",result["external_id"],order["username"],result["subscription_url"],result["config_url"],result["public_url"],order["volume_gb"],order["days"],order["ip_limit"],expires,now()))
            c.execute("UPDATE orders SET status='completed',output_url=?,config_url=?,external_id=? WHERE id=?",(result["subscription_url"],result["config_url"],result["external_id"],order_id))
            c.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(user["id"],"سرویس شما آماده است",f"سرویس {order['username']} با موفقیت ساخته شد.",now()))
        return {"ok":True,"status":"completed","url":result["subscription_url"],"subscription_url":result["subscription_url"],"config_url":result["config_url"],"public_url":result["public_url"],"links_count":result["links_count"],"message":"گروه اشتراک و کانفیگ‌ها با موفقیت ساخته شد"}
    except Exception as e:
        log.exception("Provisioning failed for order %s",order_id)
        with db() as c:
            # Refund immediately if provisioning fails; never keep payment for a nonexistent config.
            c.execute("UPDATE users SET balance=balance+? WHERE id=?",(order["price"],user["id"]))
            c.execute("INSERT INTO transactions(user_id,kind,amount,currency,status,note,created_at) VALUES(?,?,?,?,?,?,?)",(user["id"],"refund",order["price"],"IRR","completed",f"بازگشت وجه سفارش #{order_id} به دلیل خطای ساخت",now()))
            c.execute("UPDATE orders SET status='refunded',error=? WHERE id=?",(str(e)[:500],order_id))
        raise HTTPException(502,"ساخت سرویس از پنل ناموفق بود؛ مبلغ به کیف پول برگشت داده شد. جزئیات برای مدیر ثبت شد.")

class TestClaimIn(BaseModel): panel_id: int = 1

@app.post("/api/test-claim")
async def test_claim(body:TestClaimIn,user=Depends(current_user)):
    if setting("test_enabled","1")!="1": raise HTTPException(403,"سرویس تست در حال حاضر غیرفعال است")
    with db() as c:
        r=c.execute("SELECT test_used FROM users WHERE id=?",(user["id"],)).fetchone()
        if r[0]: raise HTTPException(409,"سرویس تست فقط یک بار قابل دریافت است")
        panel=c.execute("SELECT * FROM panels WHERE provider=? AND is_active=1 AND status='connected' LIMIT 1",(MAIN_PANEL_PROVIDER,)).fetchone()
        if not panel: raise HTTPException(409,"پنل متصل و آماده‌ای برای تست وجود ندارد")
        c.execute("UPDATE users SET test_used=1 WHERE id=?",(user["id"],))
        gb=1; days=7; ip_limit=1; uname=f"FineVPN-Test-{user['id']}"
        cur=c.execute("INSERT INTO orders(user_id,kind,panel_id,volume_gb,days,ip_limit,protocol,username,price,status,created_at,paid_at) VALUES(?,?,?,?,?,?,?, ?,0,'provisioning',?,?)",(user["id"],"test",panel["id"],gb,days,ip_limit,"auto",uname,now(),now())); oid=cur.lastrowid
    try:
        result=await create_remote_client(rowdict(panel),uname,gb,days,ip_limit,user["id"])
        with db() as c:
            c.execute("INSERT INTO services(order_id,user_id,panel_id,provider,external_id,username,subscription_url,config_url,public_url,volume_gb,days,ip_limit,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(oid,user["id"],panel["id"],"FineVPN",result["external_id"],uname,result["subscription_url"],result["config_url"],result["public_url"],gb,days,ip_limit,now()+days*86400,now()))
            c.execute("UPDATE orders SET status='completed',output_url=?,config_url=? WHERE id=?",(result["subscription_url"],result["config_url"],oid))
        return {"ok":True,"url":result["subscription_url"],"subscription_url":result["subscription_url"],"config_url":result["config_url"],"public_url":result["public_url"],"links_count":result["links_count"],"username":uname}
    except Exception as e:
        with db() as c: c.execute("UPDATE users SET test_used=0 WHERE id=?",(user["id"],)); c.execute("UPDATE orders SET status='failed',error=? WHERE id=?",(str(e)[:500],oid))
        raise HTTPException(502,"ساخت سرویس تست از پنل ناموفق بود")

@app.post("/api/wallet/crypto")
async def crypto_topup(body: TopupIn,user=Depends(current_user)):
    currency=body.currency.upper()
    if currency not in {"USDT","TON","TRX","BTC"}: raise HTTPException(400,"ارز پشتیبانی نمی‌شود")
    address=setting("crypto_"+currency.lower(),"")
    if not address: raise HTTPException(409,"آدرس دریافت این ارز هنوز توسط مدیر تنظیم نشده است")
    with db() as c:
        cur=c.execute("INSERT INTO transactions(user_id,kind,amount,currency,txid,status,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
           (user["id"],"crypto_topup",int(body.amount*1000000),currency,body.txid,"pending",f"مبلغ رمزارز: {body.amount} {currency} | آدرس مقصد: {address}",now()))
        tid=cur.lastrowid
    return {"ok":True,"id":tid,"status":"pending","message":"درخواست پرداخت ثبت شد و پس از بررسی به کیف پول اضافه می‌شود"}

@app.get("/api/wallet/transactions")
async def transactions(user=Depends(current_user)):
    with db() as c: rows=[rowdict(r) for r in c.execute("SELECT id,kind,amount,currency,txid,status,note,created_at FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 50",(user["id"],)).fetchall()]
    return rows

@app.post("/api/redeem")
async def redeem(body: RedeemIn,user=Depends(current_user)):
    code=body.code.strip().upper()
    with db() as c:
        r=c.execute("SELECT * FROM redeem_codes WHERE code=? AND active=1",(code,)).fetchone()
        if not r: raise HTTPException(404,"کد معتبر نیست")
        if r["expires_at"] and r["expires_at"]<now(): raise HTTPException(410,"مهلت این کد تمام شده است")
        if r["uses"]>=r["max_uses"]: raise HTTPException(409,"ظرفیت استفاده از کد تمام شده است")
        try: c.execute("INSERT INTO redemptions(code_id,user_id,created_at) VALUES(?,?,?)",(r["id"],user["id"],now()))
        except sqlite3.IntegrityError: raise HTTPException(409,"این کد را قبلاً استفاده کرده‌اید")
        if r["kind"]=="points": c.execute("UPDATE users SET points=points+? WHERE id=?",(r["value"],user["id"]))
        elif r["kind"]=="balance": c.execute("UPDATE users SET balance=balance+? WHERE id=?",(r["value"],user["id"]))
        elif r["kind"]=="discount":
            if r["value"]>100: raise HTTPException(400,"درصد تخفیف نمی‌تواند بیشتر از ۱۰۰ باشد")
            c.execute("INSERT INTO user_discounts(user_id,code_id,percent,created_at) VALUES(?,?,?,?)",(user["id"],r["id"],r["value"],now()))
        else: raise HTTPException(400,"نوع کد ناشناخته است")
        c.execute("UPDATE redeem_codes SET uses=uses+1 WHERE id=?",(r["id"],))
    return {"ok":True,"kind":r["kind"],"value":r["value"],"message":"کد با موفقیت ثبت شد"}

@app.post("/api/rewards/spin")
async def spin(user=Depends(current_user)):
    if setting("wheel_enabled","1")!="1": raise HTTPException(403,"گردونه غیرفعال است")
    with db() as c:
        r=c.execute("SELECT last_spin FROM users WHERE id=?",(user["id"],)).fetchone()
        if r["last_spin"] and now()-r["last_spin"]<86400: raise HTTPException(429,"گردونه هر ۲۴ ساعت یک بار قابل استفاده است")
        lo=int(setting("points_per_spin_min","1")); hi=int(setting("points_per_spin_max","20")); prize=secrets.randbelow(max(1,hi-lo+1))+lo
        c.execute("UPDATE users SET points=points+?,last_spin=? WHERE id=?",(prize,now(),user["id"]))
        c.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(user["id"],"جایزه گردونه",f"{prize} امتیاز به دست آوردید.",now()))
    return {"ok":True,"points":prize}

@app.post("/api/rewards/points-config")
async def points_config(body: OrderIn,user=Depends(current_user)):
    # This endpoint creates an order paid with points; it does not bypass panel readiness.
    needed_gb=int(setting("points_config_gb","1")); days=int(setting("points_config_days","7")); price=needed_gb*int(setting("points_per_gb","10"))
    if body.volume_gb!=needed_gb or body.days!=days: raise HTTPException(400,"پلن امتیازی فعلی قابل تغییر نیست")
    with db() as c:
        panel=c.execute("SELECT * FROM panels WHERE provider=? AND status='connected' AND is_active=1 LIMIT 1",(MAIN_PANEL_PROVIDER,)).fetchone()
        if not panel: raise HTTPException(409,"پنل اصلی متصل نیست")
        if user["points"]<price: raise HTTPException(402,"امتیاز کافی ندارید")
        c.execute("UPDATE users SET points=points-? WHERE id=?",(price,user["id"]))
        cur=c.execute("INSERT INTO orders(user_id,kind,panel_id,volume_gb,days,ip_limit,protocol,username,price,status,created_at,paid_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
          (user["id"],"points",panel["id"],needed_gb,days,1,"auto",body.username,price,"provisioning",now(),now()))
        oid=cur.lastrowid
    try:
        result=await create_remote_client(rowdict(panel),body.username,needed_gb,days,1,user["id"])
        with db() as c:
            c.execute("INSERT INTO services(order_id,user_id,panel_id,provider,external_id,username,subscription_url,config_url,public_url,volume_gb,days,ip_limit,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(oid,user["id"],panel["id"],"FineVPN",result["external_id"],body.username,result["subscription_url"],result["config_url"],result["public_url"],needed_gb,days,1,now()+days*86400,now()))
            c.execute("UPDATE orders SET status='completed',output_url=?,config_url=? WHERE id=?",(result["subscription_url"],result["config_url"],oid))
        return {"ok":True,"url":result["subscription_url"],"subscription_url":result["subscription_url"],"config_url":result["config_url"],"public_url":result["public_url"],"links_count":result["links_count"],"points_spent":price}
    except Exception as e:
        with db() as c: c.execute("UPDATE users SET points=points+? WHERE id=?",(price,user["id"])); c.execute("UPDATE orders SET status='refunded',error=? WHERE id=?",(str(e)[:500],oid))
        raise HTTPException(502,"ساخت سرویس ناموفق بود؛ امتیازها برگشت داده شد")

@app.post("/api/referral/claim")
async def referral_claim(user=Depends(current_user)):
    with db() as c:
        referred=c.execute("SELECT ref_by FROM users WHERE id=?",(user["id"],)).fetchone()
    if not referred or not referred[0]: raise HTTPException(400,"این حساب با لینک دعوت ثبت نشده است")
    return {"ok":True,"message":"پاداش دعوت پس از ثبت‌نام و سیاست ضدتقلب محاسبه می‌شود"}

@app.post("/api/tickets")
async def ticket_create(body: TicketIn,user=Depends(current_user)):
    with db() as c:
        cur=c.execute("INSERT INTO tickets(user_id,subject,status,created_at,updated_at) VALUES(?,?, 'open',?,?)",(user["id"],body.subject,now(),now()))
        tid=cur.lastrowid
        c.execute("INSERT INTO ticket_messages(ticket_id,sender_id,sender_role,body,created_at) VALUES(?,?, 'user',?,?)",(tid,user["id"],body.body,now()))
    return {"id":tid,"status":"open"}

@app.get("/api/tickets")
async def tickets(user=Depends(current_user)):
    with db() as c: return [rowdict(r) for r in c.execute("SELECT t.*, (SELECT body FROM ticket_messages m WHERE m.ticket_id=t.id ORDER BY id DESC LIMIT 1) last_message FROM tickets t WHERE user_id=? ORDER BY id DESC",(user["id"],)).fetchall()]

@app.get("/api/tickets/{tid}")
async def ticket_detail(tid: int,user=Depends(current_user)):
    with db() as c:
        t=c.execute("SELECT * FROM tickets WHERE id=? AND user_id=?",(tid,user["id"])).fetchone()
        if not t: raise HTTPException(404,"تیکت پیدا نشد")
        msgs=[rowdict(r) for r in c.execute("SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY id",(tid,)).fetchall()]
    return {"ticket":rowdict(t),"messages":msgs}

@app.post("/api/tickets/{tid}/reply")
async def ticket_reply(tid:int,body:ReplyIn,user=Depends(current_user)):
    with db() as c:
        t=c.execute("SELECT id FROM tickets WHERE id=? AND user_id=?",(tid,user["id"])).fetchone()
        if not t: raise HTTPException(404,"تیکت پیدا نشد")
        c.execute("INSERT INTO ticket_messages(ticket_id,sender_id,sender_role,body,created_at) VALUES(?,?, 'user',?,?)",(tid,user["id"],body.body,now()))
        c.execute("UPDATE tickets SET status='open',updated_at=? WHERE id=?",(now(),tid))
    return {"ok":True}

@app.get("/api/notifications")
async def notifications(user=Depends(current_user)):
    with db() as c: rows=[rowdict(r) for r in c.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50",(user["id"],)).fetchall()]
    return rows

@app.post("/api/notifications/read")
async def notifications_read(user=Depends(current_user)):
    with db() as c: c.execute("UPDATE notifications SET is_read=1 WHERE user_id=?",(user["id"],))
    return {"ok":True}

# ---------------- Admin API ----------------
@app.get("/api/admin/overview")
async def admin_overview(admin=Depends(require_admin)):
    with db() as c:
        def count(q): return c.execute(q).fetchone()[0]
        pending=count("SELECT COUNT(*) FROM transactions WHERE status='pending'")
        return {"users":count("SELECT COUNT(*) FROM users"),"blocked":count("SELECT COUNT(*) FROM users WHERE is_blocked=1"),
          "orders":count("SELECT COUNT(*) FROM orders"),"services":count("SELECT COUNT(*) FROM services WHERE status='active'"),
          "tickets":count("SELECT COUNT(*) FROM tickets WHERE status='open'"),"pending_payments":pending,
          "revenue":count("SELECT COALESCE(SUM(-amount),0) FROM transactions WHERE kind='purchase'")}

@app.get("/api/admin/panels")
async def admin_panels(admin=Depends(require_admin)):
    with db() as c:
        r=c.execute("SELECT * FROM panels WHERE provider=? LIMIT 1",(MAIN_PANEL_PROVIDER,)).fetchone()
    if not r: return []
    return [dict(id=r["id"],provider="FineVPN Core",name=MAIN_PANEL_NAME,base_url=PANEL_PUBLIC_URL,
                 is_active=bool(r["is_active"]),status=r["status"],last_check=r["last_check"],last_error=r["last_error"])]

@app.get("/api/admin/main-panel/status")
async def admin_main_panel_status(admin=Depends(require_admin)):
    with db() as c:
        r=c.execute("SELECT status,last_check,last_error FROM panels WHERE provider=? LIMIT 1",(MAIN_PANEL_PROVIDER,)).fetchone()
    return {"status": r["status"] if r else "unconfigured", "last_check": r["last_check"] if r else 0,
            "last_error": r["last_error"] if r else "", "public_url": PANEL_PUBLIC_URL}

@app.post("/api/admin/main-panel/test")
async def admin_test_main_panel(admin=Depends(require_admin)):
    with db() as c:
        r=c.execute("SELECT id FROM panels WHERE provider=? LIMIT 1",(MAIN_PANEL_PROVIDER,)).fetchone()
    if not r: raise HTTPException(500,"رکورد پنل اصلی پیدا نشد")
    try:
        info=await probe_main_panel()
        msg=f"اتصال موفق؛ نسخه {info['version']}، لینک‌ها {info['links_count']}، فعال {info['active_links']}"
        with db() as c: c.execute("UPDATE panels SET status='connected',last_check=?,last_error='',is_active=1 WHERE id=?",(now(),r[0]))
        audit(admin["id"],"main_panel_test_ok",json.dumps(info,ensure_ascii=False))
        return {"ok":True,"status":"connected","message":msg,"details":info}
    except Exception as e:
        with db() as c: c.execute("UPDATE panels SET status='error',last_check=?,last_error=? WHERE id=?",(now(),str(e)[:500],r[0]))
        audit(admin["id"],"main_panel_test_failed",str(e))
        raise HTTPException(502,"اتصال پنل اصلی ناموفق بود: "+str(e)[:300])

@app.post("/api/admin/panels")
async def admin_add_panel_disabled(admin=Depends(require_admin)):
    raise HTTPException(410,"FineVPN فقط از پنل اصلی متصل‌شده استفاده می‌کند؛ افزودن پنل دیگر غیرفعال است")

@app.delete("/api/admin/panels/{pid}")
async def admin_delete_panel_disabled(pid:int,admin=Depends(require_admin)):
    raise HTTPException(410,"حذف پنل اصلی از این مسیر مجاز نیست")

@app.get("/api/admin/users")
async def admin_users(q:str="",admin=Depends(require_admin)):
    with db() as c:
        rows=[rowdict(r) for r in c.execute("SELECT id,first_name,last_name,username,balance,points,is_blocked,created_at FROM users WHERE CAST(id AS TEXT) LIKE ? OR first_name LIKE ? OR username LIKE ? ORDER BY created_at DESC LIMIT 100",(f"%{q}%",f"%{q}%",f"%{q}%")).fetchall()]
    return rows

@app.post("/api/admin/users/{uid}/adjust")
async def admin_adjust_user(uid:int,body:UserAdjustIn,admin=Depends(require_admin)):
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?",(uid,)).fetchone(): raise HTTPException(404,"کاربر پیدا نشد")
        c.execute("UPDATE users SET balance=MAX(0,balance+?),points=MAX(0,points+?) WHERE id=?",(body.balance_delta,body.points_delta,uid))
        if body.blocked is not None: c.execute("UPDATE users SET is_blocked=? WHERE id=?",(int(body.blocked),uid))
        if body.balance_delta: c.execute("INSERT INTO transactions(user_id,kind,amount,status,note,created_at) VALUES(?,?,?,?,?,?)",(uid,"admin_adjust",body.balance_delta,"completed",f"تغییر موجودی توسط مدیر {admin['id']}",now()))
        c.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(uid,"به‌روزرسانی حساب","حساب شما توسط پشتیبانی به‌روزرسانی شد.",now()))
    audit(admin["id"],"user_adjust",f"uid={uid}, balance={body.balance_delta}, points={body.points_delta}, blocked={body.blocked}")
    return {"ok":True}

@app.post("/api/admin/users/{uid}/message")
async def admin_user_message(uid:int,body:ReplyIn,admin=Depends(require_admin)):
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?",(uid,)).fetchone(): raise HTTPException(404,"کاربر پیدا نشد")
        c.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(uid,"پیام مدیریت",body.body,now()))
    delivered=await telegram_send(uid,body.body[:3800])
    audit(admin["id"],"user_message",f"uid={uid}; delivered={delivered}")
    return {"ok":True,"telegram_delivered":delivered}

@app.get("/api/admin/transactions")
async def admin_transactions(admin=Depends(require_admin)):
    with db() as c: rows=[rowdict(r) for r in c.execute("SELECT t.*,u.first_name FROM transactions t LEFT JOIN users u ON u.id=t.user_id WHERE t.status='pending' OR t.kind='crypto_topup' ORDER BY t.id DESC LIMIT 100").fetchall()]
    return rows

@app.post("/api/admin/transactions/{tid}/review")
async def admin_review_transaction(tid:int,body:dict,admin=Depends(require_admin)):
    action=body.get("action")
    if action not in {"approve","reject"}: raise HTTPException(400,"عملیات نامعتبر")
    with db() as c:
        t=c.execute("SELECT * FROM transactions WHERE id=?",(tid,)).fetchone()
        if not t or t["status"]!="pending": raise HTTPException(404,"درخواست در انتظار پیدا نشد")
        if action=="approve":
            if t["kind"]=="crypto_topup":
                credit=int((body or {}).get("credit_amount",0) or 0)
                if credit<=0: raise HTTPException(400,"معادل تومانی پرداخت را مشخص کنید")
                c.execute("UPDATE users SET balance=balance+? WHERE id=?",(credit,t["user_id"]))
                c.execute("INSERT INTO transactions(user_id,kind,amount,currency,status,note,created_at) VALUES(?,?,?,?,?,?,?)",(t["user_id"],"crypto_credit",credit,"IRR","completed",f"اعتبار معادل پرداخت رمزارزی #{tid}",now()))
            c.execute("UPDATE transactions SET status='approved',note=note||? WHERE id=?",(f" | تأیید مدیر {admin['id']}",tid))
        else: c.execute("UPDATE transactions SET status='rejected',note=note||? WHERE id=?",(f" | رد مدیر {admin['id']}",tid))
        c.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(t["user_id"],"وضعیت پرداخت", "درخواست پرداخت شما تأیید شد." if action=="approve" else "درخواست پرداخت شما رد شد.",now()))
    await telegram_send(t["user_id"],"وضعیت درخواست پرداخت شما در FineVPN: "+("تأیید شد." if action=="approve" else "رد شد."))
    audit(admin["id"],"payment_"+action,str(tid)); return {"ok":True}

@app.get("/api/admin/tickets")
async def admin_tickets(admin=Depends(require_admin)):
    with db() as c: return [rowdict(r) for r in c.execute("SELECT t.*,u.first_name,u.username FROM tickets t JOIN users u ON u.id=t.user_id ORDER BY CASE t.status WHEN 'open' THEN 0 ELSE 1 END,t.updated_at DESC LIMIT 100").fetchall()]
@app.get("/api/admin/tickets/{tid}")
async def admin_ticket_detail(tid:int,admin=Depends(require_admin)):
    with db() as c:
        t=c.execute("SELECT * FROM tickets WHERE id=?",(tid,)).fetchone()
        if not t: raise HTTPException(404,"تیکت پیدا نشد")
        msgs=[rowdict(r) for r in c.execute("SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY id",(tid,)).fetchall()]
    return {"ticket":rowdict(t),"messages":msgs}
@app.post("/api/admin/tickets/{tid}/reply")
async def admin_ticket_reply(tid:int,body:ReplyIn,admin=Depends(require_admin)):
    with db() as c:
        t=c.execute("SELECT user_id FROM tickets WHERE id=?",(tid,)).fetchone()
        if not t: raise HTTPException(404,"تیکت پیدا نشد")
        c.execute("INSERT INTO ticket_messages(ticket_id,sender_id,sender_role,body,created_at) VALUES(?,?, 'admin',?,?)",(tid,admin["id"],body.body,now()))
        c.execute("UPDATE tickets SET status='answered',updated_at=? WHERE id=?",(now(),tid))
        c.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(t["user_id"],"پاسخ پشتیبانی",body.body[:250],now()))
    await telegram_send(t["user_id"],"پشتیبانی FineVPN پاسخ داد:\n"+body.body[:3500])
    audit(admin["id"],"ticket_reply",str(tid)); return {"ok":True}

@app.post("/api/admin/redeem")
async def admin_create_redeem(body:CodeIn,admin=Depends(require_admin)):
    if body.kind not in {"points","balance","discount"}: raise HTTPException(400,"نوع کد نامعتبر است")
    with db() as c:
        try: cur=c.execute("INSERT INTO redeem_codes(code,kind,value,max_uses,expires_at,created_at) VALUES(?,?,?,?,?,?)",(body.code.upper(),body.kind,body.value,body.max_uses,body.expires_at,now()))
        except sqlite3.IntegrityError: raise HTTPException(409,"این کد قبلاً ساخته شده است")
    audit(admin["id"],"redeem_create",body.code.upper()); return {"ok":True}
@app.get("/api/admin/redeem")
async def admin_redeem_list(admin=Depends(require_admin)):
    with db() as c: return [rowdict(r) for r in c.execute("SELECT * FROM redeem_codes ORDER BY id DESC LIMIT 100").fetchall()]

@app.post("/api/admin/settings")
async def admin_set_setting(body:SettingIn,admin=Depends(require_admin)):
    allowed={"config_price_per_gb","panel_price_per_gb","currency_label","crypto_usdt","crypto_ton","crypto_trx","crypto_btc","support_username","app_notice","wheel_enabled","referral_enabled","test_enabled","points_per_referral","points_per_gb","points_config_gb","points_config_days","points_per_spin_min","points_per_spin_max","wheel_enabled","referral_enabled","test_enabled","maintenance"}
    if body.key not in allowed: raise HTTPException(400,"این تنظیم قابل تغییر نیست")
    if body.key.endswith("price_per_gb") and (not body.value.isdigit() or int(body.value)<0): raise HTTPException(400,"قیمت نامعتبر است")
    if body.key in {"points_per_spin_min", "points_per_spin_max"}:
        if not body.value.isdigit() or not 1 <= int(body.value) <= 20:
            raise HTTPException(400,"جایزهٔ گردونه باید بین ۱ تا ۲۰ امتیاز باشد")
        proposed = int(body.value)
        other_key = "points_per_spin_max" if body.key == "points_per_spin_min" else "points_per_spin_min"
        other_value = int(setting(other_key, "20" if other_key == "points_per_spin_max" else "1"))
        if body.key == "points_per_spin_min" and proposed > other_value:
            raise HTTPException(400,"حداقل جایزه نمی‌تواند از حداکثر بیشتر باشد")
        if body.key == "points_per_spin_max" and proposed < other_value:
            raise HTTPException(400,"حداکثر جایزه نمی‌تواند از حداقل کمتر باشد")
    set_setting(body.key,body.value); audit(admin["id"],"setting",f"{body.key}={body.value}"); return {"ok":True}

@app.post("/api/admin/broadcast")
async def admin_broadcast(body:ReplyIn,admin=Depends(require_admin)):
    with db() as c:
        ids=[r[0] for r in c.execute("SELECT id FROM users WHERE is_blocked=0").fetchall()]
        c.executemany("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",[(uid,"اطلاعیه FineVPN",body.body,now()) for uid in ids])
    audit(admin["id"],"broadcast",f"recipients={len(ids)}")
    # Telegram delivery is attempted asynchronously by the companion bot; DB inbox is durable.
    return {"ok":True,"queued":len(ids),"message":"اعلان داخل مینی‌اپ ثبت شد"}

@app.get("/api/admin/products")
async def admin_products(admin=Depends(require_admin)):
    with db() as c: return [rowdict(r) for r in c.execute("SELECT * FROM products ORDER BY id DESC").fetchall()]
@app.post("/api/admin/products")
async def admin_product(body:dict,admin=Depends(require_admin)):
    title=str(body.get("title","پلن جدید"))[:80]; kind=body.get("kind","config"); gb=int(body.get("volume_gb",0)); days=int(body.get("days",30)); price=int(body.get("price",0))
    if kind != "config" or gb<1 or gb>10000 or days<1 or days>3650 or price<0: raise HTTPException(400,"مشخصات پلن معتبر نیست")
    with db() as c: cur=c.execute("INSERT INTO products(kind,title,volume_gb,days,price,enabled) VALUES(?,?,?,?,?,1)",(kind,title,gb,days,price))
    audit(admin["id"],"product_create",title); return {"id":cur.lastrowid}


@app.get("/api/admin/services")
async def admin_services(admin=Depends(require_admin)):
    with db() as c:
        rows=[rowdict(r) for r in c.execute("SELECT s.*,u.first_name,u.last_name FROM services s JOIN users u ON u.id=s.user_id ORDER BY s.id DESC LIMIT 100").fetchall()]
    return await _enrich_services(rows)

@app.post("/api/admin/services/{service_id}/revoke")
async def admin_revoke_service(service_id: int, admin=Depends(require_admin)):
    with db() as c:
        row=c.execute("SELECT * FROM services WHERE id=?",(service_id,)).fetchone()
    if not row:
        raise HTTPException(404,"سرویس پیدا نشد")
    client=await main_panel_client()
    try:
        response=await client.delete(PANEL_BASE_URL+"/api/subs/"+str(row["external_id"]))
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(502,"حذف گروه اشتراک از پنل اصلی ناموفق بود") from exc
    finally:
        await client.aclose()
    with db() as c:
        c.execute("UPDATE services SET status='revoked' WHERE id=?",(service_id,))
        c.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",(row["user_id"],"سرویس غیرفعال شد",f"سرویس {row['username']} توسط مدیریت غیرفعال شد.",now()))
    audit(admin["id"],"service_revoke",f"service={service_id}, sub={row['external_id']}")
    await telegram_send(row["user_id"],f"سرویس {row['username']} توسط مدیریت غیرفعال شد.")
    return {"ok":True}

@app.get("/api/admin/audit")
async def admin_audit(admin=Depends(require_admin)):
    with db() as c: return [rowdict(r) for r in c.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 100").fetchall()]

# Optional static assets directory.
if os.path.isdir(os.path.join(BASE_DIR,"assets")): app.mount("/assets",StaticFiles(directory=os.path.join(BASE_DIR,"assets")),name="assets")
