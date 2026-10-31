# Software agency lead quality — risk & operations

Configuration target: **high-intent software development opportunities** (custom apps, Shopify, SaaS, CRM/ERP, AI automation, migrations).

## Lead scoring (current config)

| Setting | Value | Use |
|---------|-------|-----|
| `min_score_to_keep` | **35** | Rows below this are dropped at ingest |
| `high_intent_threshold` | **60** | Filter UI / export; likely buyer language + pain |
| `priority_lead_threshold` | **80** | Manual review first; RFP/budget/hiring + strong pain |

Scoring engine: rule-based `listener/scoring.py` (pain + intent caps, persona hits, engagement, negatives). **Buying phrases** are in `rules.intent` (e.g. `request for proposal`, `approved budget`).

## Personas (stored on each lead)

- **shopify** — ecommerce / checkout
- **erp** — Odoo, NetSuite, inventory
- **crm** — pipeline, HubSpot, Salesforce
- **saas** — MVP, platform, startup
- **ai** — automation, chatbot, agents

Use Leads filter **persona** after runs stabilize.

## Source risk assessment

| Source | Cost | Risk | Quality note |
|--------|------|------|----------------|
| Hacker News (Algolia) | Free | Low | Noisy on generic `saas`/`ai`; high intent when comments ask for vendors |
| Reddit RSS | Free | Medium | **429 rate limits** — mitigated by 5s spacing, retries, and **2 feeds per run** (rotates). Prefer **hnrss.org** for keyword search without hitting reddit.com |
| hnrss.org | Free | Low | Good for `need developer`-style queries |
| Funding RSS | Free | Low | **Trigger events**, not RFPs; qualify as outbound timing, not hot RFP |
| X (twscrape) | Free* | **High** | Account bans, ToS; needs `twscrape` + logged-in accounts |
| LinkedIn (Playwright) | Free* | **High** | Login/session required; ToS; stub until `playwright-state.json` |
| Treg (disabled) | Paid | Low API | Off when `free_collectors.replace_treg: true` |

\*Infrastructure cost only; legal/ToS risk is on the operator.

## Noise excluded by design

Strong **negative** weights: student, tutorial, course, job seeker, open to work, freelancer promo, giveaway, airdrop.

RSS **keyword filter** on Reddit: post must match at least one include phrase from your query list.

## Recommendations

1. **Daily run**: `schedule_hours: 24` (already set) or cron `python3 app.py --run-once`.
2. **Review queue**: Leads board → **To review**; min score filter **35+**; sort by **Score**.
3. **Mark priority**: Score ≥ **80** or matched intent `request for proposal` / `implementation partner` → status **Qualified**.
4. **Reddit 429**: Stagger feeds, use Redlib self-hosted RSS, or run fewer subs per pass.
5. **Enable X/LinkedIn** only after ops setup (see [FREE_SOURCES_DEPLOY.md](FREE_SOURCES_DEPLOY.md)).
6. **Rescore** after rule tweaks: Listeners (legacy) → **Rescore all leads** or `POST /api/rescore`.
7. **Funding leads**: Treat as **timing** outreach (post-raise build phase), not service RFPs.

## Output goal mapping

| Goal | Mechanism |
|------|-----------|
| High quality only | `min_score_to_keep: 35` + strong negatives |
| Exclude noise | RSS keyword filter + negative rules |
| Buying intent | Heavy `intent` weights (RFP, vendor, budget) |
| Service requirements | Include keywords + pain phrases (ERP/CRM/migration) |
| Budget signal | Intent phrases `budget approved`, `approved budget`, hiring jobs kind (+30 base) |
