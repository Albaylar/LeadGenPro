import logging
import re
import time
import warnings
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
logger = logging.getLogger(__name__)


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
            imp_url = raw_href if raw_href.startswith("http") else urljoin(url, raw_href)
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
                resp = requests.get(imp_url, headers=headers, timeout=8, allow_redirects=True)
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
            except requests.RequestException as e:
                logger.debug("Impressum sayfası alınamadı (%s): %s", imp_url, e)

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
        issues.append("[HUKUK] Uygun Cookie onay yönetimi eksik (TTDSG §25)")
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
        logger.exception("analyze_website beklenmedik hata: %s", url)
        return {"url": url, "score": 0, "issues": [f"Hata: {str(e)[:60]}"],
                "load_time": None, "title": "", "soup": None, "response_text": ""}


def analyze_all(businesses: list[dict]) -> list[dict]:
    total = len(businesses)
    for i, biz in enumerate(businesses):
        url = biz.get("website")
        if url:
            logger.info("[%d/%d] %s", i + 1, total, biz.get("name", url)[:40])
            result = analyze_website(url)
            biz["quality_score"] = result["score"]
            biz["issues"] = result["issues"]
            biz["load_time"] = result["load_time"]
            biz["page_title"] = result["title"]
            time.sleep(0.5)
        else:
            biz["quality_score"] = 0
            biz["issues"] = ["Website yok"]
            biz["load_time"] = None
            biz["page_title"] = ""

    return businesses
