"""Pipeline runner — kampanyayı arka planda çalıştırır."""
import re
import time
import logging
import threading

import anthropic

from db import (update_campaign, save_leads, get_setting,
                get_leads, update_lead_email, update_lead_score,
                get_campaign, get_offering, save_outreach_message,
                get_last_outreach_step, update_lead_contact_email)
from agents.research_agent import research_berlin_businesses
from agents.quality_agent import analyze_website
from agents.email_agent import generate_outreach, send_email
from agents.contact_agent import extract_emails

logger = logging.getLogger(__name__)

# Tek kaynak eşikler — hem runner hem excel_agent buradan okur.
# Düşük puan = kötü site = yüksek öncelikli lead.
PRIORITY_HIGH_MAX = 75   # score < 75  → YUKSEK
PRIORITY_MID_MAX  = 90   # score < 90  → ORTA, aksi DUSUK
THRESHOLD = PRIORITY_HIGH_MAX  # geriye dönük uyum
EMAIL_RE  = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')

# Çalışan kampanyaların durdurma sinyalleri: {campaign_id: threading.Event}
_stop_signals: dict[int, threading.Event] = {}
_stop_signals_lock = threading.Lock()


def stop_campaign(cid: int) -> bool:
    """Çalışan kampanyaya durdurma sinyali gönder. True dönerse sinyal iletildi."""
    with _stop_signals_lock:
        signal = _stop_signals.get(cid)
    if signal:
        signal.set()
        return True
    return False


def priority_label(score: int | None) -> str:
    """Lead önceliği — düşük puan = yüksek öncelik (kötü site, satış fırsatı)."""
    if score is None:
        return "ORTA"
    if score < PRIORITY_HIGH_MAX:
        return "YUKSEK"
    if score < PRIORITY_MID_MAX:
        return "ORTA"
    return "DUSUK"


def run_campaign(cid: int, city: str, sectors: list[str],
                 home_office: bool = False, analyze: bool = False):
    signal = threading.Event()
    with _stop_signals_lock:
        _stop_signals[cid] = signal

    try:
        mode = "home_office" if home_office else "standard"
        update_campaign(cid,
                        progress_step=f"Araştırılıyor: {city}...",
                        progress_pct=5,
                        search_mode=mode)
        logger.info("Kampanya %d başladı — %s, ev ofisi=%s, analiz=%s", cid, city, home_office, analyze)

        businesses = research_berlin_businesses(
            sectors, max_per_sector=50, city=city, home_office=home_office
        )

        if not businesses:
            update_campaign(cid,
                            status="completed",
                            progress_step="İşletme bulunamadı",
                            progress_pct=100,
                            total_found=0)
            logger.warning("Kampanya %d: hiç işletme bulunamadı", cid)
            return

        total = len(businesses)
        update_campaign(cid,
                        total_found=total,
                        progress_step=f"{total} işletme bulundu. Kaydediliyor...",
                        progress_pct=30)

        # Tüm leadleri önce kaydet (analiz=False ile)
        save_leads(cid, businesses, analyzed=False)
        logger.info("Kampanya %d: %d lead kaydedildi", cid, total)

        if not analyze:
            update_campaign(cid, status="completed", progress_step="Tamamlandı", progress_pct=100)
            logger.info("Kampanya %d tamamlandı (%d lead) — analiz yapılmadı", cid, total)
            return

        # Analiz aşaması
        update_campaign(cid,
                        progress_step=f"{total} site analiz ediliyor...",
                        progress_pct=35)
        leads = get_leads(cid)
        own_email = get_setting("gmail_user", "")

        for i, lead in enumerate(leads):
            if signal.is_set():
                update_campaign(cid,
                                status="stopped",
                                progress_step=f"Durduruldu — {i}/{total} site analiz edildi",
                                progress_pct=35 + int(i / total * 55))
                logger.info("Kampanya %d durduruldu (%d/%d analiz edildi)", cid, i, total)
                return

            url = lead["website"]
            if url:
                result = analyze_website(url)
                update_lead_score(
                    lead["id"], result["score"], result["issues"],
                    priority_label(result["score"]),
                    page_title=result.get("title", ""),
                )

                emails = extract_emails(
                    result.get("soup"),
                    result.get("response_text", ""),
                    result.get("url", url),
                    own_email=own_email,
                )
                if emails and not lead["email"]:
                    update_lead_contact_email(lead["id"], emails[0])
                    logger.info("Email bulundu: %s ← %s", lead["name"], emails[0])
            else:
                update_lead_score(lead["id"], 0, ["Website yok"], "YUKSEK")

            pct = 35 + int((i + 1) / total * 55)
            update_campaign(cid, progress_step=f"Site analizi: {i + 1}/{total}", progress_pct=pct)
            time.sleep(0.3)

        update_campaign(cid, status="completed", progress_step="Tamamlandı", progress_pct=100)
        logger.info("Kampanya %d tamamlandı (%d lead, analiz yapıldı)", cid, total)

    except Exception:
        logger.exception("Kampanya %d başarısız", cid)
        update_campaign(cid,
                        status="failed",
                        progress_step="Beklenmeyen hata — logları kontrol et",
                        progress_pct=0)
    finally:
        with _stop_signals_lock:
            _stop_signals.pop(cid, None)


def analyze_campaign_leads(cid: int):
    """Kullanıcı tarafından tetiklenen web sitesi analizi."""
    signal = threading.Event()
    with _stop_signals_lock:
        _stop_signals[cid] = signal

    try:
        leads = get_leads(cid)
        to_analyze = [l for l in leads if l["website"] and not l["analyzed"]]

        if not to_analyze:
            update_campaign(cid,
                            status="completed",
                            progress_step="Tüm siteler zaten analiz edilmiş",
                            progress_pct=100)
            return

        total = len(to_analyze)
        update_campaign(cid,
                        status="analyzing",
                        progress_step=f"0/{total} site analiz ediliyor...",
                        progress_pct=5)
        logger.info("Manuel analiz başladı — kampanya %d, %d site", cid, total)

        own_email = get_setting("gmail_user", "")

        for i, lead in enumerate(to_analyze):
            if signal.is_set():
                update_campaign(cid,
                                status="completed",
                                progress_step=f"Analiz durduruldu — {i}/{total}",
                                progress_pct=5 + int(i / total * 90))
                logger.info("Analiz durduruldu — kampanya %d (%d/%d)", cid, i, total)
                return

            result = analyze_website(lead["website"])
            update_lead_score(
                lead["id"], result["score"], result["issues"],
                priority_label(result["score"]),
                page_title=result.get("title", ""),
            )

            emails = extract_emails(
                result.get("soup"),
                result.get("response_text", ""),
                result.get("url", lead["website"]),
                own_email=own_email,
            )
            if emails and not lead["email"]:
                update_lead_contact_email(lead["id"], emails[0])
                logger.info("Email bulundu: %s ← %s", lead["name"], emails[0])

            pct = 5 + int((i + 1) / total * 90)
            update_campaign(cid, progress_step=f"Analiz: {i + 1}/{total}", progress_pct=pct)
            time.sleep(0.3)

        update_campaign(cid,
                        status="completed",
                        progress_step="Analiz tamamlandı",
                        progress_pct=100)
        logger.info("Manuel analiz tamamlandı — kampanya %d", cid)

    except Exception:
        logger.exception("Manuel analiz başarısız — kampanya %d", cid)
        update_campaign(cid,
                        status="completed",
                        progress_step="Analiz hatası — logları kontrol et",
                        progress_pct=100)
    finally:
        with _stop_signals_lock:
            _stop_signals.pop(cid, None)


def _load_sender_from_settings() -> dict:
    """Outreach mail signature/From için sender bilgisi."""
    return {
        "name":  get_setting("sender_name") or get_setting("gmail_user", "") or "Sender",
        "title": get_setting("sender_title", ""),
        "email": get_setting("sender_email") or get_setting("gmail_user", ""),
    }


def _try_render_template(tpl: str, prospect: dict) -> str | None:
    """Pitch şablonunda {name}/{sector}/{website}/{issues}/{score} placeholder
    varsa doldur. Hata olursa None döner (LLM fallback'e geçilir)."""
    if not tpl:
        return None
    issues_text = ", ".join(prospect.get("issues_list") or []) or "—"
    vars_ = dict(
        name=prospect.get("name", ""),
        sector=prospect.get("sector", ""),
        website=prospect.get("website") or "—",
        issues=issues_text,
        score=prospect.get("quality_score") or "?",
    )
    try:
        return tpl.format_map(vars_)
    except (KeyError, ValueError) as exc:
        logger.warning("Şablon placeholder hatası: %s", exc)
        return None


def send_campaign_emails(cid: int, batch_size: int = 10) -> dict:
    """B2B outreach gönderim turu (sequence step 1).

    Offering ZORUNLU. Description/pitch boşsa Claude default'a düşer,
    ama offering kaydı şart (kim ne satıyor bilgisi).
    """
    gmail_user = get_setting("gmail_user")
    gmail_pass = get_setting("gmail_password")
    if not gmail_user or not gmail_pass:
        logger.error("send_campaign_emails: Gmail ayarları eksik")
        return {"error": "Gmail kullanıcı adı veya şifresi eksik"}

    campaign = get_campaign(cid)
    if not campaign:
        return {"error": "Kampanya bulunamadı"}

    offering = get_offering(campaign["offering_id"]) if campaign["offering_id"] else None
    if not offering:
        return {"error": "Kampanyada offering yok — Ayarlar'dan teklif ekle ve kampanya offering'ini güncelle"}

    sender = _load_sender_from_settings()

    api_key = get_setting("anthropic_api_key")
    if not api_key:
        return {"error": "Anthropic API key ayarlanmamış"}
    client = anthropic.Anthropic(api_key=api_key)

    offering_dict = dict(offering)
    pitch_de = (offering_dict.get("pitch_de") or "").strip()
    pitch_en = (offering_dict.get("pitch_en") or "").strip()
    has_static_template = bool(pitch_de or pitch_en)

    leads = get_leads(cid)
    targets = [
        l for l in leads
        if (l["priority"] == "YUKSEK" or (l["fit_score"] or 0) >= 60)
        and l["email"]
        and EMAIL_RE.match(l["email"])
        and l["email_sent"] == "Hayır"
    ]
    logger.info("Kampanya %d: %d hedef (batch=%d, template=%s)",
                cid, len(targets), batch_size, "static" if has_static_template else "LLM")

    sent, errors = 0, 0
    for lead in targets[:batch_size]:
        prospect = {
            "name":          lead["name"],
            "sector":        lead["sector"],
            "website":       lead["website"],
            "quality_score": lead["quality_score"],
            "fit_score":     lead["fit_score"],
            "fit_reasons":   lead["fit_reasons"] or lead["issues"] or "",
            "signals":       lead["signals"] or "",
            "issues":        lead["issues"] or "",
            "issues_list":   lead["issues"].split(", ") if lead["issues"] else [],
        }

        try:
            full_email = None
            # Static template varsa onu kullan, placeholder'ı doldur
            if has_static_template:
                de_body = _try_render_template(pitch_de, prospect) if pitch_de else ""
                en_body = _try_render_template(pitch_en, prospect) if pitch_en else ""
                if de_body or en_body:
                    parts = []
                    if de_body: parts.append(f"GERMAN:\n{de_body}")
                    if en_body: parts.append(f"ENGLISH:\n{en_body}")
                    full_email = "\n\n---\n\n".join(parts)

            # Static template yok / placeholder bozuksa LLM'e bırak
            if not full_email:
                result = generate_outreach(prospect, offering_dict, client, sender=sender)
                full_email = (result or {}).get("full", "")
                if not full_email:
                    raise ValueError("generate_outreach boş yanıt döndürdü")

            success = send_email(
                to_email=lead["email"],
                business_name=lead["name"],
                full_email=full_email,
                gmail_user=gmail_user,
                gmail_pass=gmail_pass,
                sender_name=sender.get("name", ""),
            )
            from datetime import datetime
            sent_at = datetime.now().isoformat() if success else None
            update_lead_email(lead["id"], "Evet" if success else "Hata", sent_at=sent_at)

            # Outreach mesajını sequence log'a kaydet (inbox/UI için)
            next_step = get_last_outreach_step(lead["id"]) + 1
            subject_line = ""
            for line in full_email.splitlines():
                s = line.strip()
                if s.startswith(("Betreff:", "Subject:")):
                    subject_line = s.split(":", 1)[1].strip()
                    break
            save_outreach_message(
                lead_id=lead["id"],
                campaign_id=cid,
                offering_id=offering_dict.get("id"),
                sequence_step=next_step,
                subject=subject_line[:300],
                body=full_email,
                status="sent" if success else "failed",
                error="" if success else "SMTP fail",
            )

            if success:
                sent += 1
                logger.info("Mail gönderildi: %s <%s>", lead["name"], lead["email"])
            else:
                errors += 1

        except Exception as exc:
            logger.exception("Mail hatası — lead id=%d", lead["id"])
            update_lead_email(lead["id"], "Hata")
            save_outreach_message(
                lead_id=lead["id"], campaign_id=cid,
                offering_id=offering_dict.get("id"),
                sequence_step=get_last_outreach_step(lead["id"]) + 1,
                subject="", body="", status="failed", error=str(exc)[:500],
            )
            errors += 1

        time.sleep(3)

    sent_total = sum(1 for l in get_leads(cid) if l["email_sent"] == "Evet")
    update_campaign(cid, emails_sent=sent_total)
    logger.info("Kampanya %d mail özeti: %d gönderildi, %d hata", cid, sent, errors)
    return {"sent": sent, "errors": errors}


