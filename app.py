import json
import logging
import os
import threading

from fastapi import FastAPI, Request, Form, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
import uvicorn

from db import (init_db, reset_stale_campaigns, create_campaign, get_campaign,
                get_all_campaigns, get_leads, get_lead, get_setting, set_setting,
                update_campaign, count_running_campaigns,
                get_offerings, get_offering, create_offering, update_offering, delete_offering)
from runner import run_campaign, send_campaign_emails, stop_campaign, analyze_campaign_leads
from followup_runner import send_followup_sequence, count_followup_ready
from agents.excel_agent import save_leads_workbook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI()
templates = Jinja2Templates(directory="templates")
templates.env.filters["fromjson"] = lambda s: json.loads(s) if s else []

SECTORS = [
    "restaurant", "cafe", "hotel", "friseur", "zahnarzt",
    "rechtsanwalt", "steuerberater", "fitnessstudio", "apotheke",
    "autohaus", "immobilien", "architekt", "fotograf", "marketing",
    "physiotherapie", "kosmetik", "blumenladen", "buchhandlung",
    "elektronik", "fahrschule", "reinigung", "umzug", "catering",
    "baecker", "metzger", "optiker", "versicherung", "tierarzt",
]

CITIES = {
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

SECTORS_SET = set(SECTORS)
CITIES_SET  = set(CITIES)
MAX_CONCURRENT_CAMPAIGNS = 3


@app.on_event("startup")
async def startup():
    os.makedirs("output", exist_ok=True)
    init_db()
    reset_stale_campaigns()
    logger.info("Uygulama başlatıldı")


def _get_all_sectors() -> list[str]:
    """Sabit sektörler + kullanıcının özel sektörleri."""
    custom_raw = get_setting("custom_sectors", "")
    custom = [s.strip() for s in custom_raw.splitlines() if s.strip()]
    seen = set(SECTORS)
    extra = [s for s in custom if s not in seen]
    return SECTORS + extra


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    campaigns = get_all_campaigns()
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={
            "campaigns": [dict(c) for c in campaigns],
            "sectors":   _get_all_sectors(),
            "cities":    CITIES,
            "offerings": [dict(o) for o in get_offerings()],
        }
    )


@app.post("/campaign/start")
async def start_campaign(
    city:        str       = Form(...),
    sectors:     list[str] = Form(...),
    home_office: bool      = Form(False),
    analyze:     bool      = Form(False),
    offering_id: int       = Form(...),
):
    if city not in CITIES_SET:
        raise HTTPException(status_code=422, detail=f"Geçersiz şehir: {city}")

    if not sectors:
        raise HTTPException(status_code=422, detail="En az bir sektör seçilmeli")

    all_valid = set(_get_all_sectors())
    invalid = [s for s in sectors if s not in all_valid]
    if invalid:
        raise HTTPException(status_code=422, detail=f"Geçersiz sektörler: {invalid}")

    # Verify offering exists
    offering = get_offering(offering_id)
    if not offering:
        raise HTTPException(status_code=422, detail="Geçersiz teklif (offering)")

    if count_running_campaigns() >= MAX_CONCURRENT_CAMPAIGNS:
        raise HTTPException(
            status_code=429,
            detail=f"Zaten {MAX_CONCURRENT_CAMPAIGNS} kampanya çalışıyor. Lütfen bekle."
        )

    search_mode  = "home_office" if home_office else "standard"
    analyze_mode = "find_analyze" if analyze else "find_only"
    cid = create_campaign(city, sectors, search_mode=search_mode, analyze_mode=analyze_mode, offering_id=offering_id)
    threading.Thread(
        target=run_campaign,
        args=(cid, city, sectors, home_office, analyze),
        daemon=True,
        name=f"campaign-{cid}",
    ).start()
    logger.info("Kampanya %d başlatıldı — %s, ev ofisi=%s, analiz=%s, offering=%s", cid, city, home_office, analyze, offering["name"])
    return JSONResponse({"campaign_id": cid})


@app.get("/campaign/{cid}", response_class=HTMLResponse)
async def campaign_page(request: Request, cid: int):
    campaign = get_campaign(cid)
    if not campaign:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    leads = get_leads(cid)
    can_analyze = (
        campaign["status"] in ("completed", "stopped") and
        any(l["website"] and not l["analyzed"] for l in leads)
    )
    offering = get_offering(campaign["offering_id"]) if campaign["offering_id"] else None
    return templates.TemplateResponse(
        request=request, name="campaign.html",
        context={
            "campaign":       dict(campaign),
            "leads":          [dict(l) for l in leads],
            "can_analyze":    can_analyze,
            "offering":       dict(offering) if offering else None,
            "followup_count": count_followup_ready(cid),
        }
    )


@app.get("/campaign/{cid}/progress")
async def campaign_progress(cid: int):
    c = get_campaign(cid)
    if not c:
        return JSONResponse({"status": "not_found"})
    return JSONResponse({
        "status": c["status"],
        "step":   c["progress_step"],
        "pct":    c["progress_pct"],
        "total":  c["total_found"],
    })


@app.post("/campaign/{cid}/stop")
async def stop_campaign_endpoint(cid: int):
    campaign = get_campaign(cid)
    if not campaign:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    if campaign["status"] not in ("running", "analyzing"):
        raise HTTPException(status_code=400, detail="Kampanya zaten çalışmıyor")
    ok = stop_campaign(cid)
    if not ok:
        update_campaign(cid, status="stopped", progress_step="Durduruldu")
    return JSONResponse({"ok": True})


@app.post("/campaign/{cid}/analyze")
async def start_analysis(cid: int):
    campaign = get_campaign(cid)
    if not campaign:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    if campaign["status"] not in ("completed", "stopped"):
        raise HTTPException(status_code=400, detail="Kampanya henüz tamamlanmadı")
    threading.Thread(
        target=analyze_campaign_leads,
        args=(cid,),
        daemon=True,
        name=f"analyze-{cid}",
    ).start()
    logger.info("Manuel analiz başlatıldı — kampanya %d", cid)
    return JSONResponse({"ok": True})


@app.post("/campaign/{cid}/send-emails")
async def send_emails(cid: int, background_tasks: BackgroundTasks):
    campaign = get_campaign(cid)
    if not campaign:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    if campaign["status"] not in ("completed", "stopped"):
        raise HTTPException(status_code=400, detail="Kampanya henüz tamamlanmadı")
    background_tasks.add_task(send_campaign_emails, cid)
    return JSONResponse({"ok": True})


@app.get("/campaign/{cid}/download")
async def download_excel(cid: int):
    campaign = get_campaign(cid)
    if not campaign:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")

    leads = [dict(l) for l in get_leads(cid)]
    path  = f"output/campaign_{cid}.xlsx"
    save_leads_workbook(leads, path)
    return FileResponse(path, filename=f"leads_{campaign['city']}_{cid}.xlsx")


@app.get("/campaign/{cid}/lead/{lid}/preview")
async def lead_preview(cid: int, lid: int):
    campaign = get_campaign(cid)
    if not campaign:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    lead = get_lead(lid)
    if not lead or lead["campaign_id"] != cid:
        raise HTTPException(status_code=404, detail="Lead bulunamadı")

    lead_dict = dict(lead)
    template_de = get_setting("email_template_de", "")
    template_en = get_setting("email_template_en", "")
    has_key = bool(get_setting("anthropic_api_key", ""))

    email_preview = None
    if template_de or template_en:
        issues_text = lead_dict.get("issues") or "—"
        vars_ = dict(
            name=lead_dict.get("name", ""),
            sector=lead_dict.get("sector", ""),
            website=lead_dict.get("website") or "—",
            issues=issues_text,
            score=lead_dict.get("quality_score") or "?",
        )
        try:
            de = template_de.format_map(vars_) if template_de else ""
            en = template_en.format_map(vars_) if template_en else ""
            parts = []
            if de: parts.append(f"🇩🇪 Almanca:\n{de}")
            if en: parts.append(f"🇬🇧 İngilizce:\n{en}")
            email_preview = "\n\n---\n\n".join(parts)
        except (KeyError, ValueError):
            email_preview = template_de or template_en

    return JSONResponse({
        "lead": lead_dict,
        "email_preview": email_preview,
        "has_template": bool(template_de or template_en),
        "has_api_key": has_key,
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="settings.html",
        context={
            "anthropic_key":  get_setting("anthropic_api_key"),
            "gmail_user":     get_setting("gmail_user"),
            "gmail_pass":     get_setting("gmail_password"),
            "custom_sectors": get_setting("custom_sectors"),
            # Sender (B2B outreach'i kim gönderiyor)
            "sender_name":    get_setting("sender_name"),
            "sender_title":   get_setting("sender_title"),
            "sender_email":   get_setting("sender_email"),
            # Offerings (çok satır, ICP dahil)
            "offerings":      [dict(o) for o in get_offerings()],
        }
    )


@app.post("/settings")
async def save_settings(
    anthropic_key:  str = Form(""),
    gmail_user:     str = Form(""),
    gmail_pass:     str = Form(""),
    custom_sectors: str = Form(""),
    sender_name:    str = Form(""),
    sender_title:   str = Form(""),
    sender_email:   str = Form(""),
):
    set_setting("anthropic_api_key", anthropic_key.strip())
    set_setting("gmail_user",        gmail_user.strip())
    set_setting("gmail_password",    gmail_pass.strip())
    set_setting("custom_sectors",    custom_sectors.strip())
    set_setting("sender_name",       sender_name.strip())
    set_setting("sender_title",      sender_title.strip())
    set_setting("sender_email",      sender_email.strip())
    logger.info("Ayarlar güncellendi")
    return JSONResponse({"ok": True})


@app.post("/api/offerings")
async def add_offering_endpoint(
    name:           str = Form(...),
    description:    str = Form(""),
    pitch_de:       str = Form(""),
    pitch_en:       str = Form(""),
    icp_sectors:    str = Form(""),
    icp_locations:  str = Form(""),
    icp_size_hint:  str = Form("any"),
    icp_signals:    str = Form(""),
    is_active:      int = Form(1),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Teklif adı boş olamaz")
    oid = create_offering(
        name.strip(), description.strip(), pitch_de.strip(), pitch_en.strip(),
        icp_sectors=icp_sectors.strip(),
        icp_locations=icp_locations.strip(),
        icp_size_hint=icp_size_hint.strip() or "any",
        icp_signals=icp_signals.strip(),
        is_active=is_active,
    )
    return JSONResponse({"ok": True, "id": oid})


@app.post("/api/offerings/{oid}")
async def update_offering_endpoint(
    oid: int,
    name:           str = Form(...),
    description:    str = Form(""),
    pitch_de:       str = Form(""),
    pitch_en:       str = Form(""),
    icp_sectors:    str = Form(""),
    icp_locations:  str = Form(""),
    icp_size_hint:  str = Form("any"),
    icp_signals:    str = Form(""),
    is_active:      int = Form(1),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Teklif adı boş olamaz")
    if not get_offering(oid):
        raise HTTPException(status_code=404, detail="Teklif bulunamadı")
    update_offering(
        oid,
        name=name.strip(),
        description=description.strip(),
        pitch_de=pitch_de.strip(),
        pitch_en=pitch_en.strip(),
        icp_sectors=icp_sectors.strip(),
        icp_locations=icp_locations.strip(),
        icp_size_hint=icp_size_hint.strip() or "any",
        icp_signals=icp_signals.strip(),
        is_active=is_active,
    )
    return JSONResponse({"ok": True})


@app.post("/api/offerings/{oid}/delete")
async def delete_offering_endpoint(oid: int):
    delete_offering(oid)
    return JSONResponse({"ok": True})


@app.post("/campaign/{cid}/check-replies")
async def check_replies_endpoint(cid: int):
    campaign = get_campaign(cid)
    if not campaign:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    
    try:
        from agents.reply_tracker_agent import check_campaign_replies
        new_replies_count = check_campaign_replies(cid)
        return JSONResponse({"ok": True, "new_replies": new_replies_count})
    except Exception as e:
        logger.exception("Cevaplar kontrol edilirken hata oluştu")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/campaign/{cid}/send-followups")
async def send_followups_endpoint(cid: int, background_tasks: BackgroundTasks):
    campaign = get_campaign(cid)
    if not campaign:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    if campaign["status"] not in ("completed", "stopped"):
        raise HTTPException(status_code=400, detail="Kampanya henüz tamamlanmadı")
    background_tasks.add_task(send_followup_sequence, cid)
    return JSONResponse({"ok": True})


@app.get("/campaign/{cid}/followup-count")
async def followup_count_endpoint(cid: int):
    campaign = get_campaign(cid)
    if not campaign:
        return JSONResponse({"count": 0})
    return JSONResponse({"count": count_followup_ready(cid)})


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
