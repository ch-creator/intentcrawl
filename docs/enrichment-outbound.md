# Enrichment & outbound design (future)

This document describes how to extend **Lead Signal Listener** beyond signal capture—contact enrichment (email, phone, WhatsApp) and outbound email—without changing current behavior until you choose to build it.

## Current scope (today)

| Stage | Supported |
|-------|-----------|
| Listen (Treg + RSS + custom endpoints) | Yes |
| Rule-based scoring & personas | Yes |
| CRM-style status + notes | Yes |
| CSV/JSON export | Yes |
| Email / phone / WhatsApp enrichment | No |
| Web scraping for contacts | No |
| Send email or WhatsApp | No |

## Target pipeline

```
Listeners (Treg) → SQLite signals → Score & filter "hot"
       → Enrichment queue → Human or auto review → Outbound (email / optional WhatsApp)
```

### 1. Define "hot" for your use case (example: book publishing)

**Listeners UI**

- **Focus:** “Authors and entrepreneurs who want to publish a book or self-publish.”
- **Keywords (short phrases):** `publish my book`, `first time author`, `self publishing`, `manuscript ready`, `looking for editor`, `Amazon KDP`.
- **Sources:** Reddit, X, HN/RSS (e.g. r/selfpublish feeds), LinkedIn posts, Google News / YouTube via custom catalog sources.
- **Scoring JSON:** Boost `intent` phrases (`ready to publish`, `need ghostwriter`, `ISBN`) and a `persona` segment `author` with book-related terms.
- **Settings:** Raise `min_score_to_keep`; use board filters **Qualified** for human-reviewed hot leads.

**Hot rule (automation later):** e.g. `score >= 70` AND at least one intent match → auto-set `qualified` (requires a small backend job).

### 2. Enrichment (new module)

**Inputs per lead:** `id`, `author`, `author_url`, `url`, `text`, `title`, `source`.

**Outputs (new columns or `enrichment` JSON on signals):**

| Field | Notes |
|-------|--------|
| `email` | Verified where possible (SMTP check or vendor) |
| `phone` | E.164 when found |
| `whatsapp` | Only if public `wa.me/…` or explicit opt-in—no “lookup any number” API |
| `confidence` | 0–1 |
| `source` | `hunter`, `apollo`, `scrape`, `profile_bio`, etc. |
| `enriched_at` | Timestamp |

**Strategies (pick one or chain):**

1. **Vendor APIs** — Hunter, Apollo, Clearbit, People Data Labs: name + domain → email/phone; best for LinkedIn/company sites linked from the post.
2. **Profile pass** — Parse Treg-returned profile/bio fields (already partially available via `author_url`).
3. **Controlled scrape** — Fetch `author_url` or linked homepage; extract `mailto:`, `tel:`, contact page; rate-limit; respect `robots.txt`; store raw URL only for audit.
4. **LLM assist (optional)** — Extract *published* contacts from post + bio text; never invent addresses.

**Queue:** After each run, enqueue leads with `status = qualified` OR `score >= threshold`. Worker: `POST /api/enrich` (internal) or CLI `python app.py --enrich`.

**Compliance:** GDPR/CAN-SPAM, platform ToS, opt-out list, log lawful basis; default to **manual approve before send**.

### 3. Outbound email (new module)

**Not in repo today.** Recommended pieces:

- **Provider:** SendGrid, Postmark, SES, or Resend.
- **Templates:** Subject/body with `{author}`, `{snippet}`, `{your_offer}`; book-publishing example: coaching / publishing services CTA.
- **State machine:** `qualified` → `enrichment_ok` → `contacted` (on send) → `replied` / `ignored`.
- **Safety:** Max sends per day, domain warmup, unsubscribe link, physical address (CAN-SPAM), no send if `confidence < 0.8`.

**API sketch:**

- `POST /api/outbound/preview` — draft for one `signal_id`.
- `POST /api/outbound/send` — send after approval; writes audit row.
- Config in `data/config.json`: `outbound.enabled`, `from_email`, provider keys via env.

### 4. WhatsApp

- **Consumer WhatsApp:** No reliable cold-outreach API; only message numbers users published or who opted in.
- **WhatsApp Business API:** Template messages, strict policies; integrate only after legal review.
- **Practical path:** Enrichment stores phone if found; UI shows “copy for WhatsApp”; human sends from Business app, then mark `contacted`.

### 5. UI hooks (aligns with sidebar + detail panel)

- Detail panel sections: **Signal** (today) → **Enrichment** (email/phone, confidence, re-run) → **Outbound** (draft email, send, history).
- List badges: “email found”, “ready to send”.
- Settings tab: enrichment providers, outbound provider, thresholds, compliance toggles.

### 6. Phased rollout

| Phase | Deliverable |
|-------|-------------|
| 0 | Configure listeners + scoring (book publishing); export CSV to external tools |
| 1 | DB fields + manual CSV import of enriched contacts |
| 2 | Enrichment worker + review queue in UI |
| 3 | Outbound preview + single-send with approval |
| 4 | Sequences + optional WhatsApp handoff |

### 7. Minimal “no code” path today

1. Set focus and keywords for book publishing in **Listeners**.
2. Run **Run now**; qualify high-score leads.
3. Export CSV → Apollo/Hunter → Lemlist/Instantly for email (outside this app).

---

For implementation questions, start with Phase 1 schema changes in `listener/store.py` and a new `listener/enrichment/` package; keep tokens and provider secrets in environment variables only.
