# Stripe → Claude → Sheets → Slack: a daily finance pipeline

A small, production-shaped Python automation that turns a daily Stripe export
into a categorized Google Sheet and a Slack digest — with AI flagging the
charges a human should actually look at.

This is a **portfolio sample** demonstrating a class of automation I build for
clients: connect a data source, enrich each record with Claude, write
structured output downstream, push a human-readable summary to wherever your
team lives. The demo runs end-to-end on bundled sample data with zero
credentials. Swapping in real Stripe / Sheets / Slack / Claude is a 10-line
change documented below.

---

## What it does

Every morning, the script:

1. **Loads yesterday's transactions** (50 sample charges bundled — replace with `stripe.Charge.list()` for production).
2. **Categorizes each one with Claude** using a finance-ops prompt — buckets include subscription revenue (new vs. renewal vs. add-on), one-time service revenue, product sales, refunds, failed transactions, disputes, and an explicit "needs review" bucket.
3. **Runs an independent rule-based anomaly check** alongside the LLM so the flag is auditable even if Claude misses something (suspicious sender domains, unclassifiable charges).
4. **Writes the enriched data** to `output/categorized_transactions.csv` (or directly to a Google Sheet — see below).
5. **Posts a daily digest to Slack** with revenue totals, category breakdown, and a `:warning:` block listing anomalies that need human review.

## Example output (from the bundled sample data)

```
*Daily Finance Digest — 2026-05-16*

*Net revenue today:* $34,551.00
  • Gross: $34,649.00  |  Refunded: $98.00
  • 50 transactions processed  |  1 failed  |  1 disputed

*Revenue by category:*
  • One-time Service Revenue: $22,749.00
  • Anomaly - Needs Review: $9,500.00
  • Subscription Revenue - Renewal: $1,455.00
  • Subscription Revenue - Add-on: $597.00
  • Product Sales: $348.00

:warning: *2 anomaly(ies) flagged:*
  • ch_3OqA39…  billing@nextgen-saas.io  $99.00  Customer disputed charge — respond in Stripe dashboard within 7 days.
  • ch_3OqA47…  unknown_payer@protonmail.com  $9,500.00  Unlabeled custom invoice from anonymous-domain sender.
```

The two anomalies are the two charges a human actually needs to look at. The
other 48 transactions, including a $14,999 enterprise integration invoice and
a $3,000 license sale, are correctly categorized as legitimate revenue without
noise.

## Run the demo

```bash
git clone <this repo>
cd stripe-ai-pipeline-demo
python3 automate.py
```

That's it — no credentials, no installation. The script falls back to a
deterministic mock of Claude so you can see the full pipeline shape. Outputs
land in `output/`.

## Wire up real APIs

Copy `.env.example` to `.env` and fill in keys you want to use. Each
integration is one swap inside the marked-up sections of `automate.py`:

| Integration       | Where to swap                                | What you need                          |
|-------------------|----------------------------------------------|----------------------------------------|
| Stripe (real data)| `load_transactions()` → `stripe.Charge.list()`| `STRIPE_SECRET_KEY` (test mode fine)   |
| Claude (real LLM) | Already wired — set `ANTHROPIC_API_KEY`       | Anthropic API key                      |
| Google Sheets     | `write_categorized_csv()` → `gspread`         | Service-account JSON + sheet ID        |
| Slack             | Already wired — set `SLACK_WEBHOOK_URL`       | Incoming webhook for target channel    |

Schedule with cron, GitHub Actions, or any orchestrator:

```cron
0 8 * * *  cd /path/to/repo && python3 automate.py
```

## Project layout

```
stripe-ai-pipeline-demo/
├── automate.py                          # Main script (350 lines, single file)
├── sample_data/
│   └── stripe_transactions.csv          # 50 realistic mock charges
├── output/
│   ├── categorized_transactions.csv     # Enriched data (gets written here)
│   ├── slack_digest.md                  # Digest that would post to Slack
│   └── run_log.txt                      # Execution log
├── .env.example
├── requirements.txt
└── README.md
```

## Design notes

A few choices worth calling out, since they're the kind of decisions clients
hire for:

- **AI categorization is paired with rule-based guardrails.** The anomaly flag
  is set by both the LLM and an independent rule check, so a hallucinated
  "looks fine" doesn't bury a real issue.
- **The mock mode is a feature, not a placeholder.** Demo runs anywhere, and
  the same code path works for offline testing of changes.
- **Single-file architecture by design.** Easy to read, easy to fork into
  Apps Script or n8n if a client wants to migrate later.
- **No third-party deps required for the demo.** Cleaner audit for buyers
  evaluating the code before committing.

## What I'd build next (for a real client)

These weren't included to keep the demo focused, but are how I'd extend it on
a paid engagement:

- **Trend tracking** — week-over-week revenue by category, churn signal from
  failed-renewal patterns.
- **Customer-level enrichment** — pull MRR / lifetime value from Stripe and
  highlight changes (a power user just downgraded; a new logo just signed).
- **Multi-channel alerting** — route anomalies above $X to a separate Slack
  channel or page on-call via PagerDuty.
- **Replay safety** — idempotent writes so a re-run doesn't double-count.

---

**Built by Lucas A.** — data analyst & Python automation specialist. Available
on Fiverr for similar automations: payment data, document processing, email
parsing, report generation, scheduled scraping, AI-enriched workflows.
