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
        email = re.sub(r'^mailto:', '', href, flags=re.I).split("?")[0].strip()
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
