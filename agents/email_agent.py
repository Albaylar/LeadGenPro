"""B2B outreach email üretimi ve SMTP gönderimi.

Offering-driven: kullanıcının tanımladığı 'offering' (ne sattığı, ICP, pitch)
prompt'a inject edilir. Sender bilgisi hardcoded değil — settings'ten gelir
(sender_name, sender_title, sender_email).

Geriye dönük uyum: eski `generate_email(biz, client)` çağrıları boş offering
ile yeni mantığa yönlenir.
"""
import html
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic

logger = logging.getLogger(__name__)


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


# --- Backward compat ---

def generate_email(biz: dict, client: anthropic.Anthropic) -> dict:
    """DEPRECATED — yeni runner kodu generate_outreach kullanmalı.
    Offering ya da sender geçilmediğinde minimal default'larla çalışır."""
    return generate_outreach(biz, offering=None, client=client, sender=None)


# --- Sending ---

def parse_subject(full_email: str) -> str:
    for line in full_email.splitlines():
        s = line.strip()
        if s.startswith("Betreff:"):
            return s.replace("Betreff:", "").strip()
        if s.startswith("Subject:"):
            return s.replace("Subject:", "").strip()
    return "Quick question"


def _split_de_en(full_email: str) -> tuple[str, str]:
    if "---" in full_email and "ENGLISH:" in full_email:
        parts = full_email.split("ENGLISH:")
        german = parts[0].replace("GERMAN:", "").strip().rstrip("-").strip()
        english = parts[1].strip()
        return german, english
    return full_email, ""


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
            safe_key = str(key).strip().replace("\r", "").replace("\n", "")
            safe_val = str(val).strip().replace("\r", "").replace("\n", "")
            msg[safe_key] = safe_val

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
