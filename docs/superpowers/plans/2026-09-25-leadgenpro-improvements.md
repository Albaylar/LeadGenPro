# LeadGenPro İyileştirmeleri Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Email kazıma, Alman hukuki uyum kontrolü, gelişmiş outreach kalitesi ve follow-up dizisi ekleyerek LeadGenPro'yu canlıya hazır hale getirmek.

**Architecture:** `quality_agent.py` genişletilir (uyum kontrolleri + soup/text return), `contact_agent.py` bu veriyi alarak email çıkarır, `email_agent.py` model ve prompt güncellenir, `followup_runner.py` sequence mantığını yönetir.

**Tech Stack:** Python 3.12, FastAPI, SQLite, BeautifulSoup4, requests, Anthropic Claude SDK, pytest

---

## Dosya Haritası

| Dosya | İşlem | Sorumluluk |
|---|---|---|
| `db.py` | Modify | `page_title` migration, `update_lead_contact_email()`, `update_lead_score()` signature |
| `agents/contact_agent.py` | **Create** | HTML/regex/link-follow ile email çıkarma |
| `agents/quality_agent.py` | Modify | `_check_german_compliance()`, `analyze_website()` soup+text return |
| `runner.py` | Modify | `analyze_campaign_leads` ve `run_campaign`'a contact + page_title entegrasyonu |
| `agents/email_agent.py` | Modify | Model yükseltme, gelişmiş prompt, `sequence_step`, `extra_headers` |
| `followup_runner.py` | **Create** | Step-2/3 follow-up sequence |
| `app.py` | Modify | `/campaign/{cid}/send-followups` endpoint |
| `templates/campaign.html` | Modify | "Follow-up Gönder (N hazır)" butonu |
| `tests/__init__.py` | **Create** | Boş, pytest paket tanıma |
| `tests/conftest.py` | **Create** | Test DB fixture |
| `tests/test_contact_agent.py` | **Create** | Email kazıma unit testleri |
| `tests/test_quality_agent.py` | **Create** | Alman uyum kontrolü unit testleri |
| `tests/test_followup_runner.py` | **Create** | Follow-up adayı filtreleme testleri |

---

## Task 1: DB Foundation

**Files:**
- Modify: `db.py`

- [ ] **Step 1: `page_title` migration satırını `init_db()` içindeki migration listesine ekle**

`db.py` — `init_db()` fonksiyonundaki migration listesi (yaklaşık satır 146) içinde, mevcut ALTER ifadelerinin sonuna ekle:

```python
        "ALTER TABLE leads ADD COLUMN page_title TEXT DEFAULT ''",
```

- [ ] **Step 2: `update_lead_score()` imzasına `page_title` parametresi ekle**

`db.py` satır 287 civarındaki `update_lead_score` fonksiyonunu değiştir:

```python
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
```

- [ ] **Step 3: `update_lead_contact_email()` fonksiyonunu ekle**

`db.py`'de `update_lead_score` fonksiyonunun hemen altına ekle:

```python
def update_lead_contact_email(lead_id: int, email: str):
    with conn() as c:
        c.execute("UPDATE leads SET email=? WHERE id=?", (email, lead_id))
```

- [ ] **Step 4: Migration'ın çalıştığını doğrula**

```bash
cd /Users/furkandenizalbaylar/Desktop/ActiveProjects/LeadGenPro
source venv/bin/activate
python -c "from db import init_db; init_db(); print('OK')"
```

Beklenen çıktı: `OK` (hata yok)

- [ ] **Step 5: Commit**

```bash
git add db.py
git commit -m "feat(db): page_title kolonu, update_lead_contact_email fonksiyonu"
```

---

## Task 2: contact_agent.py

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_contact_agent.py`
- Create: `agents/contact_agent.py`

- [ ] **Step 1: Test altyapısını oluştur**

```bash
touch /Users/furkandenizalbaylar/Desktop/ActiveProjects/LeadGenPro/tests/__init__.py
```

`tests/conftest.py` dosyasını oluştur:

```python
import pytest
import db as db_module


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB", db_path)
    db_module.init_db()
    yield db_path
```

- [ ] **Step 2: Testleri yaz**

`tests/test_contact_agent.py`:

```python
from bs4 import BeautifulSoup
import pytest


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# --- _is_valid_email ---

def test_rejects_noreply():
    from agents.contact_agent import _is_valid_email
    assert not _is_valid_email("noreply@company.de")

def test_rejects_no_reply_with_dash():
    from agents.contact_agent import _is_valid_email
    assert not _is_valid_email("no-reply@company.de")

def test_rejects_example_domain():
    from agents.contact_agent import _is_valid_email
    assert not _is_valid_email("test@example.com")

def test_rejects_own_email():
    from agents.contact_agent import _is_valid_email
    assert not _is_valid_email("me@gmail.com", own_email="me@gmail.com")

def test_accepts_info_email():
    from agents.contact_agent import _is_valid_email
    assert _is_valid_email("info@zahnarzt-berlin.de")

def test_accepts_kontakt_email():
    from agents.contact_agent import _is_valid_email
    assert _is_valid_email("kontakt@praxis-mitte.de")

# --- _extract_from_html ---

def test_finds_mailto_link():
    from agents.contact_agent import _extract_from_html
    html = '<a href="mailto:info@zahnarzt-berlin.de">Kontakt</a>'
    result = _extract_from_html(_soup(html), html)
    assert "info@zahnarzt-berlin.de" in result

def test_finds_regex_email_in_text():
    from agents.contact_agent import _extract_from_html
    html = "<p>Schreiben Sie uns: kontakt@praxis-mitte.de</p>"
    result = _extract_from_html(_soup(html), html)
    assert "kontakt@praxis-mitte.de" in result

def test_prioritizes_info_prefix():
    from agents.contact_agent import _extract_from_html
    html = (
        '<a href="mailto:random@shop.de">Mail 1</a>'
        '<a href="mailto:info@shop.de">Mail 2</a>'
    )
    result = _extract_from_html(_soup(html), html)
    assert result[0] == "info@shop.de"

def test_returns_max_3():
    from agents.contact_agent import _extract_from_html
    html = " ".join(f'<a href="mailto:addr{i}@shop.de">m</a>' for i in range(10))
    result = _extract_from_html(_soup(html), html)
    assert len(result) <= 3

def test_skips_noreply_in_mailto():
    from agents.contact_agent import _extract_from_html
    html = '<a href="mailto:noreply@shop.de">x</a>'
    result = _extract_from_html(_soup(html), html)
    assert result == []
```

- [ ] **Step 3: Testlerin başarısız olduğunu doğrula**

```bash
cd /Users/furkandenizalbaylar/Desktop/ActiveProjects/LeadGenPro
source venv/bin/activate
pip install pytest -q
pytest tests/test_contact_agent.py -v 2>&1 | head -20
```

Beklenen: `ImportError` veya `ModuleNotFoundError` — `contact_agent` henüz yok.

- [ ] **Step 4: `agents/contact_agent.py` dosyasını oluştur**

```python
"""Email extraction from business websites."""
import logging
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SKIP_EMAIL_PATTERNS = [
    "noreply", "no-reply", "donotreply", "mailer-daemon",
    "@example.", "@domain.", "@email.", "@muster.", "@test.", "@placeholder.",
]

PRIORITY_PREFIXES = ["info@", "kontakt@", "hallo@", "office@", "mail@"]

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def _is_valid_email(email: str, own_email: str = "") -> bool:
    email_lower = email.lower()
    if any(p in email_lower for p in SKIP_EMAIL_PATTERNS):
        return False
    if own_email and email_lower == own_email.lower():
        return False
    if not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
        return False
    return True


def _priority_key(email: str) -> int:
    email_lower = email.lower()
    for i, prefix in enumerate(PRIORITY_PREFIXES):
        if email_lower.startswith(prefix):
            return i
    return len(PRIORITY_PREFIXES)


def _extract_from_html(soup: BeautifulSoup, text: str, own_email: str = "") -> list[str]:
    found: set[str] = set()

    for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
        href = a.get("href", "")
        email = href.replace("mailto:", "").split("?")[0].strip()
        if email and _is_valid_email(email, own_email):
            found.add(email)

    for match in EMAIL_RE.findall(text):
        if _is_valid_email(match, own_email):
            found.add(match)

    return sorted(found, key=_priority_key)[:3]


def extract_emails(
    soup: BeautifulSoup | None,
    response_text: str,
    final_url: str,
    own_email: str = "",
) -> list[str]:
    """Extract up to 3 valid emails from a website. Follows kontakt/impressum links if needed."""
    if soup is None:
        return []

    emails = _extract_from_html(soup, response_text, own_email)
    if emails:
        return emails

    followed = 0
    for a in soup.find_all("a", href=True):
        if followed >= 2:
            break
        href = a.get("href", "").lower()
        if not any(kw in href for kw in ("kontakt", "contact", "impressum")):
            continue

        raw_href = a["href"]
        url = raw_href if raw_href.startswith("http") else urljoin(final_url, raw_href)

        try:
            resp = requests.get(url, headers=_HEADERS, timeout=8, allow_redirects=True)
            sub_soup = BeautifulSoup(resp.text, "lxml")
            sub_emails = _extract_from_html(sub_soup, resp.text, own_email)
            if sub_emails:
                return sub_emails
            followed += 1
            time.sleep(0.5)
        except Exception as e:
            logger.warning("contact_agent: %s erişilemedi: %s", url, e)

    return []
```

- [ ] **Step 5: Testleri çalıştır**

```bash
pytest tests/test_contact_agent.py -v
```

Beklenen: Tüm testler `PASSED`.

- [ ] **Step 6: Commit**

```bash
git add agents/contact_agent.py tests/__init__.py tests/conftest.py tests/test_contact_agent.py
git commit -m "feat: contact_agent — websiteden email kazıma"
```

---

## Task 3: quality_agent.py — Alman Uyum Kontrolü

**Files:**
- Create: `tests/test_quality_agent.py`
- Modify: `agents/quality_agent.py`

- [ ] **Step 1: Testleri yaz**

`tests/test_quality_agent.py`:

```python
from bs4 import BeautifulSoup


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# --- Impressum ---

def test_flags_missing_impressum():
    from agents.quality_agent import _check_german_compliance
    soup = _soup("<html><body><p>Kein Impressum hier.</p></body></html>")
    issues, deduction = _check_german_compliance(soup, "", "https://example.de")
    assert any("Impressum yok" in i for i in issues)
    assert deduction >= 25

def test_no_impressum_flag_when_link_present():
    from agents.quality_agent import _check_german_compliance
    soup = _soup('<html><body><a href="/impressum">Impressum</a></body></html>')
    issues, _ = _check_german_compliance(soup, "", "https://example.de")
    assert not any("Impressum yok" in i for i in issues)

# --- Datenschutz ---

def test_flags_missing_datenschutz():
    from agents.quality_agent import _check_german_compliance
    soup = _soup("<html><body></body></html>")
    issues, deduction = _check_german_compliance(soup, "nothing here", "https://example.de")
    assert any("Datenschutz" in i for i in issues)
    assert deduction >= 25

def test_no_datenschutz_flag_when_text_present():
    from agents.quality_agent import _check_german_compliance
    soup = _soup('<html><body><a href="/datenschutz">Datenschutz</a></body></html>')
    issues, _ = _check_german_compliance(soup, "datenschutz", "https://example.de")
    assert not any("Datenschutzerklärung yok" in i for i in issues)

# --- Cookie consent ---

def test_flags_missing_cookie_consent():
    from agents.quality_agent import _check_german_compliance
    soup = _soup("<html><body></body></html>")
    issues, _ = _check_german_compliance(soup, "no cookie info here", "https://example.de")
    assert any("Cookie" in i for i in issues)

def test_no_cookie_flag_when_cookiebot_present():
    from agents.quality_agent import _check_german_compliance
    soup = _soup("<html><body></body></html>")
    issues, _ = _check_german_compliance(soup, "cookiebot script loaded", "https://example.de")
    assert not any("Cookie onay mekanizması yok" in i for i in issues)

# --- Language ---

def test_flags_wrong_lang():
    from agents.quality_agent import _check_german_compliance
    soup = _soup('<html lang="en"><body></body></html>')
    issues, _ = _check_german_compliance(soup, "", "https://example.de")
    assert any("dil bildirimi" in i for i in issues)

def test_no_lang_flag_when_de():
    from agents.quality_agent import _check_german_compliance
    soup = _soup('<html lang="de"><body></body></html>')
    issues, _ = _check_german_compliance(soup, "", "https://example.de")
    assert not any("dil bildirimi" in i for i in issues)

# --- Alt text ---

def test_flags_images_without_alt():
    from agents.quality_agent import _check_german_compliance
    html = "<html><body>" + "".join(f'<img src="img{i}.jpg">' for i in range(5)) + "</body></html>"
    soup = _soup(html)
    issues, _ = _check_german_compliance(soup, html, "https://example.de")
    assert any("alt metin" in i for i in issues)

def test_no_alt_flag_when_alts_present():
    from agents.quality_agent import _check_german_compliance
    html = '<html><body><img src="a.jpg" alt="Praxis Bild"></body></html>'
    soup = _soup(html)
    issues, _ = _check_german_compliance(soup, html, "https://example.de")
    assert not any("alt metin" in i for i in issues)

# --- Schema.org ---

def test_flags_missing_schema():
    from agents.quality_agent import _check_german_compliance
    soup = _soup("<html><body></body></html>")
    issues, _ = _check_german_compliance(soup, "no schema here", "https://example.de")
    assert any("Schema.org" in i for i in issues)

def test_no_schema_flag_when_present():
    from agents.quality_agent import _check_german_compliance
    soup = _soup("<html><body></body></html>")
    issues, _ = _check_german_compliance(soup, '"@type": "LocalBusiness", "@context": "schema.org"', "https://example.de")
    assert not any("Schema.org" in i for i in issues)

# --- analyze_website return type ---

def test_analyze_website_returns_soup_and_text(requests_mock):
    from agents.quality_agent import analyze_website
    requests_mock.get(
        "https://example.de",
        text='<html lang="de"><body><a href="/impressum">Impressum</a></body></html>',
        headers={"Content-Type": "text/html"},
    )
    result = analyze_website("https://example.de")
    assert "soup" in result
    assert "response_text" in result
    assert result["soup"] is not None

def test_analyze_website_returns_none_soup_on_error():
    from agents.quality_agent import analyze_website
    result = analyze_website("https://this-domain-does-not-exist-xyz.de")
    assert result["soup"] is None
    assert result["response_text"] == ""
```

- [ ] **Step 2: Test bağımlılıklarını yükle**

```bash
cd /Users/furkandenizalbaylar/Desktop/ActiveProjects/LeadGenPro
source venv/bin/activate
pip install pytest requests-mock -q
```

- [ ] **Step 3: Testlerin başarısız olduğunu doğrula**

```bash
pytest tests/test_quality_agent.py -v 2>&1 | head -30
```

Beklenen: `ImportError` çünkü `_check_german_compliance` henüz yok.

- [ ] **Step 4: `_check_german_compliance()` fonksiyonunu `quality_agent.py`'a ekle**

`agents/quality_agent.py` dosyasının en üstündeki import'lara `requests` zaten var. Dosyada `analyze_website` fonksiyonunun hemen ÜZERİNE şu fonksiyonu ekle:

```python
def _check_german_compliance(soup: BeautifulSoup, response_text: str, url: str) -> tuple[list[str], int]:
    """Alman hukuki/erişilebilirlik/SEO uyum kontrolleri.
    Returns (issues, score_deduction)."""
    issues: list[str] = []
    deduction = 0
    text_lower = response_text.lower()

    # ── Kategori 1: Hukuki zorunluluklar ──────────────────────────────────

    impressum_tag = (
        soup.find("a", string=re.compile(r"impressum", re.I)) or
        soup.find("a", href=re.compile(r"impressum", re.I))
    )
    if not impressum_tag:
        issues.append("[HUKUK] Impressum yok (§5 TMG ihlali)")
        deduction += 25
    else:
        raw_href = impressum_tag.get("href", "")
        if raw_href:
            imp_url = raw_href if raw_href.startswith("http") else url.rstrip("/") + "/" + raw_href.lstrip("/")
            try:
                resp = requests.get(imp_url, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                }, timeout=8, allow_redirects=True)
                imp_text = resp.text
                found = sum([
                    bool(re.search(r'\b\d{5}\b', imp_text)),
                    bool(re.search(r'\+49[\s\-]?|\b0\d{2,5}[\s\-/]\d{3,}', imp_text)),
                    bool(re.search(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', imp_text)),
                    bool(re.search(r'inhaber|geschäftsführer|verantwortlich|vertretungsberechtig', imp_text, re.I)),
                ])
                if found < 2:
                    issues.append("[HUKUK] Impressum içeriği yetersiz (adres/telefon/email eksik)")
                    deduction += 15
            except Exception:
                pass

    datenschutz_tag = (
        soup.find("a", string=re.compile(r"datenschutz", re.I)) or
        soup.find("a", href=re.compile(r"datenschutz|privacy", re.I))
    )
    if not datenschutz_tag and "datenschutz" not in text_lower and "privacy" not in text_lower:
        issues.append("[HUKUK] Datenschutzerklärung yok (DSGVO — €20M'a kadar ceza riski)")
        deduction += 25
    elif datenschutz_tag:
        if not any(kw in text_lower for kw in ("art. 6", "art.6", "dsgvo", "betroffenenrechte", "gdpr")):
            issues.append("[HUKUK] Datenschutzerklärung DSGVO'ya göre güncellenmemiş")
            deduction += 10

    COOKIE_MANAGERS = [
        "cookiebot", "onetrust", "osano", "borlabs", "complianz",
        "usercentrics", "cookiefirst", "consentmanager", "cookieinformation",
    ]
    has_consent_mgr = any(cm in text_lower for cm in COOKIE_MANAGERS)
    has_cookie_text = "cookie" in text_lower
    if not has_consent_mgr and not has_cookie_text:
        issues.append("[HUKUK] Cookie onay mekanizması yok (TTDSG §25)")
        deduction += 10
    elif not has_consent_mgr and has_cookie_text:
        issues.append("[HUKUK] Uygun cookie onay yönetimi eksik (TTDSG §25)")
        deduction += 7

    # ── Kategori 2: Erişilebilirlik ───────────────────────────────────────

    html_tag = soup.find("html")
    if html_tag:
        lang = html_tag.get("lang", "").lower()
        if lang not in ("de", "de-de", "de-at", "de-ch", "de-li"):
            issues.append("[ERİŞİM] HTML dil bildirimi eksik (lang='de' yok)")
            deduction += 5

    imgs_without_alt = [img for img in soup.find_all("img") if not img.get("alt", "").strip()]
    if len(imgs_without_alt) > 3:
        issues.append(f"[ERİŞİM] {len(imgs_without_alt)} görselde alt metin yok (BITV 2.0)")
        deduction += 8

    h1_tags = soup.find_all("h1")
    if not h1_tags:
        issues.append("[ERİŞİM] H1 başlık yok (sayfa yapısı bozuk)")
        deduction += 5
    elif len(h1_tags) > 1:
        issues.append(f"[ERİŞİM] {len(h1_tags)} adet H1 var (başlık hiyerarşisi bozuk)")
        deduction += 5

    # ── Kategori 3: Yerel SEO ─────────────────────────────────────────────

    has_schema = (
        "schema.org" in response_text or
        ('"@type"' in response_text and '"@context"' in response_text)
    )
    if not has_schema:
        issues.append("[SEO] Schema.org LocalBusiness işaretlemesi yok")
        deduction += 5

    has_phone = bool(re.search(r'\+49[\s\-]?|\b0\d{2,5}[\s\-/]\d{3,}', response_text))
    if not has_phone:
        issues.append("[SEO] Telefon numarası bulunamadı")
        deduction += 5

    return issues, deduction
```

- [ ] **Step 5: `analyze_website()` fonksiyonunu güncelle — uyum kontrolü ve soup/text return**

`agents/quality_agent.py` içindeki `analyze_website` fonksiyonunu şu hale getir:

```python
def analyze_website(url: str) -> dict:
    if not url:
        return {"score": 0, "issues": ["Website yok"], "load_time": None, "title": "", "soup": None, "response_text": ""}

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    issues = []
    score = 100
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        start = time.time()
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        load_time = round(time.time() - start, 2)

        if not response.url.startswith("https://"):
            issues.append("HTTPS yok")
            score -= 20

        if load_time > 5:
            issues.append(f"Yavaş yükleme ({load_time}s)")
            score -= 15

        soup = BeautifulSoup(response.text, "lxml")

        if not soup.find("meta", attrs={"name": "viewport"}):
            issues.append("Mobil uyumsuz")
            score -= 20

        meta_desc = soup.find("meta", attrs={"name": "description"})
        if not meta_desc or not meta_desc.get("content", "").strip():
            issues.append("Meta description yok")
            score -= 10

        body_text = soup.get_text()
        years = re.findall(r"©\s*(\d{4})|[Cc]opyright\s*(\d{4})", body_text)
        if years:
            flat = [int(a or b) for a, b in years]
            if max(flat) < 2020:
                issues.append(f"Eski site (©{max(flat)})")
                score -= 15

        if len(soup.find_all("table")) > 3:
            issues.append("Tablo tabanlı tasarım")
            score -= 10

        social = ["facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com"]
        if not any(s in response.text.lower() for s in social):
            issues.append("Sosyal medya linki yok")
            score -= 5

        if not soup.find("link", rel=lambda r: r and "icon" in r):
            issues.append("Favicon yok")
            score -= 5

        # Alman uyum kontrolü
        compliance_issues, compliance_deduction = _check_german_compliance(soup, response.text, response.url)
        issues.extend(compliance_issues)
        score -= compliance_deduction

        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        return {
            "url":           response.url,
            "score":         max(0, score),
            "issues":        issues,
            "load_time":     load_time,
            "title":         title,
            "soup":          soup,
            "response_text": response.text,
        }

    except requests.exceptions.SSLError:
        return {"url": url, "score": max(0, score - 20), "issues": issues + ["SSL hatası"],
                "load_time": None, "title": "", "soup": None, "response_text": ""}
    except requests.exceptions.ConnectionError:
        return {"url": url, "score": 0, "issues": ["Siteye ulaşılamıyor"],
                "load_time": None, "title": "", "soup": None, "response_text": ""}
    except Exception as e:
        return {"url": url, "score": 0, "issues": [f"Hata: {str(e)[:60]}"],
                "load_time": None, "title": "", "soup": None, "response_text": ""}
```

- [ ] **Step 6: Testleri çalıştır**

```bash
pytest tests/test_quality_agent.py -v -k "not analyze_website_returns"
```

Beklenen: `_check_german_compliance` testlerinin tümü `PASSED`.

```bash
pytest tests/test_quality_agent.py -v
```

Beklenen: Tüm testler `PASSED` (requests-mock yüklüyse).

- [ ] **Step 7: Commit**

```bash
git add agents/quality_agent.py tests/test_quality_agent.py
git commit -m "feat: quality_agent — Alman uyum kontrolü (§5 TMG, DSGVO, TTDSG, BITV, SEO)"
```

---

## Task 4: runner.py — Contact Agent + Page Title Entegrasyonu

**Files:**
- Modify: `runner.py`

- [ ] **Step 1: Import'ları güncelle**

`runner.py`'ın en üstündeki import bloğunu güncelle — `db` import satırına `update_lead_contact_email` ekle:

```python
from db import (update_campaign, save_leads, get_setting,
                get_leads, update_lead_email, update_lead_score,
                get_campaign, get_offering, save_outreach_message,
                get_last_outreach_step, update_lead_contact_email)
```

Aşağıya da şu satırı ekle:

```python
from agents.contact_agent import extract_emails
```

- [ ] **Step 2: `analyze_campaign_leads`'deki analiz döngüsünü güncelle**

`runner.py` içindeki `analyze_campaign_leads` fonksiyonunda, şu satırı bul:

```python
            result = analyze_website(lead["website"])
            update_lead_score(lead["id"], result["score"], result["issues"],
                              priority_label(result["score"]))
```

Şu şekilde değiştir:

```python
            result = analyze_website(lead["website"])
            update_lead_score(
                lead["id"], result["score"], result["issues"],
                priority_label(result["score"]),
                page_title=result.get("title", ""),
            )

            own_email = get_setting("gmail_user", "")
            emails = extract_emails(
                result.get("soup"),
                result.get("response_text", ""),
                result.get("url", lead["website"]),
                own_email=own_email,
            )
            if emails and not lead["email"]:
                update_lead_contact_email(lead["id"], emails[0])
                logger.info("Email bulundu: %s ← %s", lead["name"], emails[0])
```

- [ ] **Step 3: `run_campaign`'deki analiz döngüsünü de güncelle**

`runner.py` içindeki `run_campaign` fonksiyonunda, şu satırı bul (leads döngüsü içinde):

```python
            result = analyze_website(url)
            update_lead_score(lead["id"], result["score"], result["issues"],
                              priority_label(result["score"]))
```

Şu şekilde değiştir:

```python
            result = analyze_website(url)
            update_lead_score(
                lead["id"], result["score"], result["issues"],
                priority_label(result["score"]),
                page_title=result.get("title", ""),
            )

            own_email = get_setting("gmail_user", "")
            emails = extract_emails(
                result.get("soup"),
                result.get("response_text", ""),
                result.get("url", url),
                own_email=own_email,
            )
            if emails and not lead["email"]:
                update_lead_contact_email(lead["id"], emails[0])
                logger.info("Email bulundu: %s ← %s", lead["name"], emails[0])
```

- [ ] **Step 4: Import hatası olmadığını doğrula**

```bash
cd /Users/furkandenizalbaylar/Desktop/ActiveProjects/LeadGenPro
source venv/bin/activate
python -c "from runner import run_campaign, analyze_campaign_leads; print('OK')"
```

Beklenen: `OK`

- [ ] **Step 5: Commit**

```bash
git add runner.py
git commit -m "feat(runner): contact_agent ve page_title entegrasyonu"
```

---

## Task 5: email_agent.py — Model, Prompt, Sequence Step

**Files:**
- Modify: `agents/email_agent.py`

- [ ] **Step 1: `_SYSTEM_TEMPLATE`'i değiştir**

`agents/email_agent.py` dosyasında `_SYSTEM_TEMPLATE` değişkenini şu şekilde değiştir:

```python
_SYSTEM_TEMPLATE = """You are writing a B2B cold outreach email on behalf of {sender_name}{sender_title_suffix}.

WHAT THE SENDER OFFERS:
{offering_description}

PITCH SUMMARY:
{pitch_summary}

WRITING STYLE — MANDATORY:
- German part: formal "Sie" form throughout. Prefer concrete Zahlen/Fakten over vague promises.
  No marketing-speak (keine Formulierungen wie "innovative Lösung" oder "maßgeschneidert").
  Betreff: max 8 Wörter, kein Ausrufezeichen.
  Never start with "Ich hoffe", "Ich schreibe Ihnen wegen" or "Ich wende mich an Sie".
  Opening sentence: reference ONE specific thing about their website or sector.
  Max 3 sentences before the value proposition. Max 3 bullet points if used.
- English part: same peer-to-peer tone — concrete, no hype.

{step_instructions}

Write the email in TWO parts: first the full German version, then the full English version.
Each part: {word_target}. Personalize using the prospect details. Do NOT invent facts.

Output EXACTLY in this format (no extra commentary, no markdown headers):

GERMAN:
Betreff: <short subject line in German>

<full German body, signed with sender name + title + email>

---

ENGLISH:
Subject: <short subject line in English>

<full English body, signed with sender name + title + email>"""

_STEP_INSTRUCTIONS = {
    1: "This is the FIRST outreach. Lead with ONE specific observation about their website or compliance issues, then your value proposition.",
    2: "This is a SHORT follow-up. Acknowledge you wrote approximately one week ago. Ask if they had a chance to review your proposal. Be brief and friendly.",
    3: "This is a FINAL break-up email. Very short and gracious — if no interest, that is perfectly fine. Do NOT sell.",
}

_WORD_TARGETS = {
    1: "130-200 words per language",
    2: "60-80 words per language",
    3: "40-60 words per language",
}
```

- [ ] **Step 2: `_build_system_prompt()` imzasına `sequence_step` ekle**

```python
def _build_system_prompt(offering: dict | None, sender: dict | None, sequence_step: int = 1) -> str:
    sender = sender or {}
    name  = sender.get("name") or "the sender"
    title = sender.get("title") or ""
    email = sender.get("email") or ""

    title_suffix = f", {title}" if title else ""

    if offering:
        desc  = offering.get("description") or "B2B software/consulting services."
        pitch_de = (offering.get("pitch_de") or "").strip()
        pitch_en = (offering.get("pitch_en") or "").strip()
        pitch_summary = ""
        if pitch_de:
            pitch_summary += f"[DE pitch reference]\n{pitch_de}\n\n"
        if pitch_en:
            pitch_summary += f"[EN pitch reference]\n{pitch_en}\n"
        if not pitch_summary:
            pitch_summary = "(no detailed pitch provided — derive from description)"
    else:
        desc = "B2B software, consulting and AI services."
        pitch_summary = "(no pitch provided)"

    prompt = _SYSTEM_TEMPLATE.format(
        sender_name=name,
        sender_title_suffix=title_suffix,
        offering_description=desc,
        pitch_summary=pitch_summary,
        step_instructions=_STEP_INSTRUCTIONS.get(sequence_step, _STEP_INSTRUCTIONS[1]),
        word_target=_WORD_TARGETS.get(sequence_step, _WORD_TARGETS[1]),
    )
    prompt += (
        f"\n\nSIGNATURE BLOCK (use exactly this at the end of each language body):\n"
        f"{name}\n"
        + (f"{title}\n" if title else "")
        + (f"{email}\n" if email else "")
    )
    return prompt
```

- [ ] **Step 3: `generate_outreach()` imzasını ve user_content'i güncelle**

`generate_outreach` fonksiyonunu şu şekilde değiştir:

```python
def generate_outreach(prospect: dict, offering: dict | None,
                      client: anthropic.Anthropic,
                      sender: dict | None = None,
                      model: str = "claude-sonnet-4-6",
                      sequence_step: int = 1) -> dict:
    """B2B outreach üret. Returns {'full': str}."""
    name        = prospect.get("name", "")
    sector      = prospect.get("sector", "")
    website     = prospect.get("website", "")
    page_title  = prospect.get("page_title", "")
    fit_reasons = prospect.get("fit_reasons", "") or prospect.get("issues", "")
    if isinstance(fit_reasons, list):
        fit_reasons = ", ".join(fit_reasons)
    signals   = prospect.get("signals", "")
    fit_score = prospect.get("fit_score") or prospect.get("quality_score") or "?"
    issues    = prospect.get("issues", "")

    user_content = (
        f"PROSPECT COMPANY:\n"
        f"  Name: {name}\n"
        f"  Sector: {sector}\n"
        f"  Website: {website or '—'}\n"
        f"  Website title: {page_title or '—'}\n"
        f"  Fit score (offering ↔ company): {fit_score}/100\n"
        f"  Why this is a fit: {fit_reasons or '—'}\n"
        f"  Detected signals: {signals or '—'}\n"
        f"  Website issues: {issues or '—'}\n"
    )
    msg = client.messages.create(
        model=model,
        max_tokens=900,
        system=_build_system_prompt(offering, sender, sequence_step=sequence_step),
        messages=[{"role": "user", "content": user_content}],
    )
    full = msg.content[0].text.strip()
    return {"full": full}
```

- [ ] **Step 4: `_parse_subject` public yap, `send_email()` imzasına `extra_headers` ekle**

`agents/email_agent.py` dosyasındaki `_parse_subject` fonksiyonunu ve `send_email` fonksiyonunu tamamen şu şekilde değiştir:

```python
def parse_subject(full_email: str) -> str:
    for line in full_email.splitlines():
        s = line.strip()
        if s.startswith("Betreff:"):
            return s.replace("Betreff:", "").strip()
        if s.startswith("Subject:"):
            return s.replace("Subject:", "").strip()
    return "Quick question"


def send_email(to_email: str, business_name: str, full_email: str,
               gmail_user: str = "", gmail_pass: str = "",
               sender_name: str = "",
               extra_headers: dict | None = None) -> bool:
    """Gmail SMTP üzerinden iki dilli outreach gönder."""
    subject = parse_subject(full_email) or f"Re: {business_name}"
    german_block, english_block = _split_de_en(full_email)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    display_name = sender_name or gmail_user
    msg["From"] = f"{display_name} <{gmail_user}>"
    msg["To"]   = to_email

    if extra_headers:
        for key, val in extra_headers.items():
            msg[key] = val

    plain = full_email

    de_safe = "<br>".join(html.escape(line) for line in german_block.splitlines())
    en_safe = "<br>".join(html.escape(line) for line in english_block.splitlines())

    html_body = f"""<div style="font-family:Arial,sans-serif;max-width:620px;color:#1a1a1a;line-height:1.7;font-size:15px">
  <div style="margin-bottom:32px">
    {de_safe}
  </div>
  {'<hr style="border:none;border-top:2px solid #e0e0e0;margin:32px 0">' if en_safe else ''}
  <div>
    {en_safe}
  </div>
</div>"""

    msg.attach(MIMEText(plain,     "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html",  "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, to_email, msg.as_string())
        return True
    except smtplib.SMTPException as e:
        logger.error("SMTP hatası (%s): %s", to_email, e)
        return False
```

> **Not:** `runner.py` içindeki `_parse_subject` import'u yoktu (private fonksiyondu), `followup_runner.py` `parse_subject`'i import edecek — Task 6'da bu zaten yazıldı.

- [ ] **Step 5: Import hatası olmadığını doğrula**

```bash
python -c "from agents.email_agent import generate_outreach, send_email, parse_subject; print('OK')"
```

Beklenen: `OK`

- [ ] **Step 6: Commit**

```bash
git add agents/email_agent.py
git commit -m "feat(email_agent): Sonnet 4.6, gelişmiş Almanca prompt, sequence_step, extra_headers"
```

---

## Task 6: followup_runner.py

**Files:**
- Create: `tests/test_followup_runner.py`
- Create: `followup_runner.py`

- [ ] **Step 1: Testleri yaz**

`tests/test_followup_runner.py`:

```python
from datetime import datetime, timedelta
import pytest


def _insert_campaign(db_path, offering_id=1):
    import db as db_module
    return db_module.create_campaign("berlin", ["restaurant"], offering_id=offering_id)


def _insert_lead(db_path, cid, email="test@restaurant.de"):
    import sqlite3
    with sqlite3.connect(db_path) as c:
        cur = c.execute(
            "INSERT INTO leads(campaign_id, name, sector, website, email, email_sent) "
            "VALUES(?,?,?,?,?,?)",
            (cid, "Test Restaurant", "restaurant", "https://test.de", email, "Evet"),
        )
        return cur.lastrowid


def _insert_offering(db_path):
    import db as db_module
    return db_module.create_offering("Test Offering", description="Test")


def _insert_outreach(db_path, lead_id, cid, step, sent_at_offset_days=0, status="sent"):
    import sqlite3
    sent_at = (datetime.now() - timedelta(days=sent_at_offset_days)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO outreach_messages(lead_id, campaign_id, sequence_step, sent_at, status, subject, body) "
            "VALUES(?,?,?,?,?,?,?)",
            (lead_id, cid, step, sent_at, status, "Test Subject", "Test Body"),
        )


# ── get_followup_candidates ──────────────────────────────────────────────────

def test_step2_candidate_after_7_days(test_db):
    oid = _insert_offering(test_db)
    cid = _insert_campaign(test_db, offering_id=oid)
    lid = _insert_lead(test_db, cid)
    _insert_outreach(test_db, lid, cid, step=1, sent_at_offset_days=8)

    from followup_runner import get_followup_candidates
    result = get_followup_candidates(cid, step=2, days_wait=7)
    assert len(result) == 1
    assert result[0]["id"] == lid


def test_step2_not_ready_before_7_days(test_db):
    oid = _insert_offering(test_db)
    cid = _insert_campaign(test_db, offering_id=oid)
    lid = _insert_lead(test_db, cid)
    _insert_outreach(test_db, lid, cid, step=1, sent_at_offset_days=3)

    from followup_runner import get_followup_candidates
    result = get_followup_candidates(cid, step=2, days_wait=7)
    assert result == []


def test_excludes_replied_lead(test_db):
    import sqlite3
    oid = _insert_offering(test_db)
    cid = _insert_campaign(test_db, offering_id=oid)
    lid = _insert_lead(test_db, cid)
    _insert_outreach(test_db, lid, cid, step=1, sent_at_offset_days=8)
    with sqlite3.connect(test_db) as c:
        c.execute("UPDATE leads SET reply_received=1 WHERE id=?", (lid,))

    from followup_runner import get_followup_candidates
    result = get_followup_candidates(cid, step=2, days_wait=7)
    assert result == []


def test_excludes_already_sent_step2(test_db):
    oid = _insert_offering(test_db)
    cid = _insert_campaign(test_db, offering_id=oid)
    lid = _insert_lead(test_db, cid)
    _insert_outreach(test_db, lid, cid, step=1, sent_at_offset_days=10)
    _insert_outreach(test_db, lid, cid, step=2, sent_at_offset_days=3)

    from followup_runner import get_followup_candidates
    result = get_followup_candidates(cid, step=2, days_wait=7)
    assert result == []


def test_step3_candidate_after_14_days(test_db):
    oid = _insert_offering(test_db)
    cid = _insert_campaign(test_db, offering_id=oid)
    lid = _insert_lead(test_db, cid)
    _insert_outreach(test_db, lid, cid, step=1, sent_at_offset_days=15)
    _insert_outreach(test_db, lid, cid, step=2, sent_at_offset_days=8)

    from followup_runner import get_followup_candidates
    result = get_followup_candidates(cid, step=3, days_wait=14)
    assert len(result) == 1


# ── count_followup_ready ──────────────────────────────────────────────────────

def test_count_followup_ready(test_db):
    oid = _insert_offering(test_db)
    cid = _insert_campaign(test_db, offering_id=oid)
    lid1 = _insert_lead(test_db, cid, email="a@test.de")
    lid2 = _insert_lead(test_db, cid, email="b@test.de")
    _insert_outreach(test_db, lid1, cid, step=1, sent_at_offset_days=8)   # step2 hazır
    _insert_outreach(test_db, lid2, cid, step=1, sent_at_offset_days=15)  # step2 hazır
    _insert_outreach(test_db, lid2, cid, step=2, sent_at_offset_days=8)   # step3 hazır

    from followup_runner import count_followup_ready
    assert count_followup_ready(cid) == 3  # lid1:step2 + lid2:step2 + lid2:step3
```

- [ ] **Step 2: Testlerin başarısız olduğunu doğrula**

```bash
pytest tests/test_followup_runner.py -v 2>&1 | head -10
```

Beklenen: `ImportError` — `followup_runner` henüz yok.

- [ ] **Step 3: `followup_runner.py` dosyasını oluştur**

```python
"""Follow-up sequence runner — step-2 ve step-3 outreach dizisi."""
import logging
import time
from datetime import datetime, timedelta

import anthropic

from db import (conn, get_setting, get_campaign, get_offering,
                save_outreach_message, get_last_outreach_step)
from agents.email_agent import generate_outreach, send_email, parse_subject

logger = logging.getLogger(__name__)


def get_followup_candidates(cid: int, step: int, days_wait: int) -> list[dict]:
    """Step için hazır leadleri döndür (step-1 gönderilmiş, step-N yok, yanıt yok, süre geçmiş)."""
    cutoff = (datetime.now() - timedelta(days=days_wait)).strftime("%Y-%m-%d %H:%M:%S")
    with conn() as c:
        rows = c.execute("""
            SELECT l.id, l.name, l.email, l.sector, l.website,
                   l.quality_score, l.fit_score, l.fit_reasons, l.signals,
                   l.issues, l.page_title, l.reply_received,
                   om.sent_at AS first_sent_at,
                   om.subject  AS first_subject,
                   om.id       AS first_msg_id
            FROM leads l
            JOIN outreach_messages om
              ON om.lead_id = l.id AND om.sequence_step = 1 AND om.status = 'sent'
            WHERE l.campaign_id = ?
              AND l.reply_received = 0
              AND l.email IS NOT NULL AND l.email != ''
              AND om.sent_at < ?
              AND NOT EXISTS (
                  SELECT 1 FROM outreach_messages om2
                  WHERE om2.lead_id = l.id AND om2.sequence_step = ?
              )
        """, (cid, cutoff, step)).fetchall()
    return [dict(r) for r in rows]


def count_followup_ready(cid: int) -> int:
    """UI badge için: kaç lead herhangi bir follow-up adımına hazır."""
    return (
        len(get_followup_candidates(cid, step=2, days_wait=7)) +
        len(get_followup_candidates(cid, step=3, days_wait=14))
    )


def _load_sender() -> dict:
    return {
        "name":  get_setting("sender_name") or get_setting("gmail_user", "") or "Sender",
        "title": get_setting("sender_title", ""),
        "email": get_setting("sender_email") or get_setting("gmail_user", ""),
    }


def send_followup_sequence(cid: int) -> dict:
    """Kampanya için step-2 ve step-3 follow-up'ları gönder."""
    gmail_user = get_setting("gmail_user")
    gmail_pass = get_setting("gmail_password")
    if not gmail_user or not gmail_pass:
        logger.error("send_followup_sequence: Gmail ayarları eksik")
        return {"error": "Gmail ayarları eksik"}

    api_key = get_setting("anthropic_api_key")
    if not api_key:
        return {"error": "Anthropic API key ayarlanmamış"}

    campaign = get_campaign(cid)
    if not campaign:
        return {"error": "Kampanya bulunamadı"}

    offering = get_offering(campaign["offering_id"]) if campaign["offering_id"] else None
    if not offering:
        return {"error": "Offering bulunamadı — Ayarlar'dan teklif ekle"}

    client = anthropic.Anthropic(api_key=api_key)
    sender = _load_sender()
    offering_dict = dict(offering)

    step2_targets = get_followup_candidates(cid, step=2, days_wait=7)
    step3_targets = get_followup_candidates(cid, step=3, days_wait=14)

    sent2, sent3, errors = 0, 0, 0

    for step_num, targets in [(2, step2_targets), (3, step3_targets)]:
        for lead in targets:
            prospect = {
                "name":          lead["name"],
                "sector":        lead["sector"],
                "website":       lead.get("website", ""),
                "quality_score": lead.get("quality_score"),
                "fit_score":     lead.get("fit_score"),
                "fit_reasons":   lead.get("fit_reasons") or lead.get("issues") or "",
                "signals":       lead.get("signals") or "",
                "issues":        lead.get("issues") or "",
                "page_title":    lead.get("page_title") or "",
            }
            try:
                result = generate_outreach(
                    prospect, offering_dict, client,
                    sender=sender, sequence_step=step_num,
                )
                full_email = (result or {}).get("full", "")
                if not full_email:
                    raise ValueError("generate_outreach boş yanıt döndürdü")

                extra_headers: dict[str, str] = {}
                if lead.get("first_subject"):
                    msg_id = f"<campaign{cid}_lead{lead['id']}_step1@leadgenpro>"
                    extra_headers["In-Reply-To"] = msg_id
                    extra_headers["References"]  = msg_id

                success = send_email(
                    to_email=lead["email"],
                    business_name=lead["name"],
                    full_email=full_email,
                    gmail_user=gmail_user,
                    gmail_pass=gmail_pass,
                    sender_name=sender.get("name", ""),
                    extra_headers=extra_headers,
                )

                save_outreach_message(
                    lead_id=lead["id"],
                    campaign_id=cid,
                    offering_id=offering_dict.get("id"),
                    sequence_step=step_num,
                    subject=parse_subject(full_email)[:300],
                    body=full_email,
                    status="sent" if success else "failed",
                    error="" if success else "SMTP fail",
                )

                if success:
                    if step_num == 2:
                        sent2 += 1
                    else:
                        sent3 += 1
                    logger.info("Follow-up step-%d gönderildi: %s <%s>",
                                step_num, lead["name"], lead["email"])
                else:
                    errors += 1

            except Exception:
                logger.exception("Follow-up hatası — lead id=%d step=%d", lead["id"], step_num)
                save_outreach_message(
                    lead_id=lead["id"], campaign_id=cid,
                    offering_id=offering_dict.get("id"),
                    sequence_step=step_num,
                    subject="", body="", status="failed",
                    error="exception",
                )
                errors += 1

            time.sleep(3)

    logger.info("Follow-up özeti — kampanya %d: step2=%d, step3=%d, hata=%d",
                cid, sent2, sent3, errors)
    return {"step2_sent": sent2, "step3_sent": sent3, "errors": errors}
```

- [ ] **Step 4: Testleri çalıştır**

```bash
pytest tests/test_followup_runner.py -v
```

Beklenen: Tüm testler `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add followup_runner.py tests/test_followup_runner.py
git commit -m "feat: followup_runner — step-2/3 outreach dizisi"
```

---

## Task 7: app.py + campaign.html

**Files:**
- Modify: `app.py`
- Modify: `templates/campaign.html`

- [ ] **Step 1: `followup_runner` import'larını `app.py`'a ekle**

`app.py`'ın `runner` import satırının altına ekle:

```python
from followup_runner import send_followup_sequence, count_followup_ready
```

- [ ] **Step 2: Yeni endpoint'i `app.py`'a ekle**

`/campaign/{cid}/check-replies` endpoint'inin hemen altına ekle:

```python
@app.post("/campaign/{cid}/send-followups")
async def send_followups_endpoint(cid: int, background_tasks: BackgroundTasks):
    campaign = get_campaign(cid)
    if not campaign:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    if campaign["status"] not in ("completed", "stopped"):
        raise HTTPException(status_code=400, detail="Kampanya henüz tamamlanmadı")
    background_tasks.add_task(send_followup_sequence, cid)
    return JSONResponse({"ok": True})


@app.get("/campaign/{cid}/followup-count")
async def followup_count_endpoint(cid: int):
    campaign = get_campaign(cid)
    if not campaign:
        return JSONResponse({"count": 0})
    return JSONResponse({"count": count_followup_ready(cid)})
```

- [ ] **Step 3: `campaign_page` route'unu `followup_count` ile güncelle**

`app.py`'daki `/campaign/{cid}` GET endpoint'ini bul. `context` dict'ine şu satırı ekle:

```python
            "followup_count": count_followup_ready(cid),
```

Güncel hali:

```python
    return templates.TemplateResponse(
        request=request, name="campaign.html",
        context={
            "campaign":       dict(campaign),
            "leads":          [dict(l) for l in leads],
            "can_analyze":    can_analyze,
            "offering":       dict(offering) if offering else None,
            "followup_count": count_followup_ready(cid),
        }
    )
```

- [ ] **Step 4: `campaign.html`'e Follow-up butonu ekle**

`templates/campaign.html` dosyasını aç. "E-posta Gönder" butonunu bul (genellikle `send-emails` POST isteği yapan buton). Hemen yanına şu butonu ekle:

```html
<button
  id="btnFollowup"
  onclick="sendFollowups()"
  class="px-4 py-2 text-sm font-medium rounded-lg bg-violet-600 hover:bg-violet-500 text-white transition-colors disabled:opacity-50"
  {% if followup_count == 0 %}disabled{% endif %}
>
  Follow-up Gönder
  {% if followup_count > 0 %}
    <span class="ml-1 bg-white/20 text-white text-xs font-bold px-1.5 py-0.5 rounded-full">
      {{ followup_count }}
    </span>
  {% endif %}
</button>
```

Ve `<script>` bloğuna (ya da mevcut script'in içine) şu fonksiyonu ekle:

```javascript
async function sendFollowups() {
  const btn = document.getElementById('btnFollowup');
  btn.disabled = true;
  btn.textContent = 'Gönderiliyor...';
  try {
    const res = await fetch(`/campaign/{{ campaign.id }}/send-followups`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      btn.textContent = 'Gönderildi ✓';
    } else {
      btn.textContent = 'Hata';
      btn.disabled = false;
    }
  } catch {
    btn.textContent = 'Hata';
    btn.disabled = false;
  }
}
```

- [ ] **Step 5: Sunucunun başladığını doğrula**

```bash
cd /Users/furkandenizalbaylar/Desktop/ActiveProjects/LeadGenPro
source venv/bin/activate
python -c "import app; print('OK')"
```

Beklenen: `OK`

- [ ] **Step 6: Tüm testleri çalıştır**

```bash
pytest tests/ -v
```

Beklenen: Tüm testler `PASSED`.

- [ ] **Step 7: Final commit**

```bash
git add app.py templates/campaign.html
git commit -m "feat: follow-up endpoint ve campaign.html butonu"
```

---

## Özet — Tamamlanan Değişiklikler

| Bileşen | Sonuç |
|---|---|
| `db.py` | `page_title` migration, `update_lead_contact_email`, güncel `update_lead_score` |
| `agents/contact_agent.py` | Website HTML'inden email çıkarma (mailto + regex + link takibi) |
| `agents/quality_agent.py` | 12 Alman uyum kontrolü, `analyze_website` soup+text döndürüyor |
| `runner.py` | Analiz sonrası otomatik email kazıma + page_title kaydı |
| `agents/email_agent.py` | Sonnet 4.6, gelişmiş Almanca prompt, sequence_step, extra_headers |
| `followup_runner.py` | Step-2 (7 gün) ve step-3 (14 gün) follow-up dizisi |
| `app.py` | `/send-followups` ve `/followup-count` endpoint'leri |
| `templates/campaign.html` | "Follow-up Gönder (N hazır)" butonu |
