import logging
import re
import time
import warnings

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
logger = logging.getLogger(__name__)


def analyze_website(url: str) -> dict:
    if not url:
        return {"score": 0, "issues": ["Website yok"], "load_time": None, "title": ""}

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

        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        return {
            "url": response.url,
            "score": max(0, score),
            "issues": issues,
            "load_time": load_time,
            "title": title,
        }

    except requests.exceptions.SSLError:
        return {"url": url, "score": max(0, score - 20), "issues": issues + ["SSL hatası"], "load_time": None, "title": ""}
    except requests.exceptions.ConnectionError:
        return {"url": url, "score": 0, "issues": ["Siteye ulaşılamıyor"], "load_time": None, "title": ""}
    except Exception as e:
        return {"url": url, "score": 0, "issues": [f"Hata: {str(e)[:60]}"], "load_time": None, "title": ""}


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
