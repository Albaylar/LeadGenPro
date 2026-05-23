import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You are writing cold outreach emails on behalf of Furkan Deniz Albaylar, an iOS Developer & IT Consultant based in Berlin.

Write the email in TWO parts: first the full German version, then the full English version.
Fill in [placeholders] based on the business details provided. Tone: professional, confident, not salesy.

Output EXACTLY in this format (no extra commentary):

GERMAN:
Betreff: Optimierung & KI-Wachstum für [NAME]

Sehr geehrte Damen und Herren,

Ich bin auf die Website von [NAME] aufmerksam geworden und habe eine kurze Analyse Ihrer digitalen Präsenz durchgeführt. Als in Berlin ansässiger iOS Developer und IT Consultant helfe ich lokalen Unternehmen dabei, durch moderne Technologie und KI nachhaltig zu wachsen.

Was mir aufgefallen ist:

• Technisch: [TOP_ISSUE_DE] beeinträchtigt Ihre Glaubwürdigkeit und Ihr Google-Ranking.
• AI-SEO: Mit KI optimiere ich Ihre Website gezielt für lokalen Suchtrends – damit Sie vor Ihrer Konkurrenz gefunden werden.
• Mobile & App: [1 sentence tailored to SECTOR — specific benefit of a mobile app or optimized digital tool]

Mein Ziel: Ihre Online-Präsenz in ein automatisiertes Werkzeug zur Kundengewinnung verwandeln. Hätten Sie nächste Woche 5 Minuten Zeit für ein kurzes Gespräch?

Mit freundlichen Grüßen,
Furkan Deniz Albaylar
iOS Developer & IT Consultant | Berlin
furkandenizalbaylar@gmail.com

---

ENGLISH:
Subject: Digital Growth Opportunity for [NAME]

Dear Team,

I recently came across the website of [NAME] and performed a brief digital audit. As a Berlin-based iOS Developer and IT Consultant, I help local businesses scale by leveraging modern technology and AI.

Key Insights:

• Technical: [TOP_ISSUE_EN] is affecting your online credibility and search visibility.
• AI-SEO: I use AI to optimize your site for local search trends, so you rank above your competitors.
• Mobile & App: [Same mobile/app benefit in English, tailored to SECTOR]

My goal: transform your online presence into an automated customer acquisition tool. Would you have 5 minutes next week for a brief introductory call?

Best regards,
Furkan Deniz Albaylar
iOS Developer & IT Consultant | Berlin
furkandenizalbaylar@gmail.com"""


def generate_email(biz: dict, client: anthropic.Anthropic) -> dict:
    name = biz.get("name", "")
    sector = biz.get("sector", "")
    website = biz.get("website", "")
    issues = biz.get("issues", [])
    score = biz.get("quality_score", "?")

    technical_issue_de = issues[0] if issues else "Verbesserungspotenzial bei der Ladezeit"
    technical_issue_en = issues[0] if issues else "page load performance"

    user_content = (
        f"Business: {name}\n"
        f"Sector: {sector}\n"
        f"Website: {website}\n"
        f"Quality score: {score}/100\n"
        f"Technical issues: {', '.join(issues) if issues else 'minor improvements needed'}\n"
        f"TOP_ISSUE_DE: {technical_issue_de}\n"
        f"TOP_ISSUE_EN: {technical_issue_en}"
    )

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    full_email = msg.content[0].text.strip()
    return {"full": full_email}


def send_email(to_email: str, business_name: str, full_email: str,
               gmail_user: str = "", gmail_pass: str = "") -> bool:
    # Konu satırını emailden çek
    subject = f"Optimierung & KI-Wachstum für {business_name} / Digital Growth Opportunity"
    for line in full_email.splitlines():
        if line.startswith("Betreff:"):
            subject = line.replace("Betreff:", "").strip()
            break

    # Almanca ve İngilizce bölümleri ayır
    if "---" in full_email and "ENGLISH:" in full_email:
        parts = full_email.split("ENGLISH:")
        german_block = parts[0].replace("GERMAN:", "").strip()
        english_block = parts[1].strip()
    else:
        german_block = full_email
        english_block = ""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Furkan Deniz Albaylar <{gmail_user}>"
    msg["To"] = to_email

    plain = full_email

    html = f"""<div style="font-family:Arial,sans-serif;max-width:620px;color:#1a1a1a;line-height:1.7;font-size:15px">
  <div style="margin-bottom:32px">
    {'<br>'.join(german_block.splitlines())}
  </div>
  <hr style="border:none;border-top:2px solid #e0e0e0;margin:32px 0">
  <div>
    {'<br>'.join(english_block.splitlines())}
  </div>
</div>"""

    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, to_email, msg.as_string())
        return True
    except Exception as e:
        logger.error("SMTP hatası (%s): %s", to_email, e)
        return False
