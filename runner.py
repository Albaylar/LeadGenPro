"""Pipeline runner — kampanyayı arka planda çalıştırır."""
import re
import time
import logging
import threading

import anthropic

from db import (update_campaign, save_leads, get_setting,
                get_leads, update_lead_email, update_lead_score)
from agents.research_agent import research_berlin_businesses
from agents.quality_agent import analyze_website
from agents.email_agent import generate_email, send_email

logger = logging.getLogger(__name__)

THRESHOLD = 75
EMAIL_RE  = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')

# Çalışan kampanyaların durdurma sinyalleri: {campaign_id: threading.Event}
_stop_signals: dict[int, threading.Event] = {}


def stop_campaign(cid: int) -> bool:
    """Çalışan kampanyaya durdurma sinyali gönder. True dönerse sinyal iletildi."""
    signal = _stop_signals.get(cid)
    if signal:
        signal.set()
        return True
    return False


def priority_label(score: int) -> str:
    if score < THRESHOLD:
        return "YUKSEK"
    if score < 90:
        return "ORTA"
    return "DUSUK"


def run_campaign(cid: int, city: str, sectors: list[str],
                 home_office: bool = False, analyze: bool = False):
    signal = threading.Event()
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
                update_lead_score(lead["id"], result["score"], result["issues"],
                                  priority_label(result["score"]))
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
        _stop_signals.pop(cid, None)


def analyze_campaign_leads(cid: int):
    """Kullanıcı tarafından tetiklenen web sitesi analizi."""
    signal = threading.Event()
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

        for i, lead in enumerate(to_analyze):
            if signal.is_set():
                update_campaign(cid,
                                status="completed",
                                progress_step=f"Analiz durduruldu — {i}/{total}",
                                progress_pct=5 + int(i / total * 90))
                logger.info("Analiz durduruldu — kampanya %d (%d/%d)", cid, i, total)
                return

            result = analyze_website(lead["website"])
            update_lead_score(lead["id"], result["score"], result["issues"],
                              priority_label(result["score"]))

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
        _stop_signals.pop(cid, None)


def send_campaign_emails(cid: int) -> dict:
    gmail_user = get_setting("gmail_user")
    gmail_pass = get_setting("gmail_password")

    if not gmail_user or not gmail_pass:
        logger.error("send_campaign_emails: Gmail ayarları eksik")
        return {"error": "Gmail kullanıcı adı veya şifresi eksik"}

    # Özel email şablonu
    template_de = get_setting("email_template_de", "")
    template_en = get_setting("email_template_en", "")
    use_template = bool(template_de and template_en)

    client = None
    if not use_template:
        api_key = get_setting("anthropic_api_key")
        if not api_key:
            logger.error("send_campaign_emails: Anthropic API key eksik ve şablon girilmemiş")
            return {"error": "Anthropic API key ayarlanmamış (ya da Ayarlar'dan e-posta şablonu girin)"}
        client = anthropic.Anthropic(api_key=api_key)
    leads  = get_leads(cid)

    targets = [
        l for l in leads
        if l["priority"] == "YUKSEK"
        and l["email"]
        and EMAIL_RE.match(l["email"])
        and l["email_sent"] == "Hayır"
    ]

    logger.info("Kampanya %d: %d adrese mail (%s)",
                cid, min(len(targets), 10),
                "özel şablon" if use_template else "Claude")

    sent = 0
    errors = 0
    for lead in targets[:10]:
        biz = {
            "name":          lead["name"],
            "sector":        lead["sector"],
            "website":       lead["website"],
            "quality_score": lead["quality_score"],
            "issues":        lead["issues"].split(", ") if lead["issues"] else [],
        }

        try:
            if use_template:
                issues_text = ", ".join(biz["issues"]) if biz["issues"] else "—"
                vars_ = dict(
                    name=biz["name"], sector=biz["sector"],
                    website=biz["website"] or "—",
                    issues=issues_text, score=biz["quality_score"] or "?"
                )
                try:
                    de_body = template_de.format_map(vars_)
                    en_body = template_en.format_map(vars_)
                except KeyError as exc:
                    logger.warning("Şablon değişken hatası: %s — lead id=%d", exc, lead["id"])
                    de_body = template_de
                    en_body = template_en
                full_email = f"GERMAN:\n{de_body}\n\n---\n\nENGLISH:\n{en_body}"
            else:
                result     = generate_email(biz, client)
                full_email = result.get("full", "")
                if not full_email:
                    raise ValueError("generate_email boş yanıt döndürdü")

            success = send_email(
                to_email=lead["email"],
                business_name=lead["name"],
                full_email=full_email,
                gmail_user=gmail_user,
                gmail_pass=gmail_pass,
            )
            update_lead_email(lead["id"], "Evet" if success else "Hata")
            if success:
                sent += 1
                logger.info("Mail gönderildi: %s <%s>", lead["name"], lead["email"])
            else:
                errors += 1

        except Exception:
            logger.exception("Mail hatası — lead id=%d", lead["id"])
            update_lead_email(lead["id"], "Hata")
            errors += 1

        time.sleep(3)

    sent_total = sum(1 for l in get_leads(cid) if l["email_sent"] == "Evet")
    update_campaign(cid, emails_sent=sent_total)
    logger.info("Kampanya %d mail özeti: %d gönderildi, %d hata", cid, sent, errors)
    return {"sent": sent, "errors": errors}
