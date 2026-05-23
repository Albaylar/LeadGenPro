import logging
import time
import re

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

logger = logging.getLogger(__name__)

# Gelbe Seiten URL slugları — şehir ana + ilçeler
CITY_DISTRICTS = {
    "berlin": [
        "berlin", "berlin-mitte", "berlin-charlottenburg",
        "berlin-neukoelln", "berlin-prenzlauer-berg",
        "berlin-friedrichshain", "berlin-kreuzberg",
        "berlin-schoeneberg", "berlin-wedding", "berlin-tempelhof",
        "berlin-pankow", "berlin-spandau",
    ],
    "hamburg": [
        "hamburg", "hamburg-altona", "hamburg-eimsbuettel",
        "hamburg-wandsbek", "hamburg-barmbek", "hamburg-harburg",
        "hamburg-nord",
    ],
    "muenchen": [
        "muenchen", "muenchen-schwabing", "muenchen-maxvorstadt",
        "muenchen-neuhausen", "muenchen-bogenhausen",
        "muenchen-haidhausen", "muenchen-sendling",
    ],
    "frankfurt": [
        "frankfurt-am-main", "frankfurt-am-main-sachsenhausen",
        "frankfurt-am-main-bornheim", "frankfurt-am-main-nordend",
        "frankfurt-am-main-bockenheim",
    ],
    "koeln": [
        "koeln", "koeln-innenstadt", "koeln-ehrenfeld",
        "koeln-nippes", "koeln-sued", "koeln-lindenthal",
    ],
    "duesseldorf": [
        "duesseldorf", "duesseldorf-altstadt", "duesseldorf-pempelfort",
        "duesseldorf-bilk", "duesseldorf-flingern",
    ],
    "stuttgart": [
        "stuttgart", "stuttgart-mitte", "stuttgart-west",
        "stuttgart-nord", "stuttgart-sued",
    ],
    "leipzig": [
        "leipzig", "leipzig-mitte", "leipzig-gohlis",
        "leipzig-connewitz", "leipzig-schleussig",
    ],
    "dresden": [
        "dresden", "dresden-altstadt", "dresden-neustadt",
        "dresden-blasewitz", "dresden-loschwitz",
    ],
    "nuernberg": [
        "nuernberg", "nuernberg-mitte", "nuernberg-sued",
        "nuernberg-nord", "nuernberg-west",
    ],
}

# Gelbe Seiten slug → DDG'de kullanılacak gerçek şehir adı
CITY_SEARCH_NAME = {
    "berlin":      "Berlin",
    "hamburg":     "Hamburg",
    "muenchen":    "München",
    "frankfurt":   "Frankfurt",
    "koeln":       "Köln",
    "duesseldorf": "Düsseldorf",
    "stuttgart":   "Stuttgart",
    "leipzig":     "Leipzig",
    "dresden":     "Dresden",
    "nuernberg":   "Nürnberg",
}

# DuckDuckGo çoklu query şablonları — her biri farklı sonuç seti getirir
DDG_QUERY_VARIANTS = [
    '"{sector}" "{city}" site:*.de',
    '"{sector}" "{city}" Praxis site:*.de',
    '"{sector}" "{city}" Öffnungszeiten site:*.de',
    '"{sector}" "{city}" Bewertung site:*.de',
]

# Ev ofisi / freiberufler modu için özel sorgular
DDG_HOME_OFFICE_VARIANTS = [
    '"{sector}" "{city}" freiberuflich site:*.de',
    '"{sector}" "{city}" selbstständig site:*.de',
    '"{sector}" "{city}" Privatpraxis site:*.de',
    '"{sector}" "{city}" Heimarbeit site:*.de',
    '"{sector}" "{city}" Kleinunternehmen site:*.de',
]

SKIP_DOMAINS = [
    "gelbeseiten", "yelp", "facebook", "instagram", "maps.google",
    "wikipedia", "tripadvisor", "linkedin", "twitter", "xing",
    "meinestadt", "dasoertliche", "11880", "golocal", "cylex",
    "wlw", "schauinsland", "google.de", "bing.com",
    "jameda", "doctolib", "sanego", "kununu", "indeed",
    # Şehir portalleri & genel dizinler
    "berlin.de", "hamburg.de", "muenchen.de", "frankfurt.de",
    "koeln.de", "duesseldorf.de", "stuttgart.de", "leipzig.de",
    "arzt-auskunft", "arztsuche", "arztfinden", "doccheck",
    "healtheon", "practicle", "zocdoc", "find-a-doctor",
    ".agency", ".marketing", ".ads", "seo-agentur",
]

# Sahte isim olduğunu gösteren tek kelimeler
GENERIC_TITLES = {
    "behandlung", "kontakt", "impressum", "datenschutz", "startseite",
    "home", "leistungen", "team", "praxis", "über uns", "about",
    "willkommen", "news", "aktuelles", "blog", "galerie", "anfahrt",
}


def _extract_gelbe_page(soup, sector: str, seen_names: set) -> list[dict]:
    listings = soup.find_all("article", class_=re.compile(r"mod-Treffer"))
    results = []
    for listing in listings:
        name_tag = listing.find("p", class_=re.compile("mod-Treffer__name")) or listing.find("h2")
        website_tag = listing.find("a", href=re.compile(r"^https?://(?!.*gelbeseiten)"))
        email_tag = listing.find("a", href=re.compile(r"^mailto:"))
        phone_tag = listing.find("a", href=re.compile(r"^tel:"))

        name = name_tag.get_text(strip=True) if name_tag else ""
        if not name or name in seen_names:
            continue

        seen_names.add(name)
        results.append({
            "name": name,
            "website": website_tag.get("href") if website_tag else None,
            "email": email_tag.get("href", "").replace("mailto:", "") if email_tag else None,
            "phone": phone_tag.get("href", "").replace("tel:", "") if phone_tag else None,
            "sector": sector,
            "source": "gelbe_seiten",
        })
    return results


def _get_with_retry(url: str, headers: dict, max_retries: int = 3) -> requests.Response | None:
    """Exponential backoff ile HTTP GET. 429/503 veya timeout'ta yeniden dener."""
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 429:
                wait = 2 ** attempt * 3
                logger.warning("Rate limit (429) — %ds bekleniyor (%s)", wait, url)
                time.sleep(wait)
                continue
            if resp.status_code == 503:
                wait = 2 ** attempt * 2
                logger.warning("503 Service Unavailable — %ds bekleniyor (%s)", wait, url)
                time.sleep(wait)
                continue
            return resp
        except requests.exceptions.Timeout:
            logger.warning("Timeout (deneme %d/%d): %s", attempt + 1, max_retries, url)
            time.sleep(2 ** attempt)
        except requests.exceptions.ConnectionError as e:
            logger.warning("Bağlantı hatası (deneme %d/%d): %s — %s", attempt + 1, max_retries, url, e)
            time.sleep(2 ** attempt)
    return None


def search_gelbe_seiten(sector: str, city: str, max_results: int = 150, max_pages: int = 3) -> list[dict]:
    city_key  = city.lower()
    locations = CITY_DISTRICTS.get(city_key, [city_key])

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    businesses: list[dict] = []
    seen_names: set[str]   = set()

    for location in locations:
        for page in range(1, max_pages + 1):
            url = f"https://www.gelbeseiten.de/suche/{sector}/{location}"
            if page > 1:
                url += f"?seite={page}"

            resp = _get_with_retry(url, headers)
            if resp is None:
                logger.warning("Gelbe Seiten erişilemiyor: %s/%s s.%d — atlanıyor", sector, location, page)
                break

            try:
                soup         = BeautifulSoup(resp.text, "lxml")
                page_results = _extract_gelbe_page(soup, sector, seen_names)
            except Exception as e:
                logger.exception("Gelbe Seiten parse hatası (%s/%s s.%d)", sector, location, page)
                break

            if not page_results:
                break

            businesses.extend(page_results)
            time.sleep(0.5)

            if len(businesses) >= max_results:
                return businesses[:max_results]

    return businesses


def _normalize_url(url: str) -> str:
    """URL'yi kök domain'e indir ve lowercase yap (dedup için)."""
    url = url.strip().rstrip("/")
    parts = url.split("//", 1)
    if len(parts) == 2:
        domain = parts[1].split("/", 1)[0].lower()
        return "https://" + domain
    return url.lower()


def _search_ddg(sector: str, city: str, query_variants: list[str],
                max_results: int, source_tag: str, sleep_sec: float = 1.0) -> list[dict]:
    """Ortak DuckDuckGo arama motoru — farklı sorgu setleriyle çalışır."""
    businesses: list[dict] = []
    seen_urls:  set[str]   = set()
    city_display = CITY_SEARCH_NAME.get(city.lower(), city.title())
    city_lower   = city_display.lower()

    for template in query_variants:
        if len(businesses) >= max_results:
            break
        query = template.format(sector=sector, city=city_display)
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(
                    query, region="de-de", safesearch="off", max_results=42
                ) or [])

            for r in results:
                url   = r.get("href", "").rstrip("/")
                title = r.get("title", "")
                body  = r.get("body", "")

                if not url or any(d in url for d in SKIP_DOMAINS):
                    continue

                norm = _normalize_url(url)
                if norm in seen_urls:
                    continue

                if not (city_lower in url.lower() or city_lower in title.lower()
                        or city_lower in body.lower()):
                    continue

                raw_name = title.split(" - ")[0].split(" | ")[0].strip()
                if (not raw_name or raw_name.lower() in GENERIC_TITLES
                        or raw_name.lower().startswith(
                            ("impressum", "kontakt", "datenschutz", "unser ", "ihre ", "our "))):
                    domain   = norm.split("//", 1)[-1].split("/")[0].replace("www.", "")
                    raw_name = domain.split(".")[0].replace("-", " ").title()
                if not raw_name:
                    continue

                seen_urls.add(norm)
                businesses.append({
                    "name":    raw_name,
                    "website": norm,
                    "email":   None,
                    "phone":   None,
                    "sector":  sector,
                    "source":  source_tag,
                })

                if len(businesses) >= max_results:
                    break

            time.sleep(sleep_sec)

        except Exception as e:
            logger.warning("DDG hatası [%s] (%s/%s): %s", source_tag, sector, city, e)

    return businesses


def search_duckduckgo(sector: str, city: str, max_results: int = 100) -> list[dict]:
    return _search_ddg(sector, city, DDG_QUERY_VARIANTS, max_results, "duckduckgo", 1.0)


def search_home_offices(sector: str, city: str, max_results: int = 60) -> list[dict]:
    return _search_ddg(sector, city, DDG_HOME_OFFICE_VARIANTS, max_results, "home_office_ddg", 1.5)


def research_berlin_businesses(sectors: list[str], max_per_sector: int = 50,
                                city: str = "berlin",
                                home_office: bool = False) -> list[dict]:
    all_businesses: list[dict] = []
    seen_keys:      set[str]   = set()

    for sector in sectors:
        logger.info("Araştırılıyor: %s / %s (ev ofisi=%s)", sector, city, home_office)

        gs_results  = search_gelbe_seiten(sector, city, max_results=max_per_sector * 3)
        time.sleep(1)
        ddg_results = search_duckduckgo(sector, city, max_results=max_per_sector * 2)
        time.sleep(1)

        ho_results: list[dict] = []
        if home_office:
            ho_results = search_home_offices(sector, city, max_results=max_per_sector)
            time.sleep(1)

        added = 0
        for biz in gs_results + ddg_results + ho_results:
            raw_url = biz.get("website") or ""
            key = _normalize_url(raw_url) if raw_url else biz.get("name", "").lower().strip()
            if key and key not in seen_keys:
                # Gelbe Seiten URL'lerini de normalize et
                if raw_url:
                    biz["website"] = _normalize_url(raw_url)
                seen_keys.add(key)
                all_businesses.append(biz)
                added += 1

        logger.info("%d yeni işletme (%s/%s) — toplam: %d", added, sector, city, len(all_businesses))

    return all_businesses
