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
    """UI badge için: kaç lead herhangi bir follow-up adımına hazır.

    step2 sayısı: step-1 gönderilmiş ve 7+ gün geçmiş (step-2 olup olmadığına bakılmaz).
    step3 sayısı: step-2 gönderilmiş ve 7+ gün geçmiş, step-3 henüz yok.
    """
    cutoff7 = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    with conn() as c:
        step2_count = c.execute("""
            SELECT COUNT(DISTINCT l.id)
            FROM leads l
            JOIN outreach_messages om
              ON om.lead_id = l.id AND om.sequence_step = 1 AND om.status = 'sent'
            WHERE l.campaign_id = ?
              AND l.reply_received = 0
              AND l.email IS NOT NULL AND l.email != ''
              AND om.sent_at < ?
        """, (cid, cutoff7)).fetchone()[0]

        step3_count = c.execute("""
            SELECT COUNT(DISTINCT l.id)
            FROM leads l
            JOIN outreach_messages om
              ON om.lead_id = l.id AND om.sequence_step = 2 AND om.status = 'sent'
            WHERE l.campaign_id = ?
              AND l.reply_received = 0
              AND l.email IS NOT NULL AND l.email != ''
              AND om.sent_at < ?
              AND NOT EXISTS (
                  SELECT 1 FROM outreach_messages om3
                  WHERE om3.lead_id = l.id AND om3.sequence_step = 3
              )
        """, (cid, cutoff7)).fetchone()[0]

    return step2_count + step3_count


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
