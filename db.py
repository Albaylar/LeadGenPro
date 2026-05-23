import sqlite3
import json
import logging
from datetime import datetime

DB = "leadgen.db"
logger = logging.getLogger(__name__)


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
            analyze_mode  TEXT    DEFAULT 'find_only'
        );
        CREATE TABLE IF NOT EXISTS leads (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id   INTEGER,
            name          TEXT,
            sector        TEXT,
            website       TEXT,
            email         TEXT,
            phone         TEXT,
            quality_score INTEGER,
            issues        TEXT,
            priority      TEXT,
            email_sent    TEXT    DEFAULT 'Hayır',
            analyzed      INTEGER DEFAULT 0,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        );
        CREATE INDEX IF NOT EXISTS idx_leads_campaign ON leads(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
        """)
    # Mevcut DB'ye eksik kolonları ekle (idempotent)
    for stmt in [
        "ALTER TABLE campaigns ADD COLUMN search_mode TEXT DEFAULT 'standard'",
        "ALTER TABLE campaigns ADD COLUMN analyze_mode TEXT DEFAULT 'find_only'",
        "ALTER TABLE leads ADD COLUMN analyzed INTEGER DEFAULT 0",
    ]:
        try:
            with conn() as c:
                c.execute(stmt)
        except Exception:
            pass


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
        return row["value"] if row else default


def set_setting(key, value):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))


def create_campaign(city, sectors, search_mode="standard", analyze_mode="find_only"):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO campaigns(city,sectors,status,created_at,search_mode,analyze_mode) VALUES(?,?,?,?,?,?)",
            (city, json.dumps(sectors), "running",
             datetime.now().strftime("%Y-%m-%d %H:%M"), search_mode, analyze_mode)
        )
        return cur.lastrowid


def update_campaign(cid, **kwargs):
    if not kwargs:
        return
    allowed = {"status", "progress_step", "progress_pct", "total_found", "emails_sent",
               "excel_path", "search_mode", "analyze_mode"}
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    if not filtered:
        return
    sets = ", ".join(f"{k}=?" for k in filtered)
    with conn() as c:
        c.execute(f"UPDATE campaigns SET {sets} WHERE id=?", (*filtered.values(), cid))


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


def update_lead_score(lead_id: int, quality_score: int, issues: list, priority: str):
    with conn() as c:
        c.execute(
            "UPDATE leads SET quality_score=?, issues=?, priority=?, analyzed=1 WHERE id=?",
            (quality_score,
             ", ".join(issues) if isinstance(issues, list) else (issues or ""),
             priority,
             lead_id)
        )


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


def update_lead_email(lead_id, status):
    with conn() as c:
        c.execute("UPDATE leads SET email_sent=? WHERE id=?", (status, lead_id))
