# LeadGenPro İyileştirme Tasarımı

**Tarih:** 2026-09-25  
**Yazar:** Furkan Albaylar  
**Durum:** Onaylandı — Implementation hazır

---

## Problem

LeadGenPro şu anda test aşamasında; canlıya geçilemiyor çünkü:

1. Bulunan leadlerin büyük çoğunluğunda **email adresi yok** — websitelerinden kazınmıyor
2. Üretilen outreach **e-postaları jenerik** — site başlığı, gerçek sorunlar prompt'a gitmiyor
3. **Follow-up dizisi yok** — ilk email'den sonra sistem duruyor
4. **Alman hukuki uyum kontrolü yok** — en güçlü satış argümanı kullanılmıyor

---

## Kapsam Dışı

- Harici email bulma servisi (Hunter.io, Apollo) — GDPR riski, ücretli
- Tam UI yeniden tasarımı
- Kampanya zamanlama otomasyonu (cron)
- Deprecated dosyaların temizlenmesi (ayrı iş)

---

## Mimari Değişiklikler

### Mevcut pipeline

```
Araştırma → DB kayıt → [Analiz] → Email gönder
```

### Yeni pipeline

```
Araştırma → DB kayıt → [Analiz + Email Kazıma + Uyum Kontrolü] → Email gönder → [Follow-up Scheduler]
```

### Etkilenen dosyalar

| Dosya | Değişim türü |
|---|---|
| `agents/contact_agent.py` | **Yeni** — websiteden email kazıma |
| `agents/quality_agent.py` | Genişletildi — `soup`/`response_text` return eder, uyum kontrolleri eklendi |
| `agents/email_agent.py` | Model yükseltme, gelişmiş prompt |
| `runner.py` | `analyze_campaign_leads` içine email kazıma adımı eklendi |
| `followup_runner.py` | **Yeni** — follow-up sequence logic |
| `app.py` | Yeni endpoint: `/campaign/{cid}/send-followups` |
| `db.py` | `update_lead_contact_email()`, `update_lead_score()` `page_title` parametresi, `leads` tablosuna `page_title` kolonu |

---

## Bölüm 1 — `contact_agent.py` (Email Kazıma)

### Prensip

`quality_agent.analyze_website()` zaten HTTP isteği yapıyor. Bu isteği yeniden kullanmak için `analyze_website()` artık `soup`, `response_text`, `final_url` da döndürür. `contact_agent` bu veriyi parametre olarak alır — siteye ikinci kez bağlanmaz.

### Email bulma stratejisi (öncelik sırasıyla)

1. **Ana sayfadaki `mailto:` linkleri** — `soup.find_all("a", href=re.compile(r"^mailto:"))`
2. **Regex ile metin içi email** — tüm `response_text` üzerinde
3. **Kontakt/Impressum sayfası ziyareti** — ana sayfada bulunamazsa `href` içinde `kontakt`, `contact`, `impressum` geçen ilk 2 link takip edilir; aynı kazıma tekrarlanır (max 2 ek HTTP isteği)

### Spam/çöp filtreleme

```python
SKIP_EMAILS = {
    "noreply", "no-reply", "donotreply", "mailer-daemon",
    "example.com", "domain.", "email.", "muster."
}
PRIORITY_PREFIXES = ["info@", "kontakt@", "hallo@", "office@", "mail@"]
```

- Placeholder veya noreply adreslerini at
- Kendi Gmail kullanıcı adıyla aynıysa at
- Max 3 email tut; `PRIORITY_PREFIXES` eşleşenleri öne al
- İlk (en güvenilir) email DB'ye yazılır

### DB entegrasyonu

```python
# runner.py — analyze_campaign_leads döngüsü içinde:
result = analyze_website(url)          # soup + response_text de döner
emails = extract_emails(
    result["soup"],
    result["response_text"],
    result["final_url"]
)
if emails and not lead["email"]:       # mevcut email'i geçersiz kılma
    update_lead_contact_email(lead["id"], emails[0])
```

`update_lead_contact_email(lead_id, email)` → sadece `leads.email` günceller.

---

## Bölüm 2 — Derin Alman Uyum Kontrolü

`quality_agent.py`'a `_check_german_compliance(soup, response_text, url)` fonksiyonu eklenir. 4 kategoride 12 kontrol.

### Kategori 1 — Hukuki Zorunluluklar

| Kontrol | Tespit | Puan |
|---|---|---|
| **Impressum yok** (§5 TMG) | `<a>` veya `href`'de "impressum" | −25 |
| **Impressum içeriği yetersiz** | Impressum sayfası takip edilir; PLZ `\d{5}`, telefon, email, "Inhaber"/"GF" aranır — 2'den az bulunursa flag | −15 |
| **Datenschutzerklärung yok** (DSGVO) | Link veya metin içinde "datenschutz"/"privacy" | −25 |
| **Datenschutzerklärung DSGVO'ya uygun değil** | Sayfada "Art. 6", "DSGVO", "Betroffenenrechte" geçiyor mu? | −10 |
| **Cookie onay mekanizması yok** (TTDSG §25) | Cookiebot, OneTrust, Usercentrics, Borlabs, Complianz, CookieFirst, ConsentManager script varlığı; yoksa genel "cookie" metin varlığı | −10 |

> Impressum/Datenschutz eksikliği Almanya'da Abmahnung (hukuki ihtarname) sebebidir.

### Kategori 2 — Erişilebilirlik (BITV 2.0 / WCAG 2.1)

| Kontrol | Tespit | Puan |
|---|---|---|
| **`lang="de"` eksik** | `<html lang="">` attribute | −5 |
| **Alt metin eksik görseller** | `<img>` tag'lerinde boş/eksik `alt`; 3'ten fazlaysa flag | −8 |
| **Başlık hiyerarşisi bozuk** | `<h1>` yok veya birden fazla `<h1>` | −5 |

### Kategori 3 — Yerel SEO

| Kontrol | Tespit | Puan |
|---|---|---|
| **Schema.org LocalBusiness yok** | `schema.org` veya `"@type"` JSON-LD | −5 |
| **Telefon numarası yok** | `+49` veya `0\d{3,5}[\s\-]\d+` pattern | −5 |

### Issues listesi prefix formatı

```python
"[HUKUK] Impressum yok (§5 TMG ihlali)"
"[HUKUK] Datenschutzerklärung DSGVO'ya uygun değil"
"[HUKUK] Cookie onay mekanizması yok (TTDSG §25)"
"[SEO]   Schema.org LocalBusiness işaretlemesi yok"
"[ERİŞİM] 7 görselde alt metin eksik"
```

Email agent bu prefix'lere bakarak hukuki sorunları öne çıkaran açılış yapar.

---

## Bölüm 3 — E-posta Kalitesi İyileştirmesi

### Model yükseltme

```python
# Öncesi
model: str = "claude-haiku-4-5-20251001"
# Sonrası
model: str = "claude-sonnet-4-6"
```

~10 email = ~$0.02 maliyet artışı (ihmal edilebilir).

### Prompt iyileştirmeleri

**1. Almanca ton talimatı (yeni)**
```
German style: formal "Sie" form throughout. Prefer concrete Zahlen/Fakten.
No marketing-speak. Betreff: max 8 Wörter, kein Ausrufezeichen.
Never start with "Ich hoffe" or "Ich schreibe Ihnen wegen".
Opening sentence: reference ONE specific thing about their website or sector.
Max 3 sentences before the value proposition.
```

**2. Genişletilmiş prospect context**

```python
prospect = {
    "name":          lead["name"],
    "sector":        lead["sector"],
    "website":       lead["website"],
    "quality_score": lead["quality_score"],
    "page_title":    lead["page_title"],    # YENİ
    "issues":        lead["issues"],        # prefix'li uyum sorunları dahil
    "fit_score":     lead["fit_score"],
    "fit_reasons":   lead["fit_reasons"],
    "signals":       lead["signals"],
}
```

`page_title` ve `[HUKUK]` prefix'li sorunlar modele geçilince çok spesifik e-postalar üretilir:
> *"Ihre Website unter zahnarzt-berlin.de hat weder ein Impressum noch eine Datenschutzerklärung — das kann zu einer kostspieligen Abmahnung führen."*

### DB değişikliği

`leads` tablosuna `page_title TEXT` kolonu eklenir (migration olarak).  
`update_lead_score(lead_id, quality_score, issues, priority, page_title="")` imzası güncellenir.

---

## Bölüm 4 — `followup_runner.py` (Follow-up Dizisi)

### Sequence mantığı

```
Gün  0: Step 1 — İlk outreach (mevcut sistem)
Gün  7: Step 2 — Follow-up ("kurze Nachfrage")
Gün 14: Step 3 — Break-up ("letzte Nachricht")
Gün 14+: Artık email gönderilmez
```

Lead diziden çıkar eğer:
- `reply_received = 1` (yanıt geldi)
- `email_sent = 'Hata'` (ilk mail başarısız)
- `sequence_step >= 3` (break-up zaten gönderildi)

### `get_followup_candidates(cid, step, days_wait)`

`outreach_messages` tablosunu sorgular:
- Step-1 gönderilmiş (`sequence_step = 1` kaydı var)
- Step-`{step}` henüz gönderilmemiş (o step kaydı yok)
- `reply_received = 0`
- Step-1'in `sent_at` değeri `now - days_wait` günden eski

### Prompt'ta step farkı

| Step | Ton | Kelime hedefi |
|---|---|---|
| 1 | Değer önerisi + somut sorun | 130–200 |
| 2 | "Geçen hafta yazdım, sormak istedim" | 60–80 |
| 3 | "Son mesajım, ilginiz yoksa anlıyorum" | 40–60 |

`generate_outreach()` yeni `sequence_step: int = 1` parametresi alır.

### Thread bağlantısı (Gmail'de aynı konuşma)

Step 2 ve 3 gönderilirken `outreach_messages` tablosundan step-1'in `subject` değeri alınır. SMTP mesajına `In-Reply-To` ve `References` header'ları eklenir — Gmail'de tek thread olarak görünür.

### UI entegrasyonu

```
POST /campaign/{cid}/send-followups
```

`campaign.html`'de "E-posta Gönder" butonunun yanına "Follow-up Gönder" butonu eklenir. Buton etiketi kaç lead'in hazır olduğunu gösterir:

```
Follow-up Gönder (12 hazır)
```

---

## Veri Akışı Özeti

```
analyze_campaign_leads(cid)
  └─ for each lead with website:
       ├─ analyze_website(url)
       │    ├─ teknik kontroller (HTTPS, hız, mobil...)
       │    └─ _check_german_compliance(soup, text, url)
       │         ├─ Impressum link + içerik (1 ek istek)
       │         ├─ Datenschutz link + DSGVO keywords
       │         ├─ Cookie consent script
       │         ├─ lang attr, alt tags, h1 yapısı
       │         └─ Schema.org, telefon
       ├─ update_lead_score(id, score, issues, priority, page_title)
       └─ extract_emails(soup, response_text, final_url)  [contact_agent]
            ├─ mailto links
            ├─ regex on full text
            └─ kontakt/impressum page follow (max 2 req)
            └─ update_lead_contact_email(id, email)

send_campaign_emails(cid)
  └─ targets: YUKSEK priority OR fit_score >= 60, email var, email_sent='Hayır'
       └─ generate_outreach(prospect, offering, client, sender, sequence_step=1)
            model: claude-sonnet-4-6
            prompt: page_title + [HUKUK]/[SEO]/[ERİŞİM] issues dahil

send_followup_sequence(cid)
  └─ step2: get_followup_candidates(cid, step=2, days_wait=7)
  └─ step3: get_followup_candidates(cid, step=3, days_wait=14)
       └─ generate_outreach(..., sequence_step=2/3)
            └─ send with In-Reply-To header
```

---

## Açık Sorular / Sonraki Adımlar

- Impressum derin kontrol için ek HTTP isteği → timeout/rate-limit dikkat
- TTDSG §25 kontrolü — Cookie olmayan statik siteler false positive verebilir; eşik ayarlanabilir
- Follow-up'ta `days_wait` değerleri (7/14) settings üzerinden konfigüre edilebilir hale getirilebilir (sonraki iterasyon)
