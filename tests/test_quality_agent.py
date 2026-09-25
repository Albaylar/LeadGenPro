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
    requests_mock.get(
        "https://example.de/impressum",
        text="Inhaber: Max Mustermann, 12345 Berlin, +49 30 123456, max@example.de",
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
