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
