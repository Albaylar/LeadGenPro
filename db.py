import json
import logging
import os
import sqlite3
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken

DB = "leadgen.db"
SECRET_KEY_PATH = ".secret_key"
SENSITIVE_KEYS = {"anthropic_api_key", "gmail_password"}
ENC_PREFIX = "enc::"

logger = logging.getLogger(__name__)


def _load_or_create_key() -> bytes:
    """Fernet key'i diskten oku, yoksa oluştur ve 0600 izniyle kaydet."""
    if os.path.exists(SECRET_KEY_PATH):
        with open(SECRET_KEY_PATH, "rb") as f:
            return f.read().strip()
    key = Fernet.generate_key()
    with open(SECRET_KEY_PATH, "wb") as f:
        f.write(key)
    try:
        os.chmod(SECRET_KEY_PATH, 0o600)
    except OSError:
        pass
    logger.warning("Yeni şifreleme anahtarı oluşturuldu: %s (yedekle!)", SECRET_KEY_PATH)
    return key


_fernet = Fernet(_load_or_create_key())


def _encrypt(plain: str) -> str:
    if plain == "" or plain is None:
        return ""
    return ENC_PREFIX + _fernet.encrypt(plain.encode("utf-8")).decode("ascii")


def _decrypt(stored: str) -> str:
    if not stored:
        return ""
    if not stored.startswith(ENC_PREFIX):
        # Eski plain-text değer — migration sırasında yeniden yazılacak.
        return stored
    try:
        return _fernet.decrypt(stored[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.error("Şifre çözme başarısız — .secret_key değişmiş olabilir")
        return ""


def conn():
    c = sqlite3.connect(DB, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")   # concurrent read/write
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS offerings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            description     TEXT,             -- ne sattığının özeti (LLM bunu kullanır)
            pitch_de        TEXT,             -- {name} {sector} {website} {issues} {score} placeholder'lı şablon
            pitch_en        TEXT,
            -- ICP (Ideal Customer Profile) — basit alanlar, MVP için yeterli
            icp_sectors     TEXT,             -- virgülle ayrık sektör listesi (boş = hepsi)
            icp_locations   TEXT,             -- 'berlin,remote,germany,eu'
            icp_size_hint   TEXT,             -- 'micro' | 'small' | 'medium' | 'any'
            icp_signals     TEXT,             -- serbest metin, LLM bunu fit reasoning'de kullanır
            is_active       INTEGER DEFAULT 1,
            created_at      TEXT
        );
        CREATE TABLE IF NOT EXISTS outreach_messages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id       INTEGER NOT NULL,
            campaign_id   INTEGER NOT NULL,
            offering_id   INTEGER,
            sequence_step INTEGER DEFAULT 1,    -- 1=ilk mail, 2=follow-up, 3=break-up
            subject       TEXT,
            body          TEXT,
            sent_at       TEXT,
            status        TEXT DEFAULT 'sent',  -- 'sent' | 'bounced' | 'failed' | 'replied'
            error         TEXT,
            FOREIGN KEY (lead_id)    REFERENCES leads(id),
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
            FOREIGN KEY (offering_id) REFERENCES offerings(id)
        );
        CREATE INDEX IF NOT EXISTS idx_outreach_lead     ON outreach_messages(lead_id);
        CREATE INDEX IF NOT EXISTS idx_outreach_campaign ON outreach_messages(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_outreach_sent_at  ON outreach_messages(sent_at);
        CREATE TABLE IF NOT EXISTS campaigns (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            city          TEXT,
            sectors       TEXT,
            status        TEXT    DEFAULT 'pending',
            progress_step TEXT    DEFAULT '',
            progress_pct  INTEGER DEFAULT 0,
            total_found   INTEGER DEFAULT 0,
            emails_sent   INTEGER DEFAULT 0,
            created_at    TEXT,
            excel_path    TEXT,
            search_mode   TEXT    DEFAULT 'standard',
            analyze_mode  TEXT    DEFAULT 'find_only',
            mode          TEXT    DEFAULT 'lead',
            offering_id   INTEGER,
            FOREIGN KEY (offering_id) REFERENCES offerings(id)
        );
        CREATE TABLE IF NOT EXISTS leads (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id    INTEGER,
            name           TEXT,
            sector         TEXT,
            website        TEXT,
            email          TEXT,
            phone          TEXT,
            quality_score  INTEGER,           -- eski metrik: sitenin teknik kalitesi
            issues         TEXT,
            priority       TEXT,
            email_sent     TEXT    DEFAULT 'Hayır',
            analyzed       INTEGER DEFAULT 0,
            email_sent_at  TEXT,
            reply_received INTEGER DEFAULT 0,
            replied_at     TEXT,
            -- Yeni B2B prospect alanları
            fit_score      INTEGER,           -- offering ICP'sine uygunluk (0-100)
            fit_reasons    TEXT,              -- niye yüksek/düşük fit
            signals        TEXT,              -- tespit edilen intent sinyalleri (JSON ya da csv)
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        );
        CREATE INDEX IF NOT EXISTS idx_leads_campaign ON leads(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
        """)

    # Migrations
    for stmt in [
        "ALTER TABLE campaigns ADD COLUMN search_mode TEXT DEFAULT 'standard'",
        "ALTER TABLE campaigns ADD COLUMN analyze_mode TEXT DEFAULT 'find_only'",
        "ALTER TABLE campaigns ADD COLUMN mode TEXT DEFAULT 'lead'",
        "ALTER TABLE campaigns ADD COLUMN offering_id INTEGER",
        "ALTER TABLE leads ADD COLUMN analyzed INTEGER DEFAULT 0",
        "ALTER TABLE leads ADD COLUMN email_sent_at TEXT",
        "ALTER TABLE leads ADD COLUMN reply_received INTEGER DEFAULT 0",
        "ALTER TABLE leads ADD COLUMN replied_at TEXT",
        "ALTER TABLE leads ADD COLUMN fit_score INTEGER",
        "ALTER TABLE leads ADD COLUMN fit_reasons TEXT",
        "ALTER TABLE leads ADD COLUMN signals TEXT",
        "ALTER TABLE leads ADD COLUMN page_title TEXT DEFAULT ''",
        "ALTER TABLE offerings ADD COLUMN icp_sectors TEXT",
        "ALTER TABLE offerings ADD COLUMN icp_locations TEXT",
        "ALTER TABLE offerings ADD COLUMN icp_size_hint TEXT",
        "ALTER TABLE offerings ADD COLUMN icp_signals TEXT",
        "ALTER TABLE offerings ADD COLUMN is_active INTEGER DEFAULT 1",
    ]:
        try:
            with conn() as c:
                c.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                logger.warning("ALTER atlandı: %s — %s", stmt, e)

    # Eski settings'teki email_template_de/en → default offering migration
    try:
        with conn() as c:
            has_offerings = c.execute("SELECT COUNT(*) FROM offerings").fetchone()[0] > 0
            if not has_offerings:
                tpl_de = c.execute("SELECT value FROM settings WHERE key='email_template_de'").fetchone()
                tpl_en = c.execute("SELECT value FROM settings WHERE key='email_template_en'").fetchone()
                val_de = tpl_de["value"] if tpl_de else ""
                val_en = tpl_en["value"] if tpl_en else ""
                if val_de or val_en:
                    cur = c.execute(
                        "INSERT INTO offerings("
                        "name, description, pitch_de, pitch_en, "
                        "icp_sectors, icp_locations, icp_size_hint, icp_signals, "
                        "is_active, created_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        ("Varsayılan Pitch",
                         "Settings'ten otomatik aktarılan eski şablon. "
                         "Düzenleyip kendi B2B teklifinize uyarlayın.",
                         val_de, val_en,
                         "", "", "any", "",
                         1, datetime.now().strftime("%Y-%m-%d %H:%M"))
                    )
                    default_oid = cur.lastrowid
                    c.execute(
                        "UPDATE campaigns SET offering_id = ? WHERE offering_id IS NULL",
                        (default_oid,)
                    )
                    logger.info("Eski şablonlar default offering'e migrate edildi (id=%d)", default_oid)
    except sqlite3.Error as e:
        logger.warning("Offerings migration başarısız: %s", e)


def reset_stale_campaigns():
    """Uygulama yeniden başlatılınca 'running' takılı kalan kampanyaları düzelt."""
    with conn() as c:
        affected = c.execute(
            "UPDATE campaigns SET status='failed', progress_step='Uygulama yeniden başlatıldı' "
            "WHERE status='running'"
        ).rowcount
    if affected:
        logger.warning("%d takılı kampanya 'failed' olarak işaretlendi", affected)


def get_setting(key, default=""):
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    raw = row["value"] or ""
    if key not in SENSITIVE_KEYS:
        return raw
    # Sensitive: şifreliyse çöz, plain ise migration ile yeniden yaz.
    if raw.startswith(ENC_PREFIX):
        return _decrypt(raw)
    if raw:
        logger.info("Sensitive değer migrate ediliyor (plain -> encrypted): %s", key)
        set_setting(key, raw)
    return raw


def set_setting(key, value):
    stored = _encrypt(value) if key in SENSITIVE_KEYS else value
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, stored))


def create_campaign(city, sectors, search_mode="standard", analyze_mode="find_only",
                    mode: str = "lead", offering_id: int = None):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO campaigns(city,sectors,status,created_at,search_mode,analyze_mode,mode,offering_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (city, json.dumps(sectors), "running",
             datetime.now().strftime("%Y-%m-%d %H:%M"),
             search_mode, analyze_mode, mode, offering_id)
        )
        return cur.lastrowid


def update_campaign(cid, **kwargs):
    if not kwargs:
        return
    allowed = {"status", "progress_step", "progress_pct", "total_found", "emails_sent",
               "excel_path", "search_mode", "analyze_mode", "mode", "offering_id"}
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    if not filtered:
        return
    sets = ", ".join(f"{k}=?" for k in filtered)
    with conn() as c:
        c.execute(f"UPDATE campaigns SET {sets} WHERE id=?", (*filtered.values(), cid))


# (job_postings CRUD vizyon değişikliğiyle kaldırıldı)


def save_leads(cid, businesses, analyzed: bool = False):
    with conn() as c:
        c.executemany(
            "INSERT INTO leads(campaign_id,name,sector,website,email,phone,"
            "quality_score,issues,priority,email_sent,analyzed) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [(cid,
              b.get("name", ""),
              b.get("sector", ""),
              b.get("website", ""),
              b.get("email", ""),
              b.get("phone", ""),
              b.get("quality_score"),
              ", ".join(b.get("issues", [])) if isinstance(b.get("issues"), list) else (b.get("issues") or ""),
              b.get("priority"),
              "Hayır",
              1 if analyzed else 0)
             for b in businesses]
        )


def update_lead_score(lead_id: int, quality_score: int, issues: list, priority: str, page_title: str = ""):
    with conn() as c:
        c.execute(
            "UPDATE leads SET quality_score=?, issues=?, priority=?, analyzed=1, page_title=? WHERE id=?",
            (quality_score,
             ", ".join(issues) if isinstance(issues, list) else (issues or ""),
             priority,
             page_title,
             lead_id)
        )


def update_lead_contact_email(lead_id: int, email: str):
    with conn() as c:
        c.execute("UPDATE leads SET email=? WHERE id=?", (email, lead_id))


def get_campaign(cid):
    with conn() as c:
        return c.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()


def get_lead(lead_id: int):
    with conn() as c:
        return c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()


def get_leads(cid):
    with conn() as c:
        return c.execute(
            "SELECT * FROM leads WHERE campaign_id=? ORDER BY quality_score ASC", (cid,)
        ).fetchall()


def get_all_campaigns():
    with conn() as c:
        return c.execute("SELECT * FROM campaigns ORDER BY id DESC").fetchall()


def count_running_campaigns():
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM campaigns WHERE status='running'").fetchone()[0]


def update_lead_email(lead_id, status, sent_at: str = None):
    with conn() as c:
        if sent_at:
            c.execute("UPDATE leads SET email_sent=?, email_sent_at=? WHERE id=?", (status, sent_at, lead_id))
        else:
            c.execute("UPDATE leads SET email_sent=? WHERE id=?", (status, lead_id))


def get_offerings(active_only: bool = False):
    sql = "SELECT * FROM offerings"
    if active_only:
        sql += " WHERE COALESCE(is_active,1) = 1"
    sql += " ORDER BY id DESC"
    with conn() as c:
        return c.execute(sql).fetchall()


def get_offering(oid):
    with conn() as c:
        return c.execute("SELECT * FROM offerings WHERE id=?", (oid,)).fetchone()


def get_active_offering():
    """MVP: tek aktif offering var. En son aktif olan döner, hiç yoksa None."""
    with conn() as c:
        return c.execute(
            "SELECT * FROM offerings WHERE COALESCE(is_active,1) = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()


def create_offering(name, description="", pitch_de="", pitch_en="",
                    icp_sectors="", icp_locations="", icp_size_hint="any",
                    icp_signals="", is_active: int = 1) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO offerings(name, description, pitch_de, pitch_en, "
            "icp_sectors, icp_locations, icp_size_hint, icp_signals, is_active, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (name, description, pitch_de, pitch_en,
             icp_sectors, icp_locations, icp_size_hint, icp_signals, is_active,
             datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        return cur.lastrowid


def update_offering(oid, **kwargs):
    allowed = {"name", "description", "pitch_de", "pitch_en",
               "icp_sectors", "icp_locations", "icp_size_hint", "icp_signals",
               "is_active"}
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    if not filtered:
        return
    sets = ", ".join(f"{k}=?" for k in filtered)
    with conn() as c:
        c.execute(f"UPDATE offerings SET {sets} WHERE id=?", (*filtered.values(), oid))


def delete_offering(oid):
    with conn() as c:
        c.execute("DELETE FROM offerings WHERE id=?", (oid,))


# ---------- Prospect fit & reply tracking ----------

def update_lead_fit(lead_id: int, fit_score: int, fit_reasons, signals=None):
    reasons_str = ", ".join(fit_reasons) if isinstance(fit_reasons, list) else (fit_reasons or "")
    if isinstance(signals, list):
        signals_str = ", ".join(signals)
    elif isinstance(signals, dict):
        signals_str = json.dumps(signals, ensure_ascii=False)
    else:
        signals_str = signals or ""
    with conn() as c:
        c.execute(
            "UPDATE leads SET fit_score=?, fit_reasons=?, signals=? WHERE id=?",
            (fit_score, reasons_str, signals_str, lead_id)
        )


def update_lead_reply(lead_id, reply_received: int, replied_at: str):
    with conn() as c:
        c.execute(
            "UPDATE leads SET reply_received=?, replied_at=? WHERE id=?",
            (reply_received, replied_at, lead_id)
        )


# ---------- Outreach messages (sequence log) ----------

def save_outreach_message(lead_id: int, campaign_id: int, offering_id: int | None,
                          sequence_step: int, subject: str, body: str,
                          status: str = "sent", error: str = "") -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO outreach_messages("
            "lead_id, campaign_id, offering_id, sequence_step, subject, body, "
            "sent_at, status, error) VALUES(?,?,?,?,?,?,?,?,?)",
            (lead_id, campaign_id, offering_id, sequence_step, subject, body,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status, error)
        )
        return cur.lastrowid


def get_outreach_messages(lead_id: int):
    with conn() as c:
        return c.execute(
            "SELECT * FROM outreach_messages WHERE lead_id=? ORDER BY sequence_step ASC",
            (lead_id,)
        ).fetchall()


def get_last_outreach_step(lead_id: int) -> int:
    with conn() as c:
        row = c.execute(
            "SELECT MAX(sequence_step) AS step FROM outreach_messages WHERE lead_id=?",
            (lead_id,)
        ).fetchone()
        return (row["step"] or 0) if row else 0


def count_replied(cid: int) -> int:
    with conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM leads WHERE campaign_id=? AND reply_received=1", (cid,)
        ).fetchone()[0]
