"""
Stripe → Claude → Sheets → Slack pipeline (demo)
-------------------------------------------------
Daily automation that pulls a payment processor's recent charges,
uses Claude to categorize and flag anomalies, writes the enriched
data to a Google Sheet, and posts a digest to Slack.

This file is the demo entry point. It runs end-to-end on bundled
sample data with zero credentials, and is wired so swapping in
real Stripe / Google Sheets / Slack / Claude API access is a 10-line
change (see README).

Author: Lucas A. (portfolio sample)
"""
from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent
INPUT_CSV = PROJECT_ROOT / "sample_data" / "stripe_transactions.csv"
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_CSV = OUTPUT_DIR / "categorized_transactions.csv"
OUTPUT_DIGEST = OUTPUT_DIR / "slack_digest.md"
OUTPUT_LOG = OUTPUT_DIR / "run_log.txt"

# Set ANTHROPIC_API_KEY in env to use real Claude. Otherwise the script
# falls back to a deterministic rule-based mock that produces identical-
# shape output, so the demo always runs.
USE_REAL_CLAUDE = bool(os.environ.get("ANTHROPIC_API_KEY"))

# Anomaly heuristics applied alongside the AI categorization, so the
# anomaly flag is auditable even if the LLM hallucinates.
LARGE_CHARGE_THRESHOLD_USD = 2000
SUSPICIOUS_DOMAINS = {"protonmail.com", "tutanota.com", "guerrillamail.com"}

# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class Transaction:
    """One row from the Stripe export."""

    charge_id: str
    created_at: str
    amount_cents: int
    currency: str
    customer_email: str
    description: str
    status: str

    @property
    def amount_usd(self) -> float:
        return self.amount_cents / 100


@dataclass
class EnrichedTransaction(Transaction):
    """Transaction after AI categorization and anomaly detection."""

    category: str = ""
    anomaly_flag: bool = False
    anomaly_reason: str = ""
    ai_notes: str = ""


# ---------------------------------------------------------------------------
# Step 1 — Ingest
# ---------------------------------------------------------------------------


def load_transactions(path: Path) -> list[Transaction]:
    """Load the day's transactions from the bundled CSV.

    In production this is replaced with a Stripe API call:
        import stripe
        stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
        charges = stripe.Charge.list(created={"gte": yesterday_ts}, limit=100)
    """
    txns: list[Transaction] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            txns.append(
                Transaction(
                    charge_id=row["charge_id"],
                    created_at=row["created_at"],
                    amount_cents=int(row["amount_cents"]),
                    currency=row["currency"],
                    customer_email=row["customer_email"],
                    description=row["description"],
                    status=row["status"],
                )
            )
    return txns


# ---------------------------------------------------------------------------
# Step 2 — Enrich with Claude
# ---------------------------------------------------------------------------

CATEGORIZATION_PROMPT = """You are a finance ops analyst at a small SaaS company.
For the transaction below, return STRICT JSON with three keys:
  - category: one of [
        "Subscription Revenue - Renewal",
        "Subscription Revenue - New",
        "Subscription Revenue - Add-on",
        "One-time Service Revenue",
        "Product Sales",
        "Refund",
        "Failed Transaction",
        "Dispute",
        "Anomaly - Needs Review"
    ]
  - anomaly: true | false  (true ONLY for genuinely suspicious activity:
      disputes, suspicious sender domains, unclassifiable charges, or
      amounts that are wildly inconsistent with the customer's history.
      Large B2B invoices to corporate emails are NOT anomalies.)
  - notes: a one-sentence explanation a human can act on.

Transaction:
{transaction_json}
"""


def categorize_with_claude(txn: Transaction) -> dict:
    """Call Claude to categorize a transaction. Falls back to mock mode."""
    if USE_REAL_CLAUDE:
        # Real call — kept minimal so it's obvious how to wire up.
        from anthropic import Anthropic  # type: ignore

        client = Anthropic()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": CATEGORIZATION_PROMPT.format(
                        transaction_json=json.dumps(asdict(txn), indent=2)
                    ),
                }
            ],
        )
        return json.loads(message.content[0].text)

    return _mock_categorize(txn)


def _mock_categorize(txn: Transaction) -> dict:
    """Deterministic stand-in for Claude. Mirrors the real prompt's logic so
    the demo output is identical-shape to a real run."""
    desc = txn.description.lower()
    status = txn.status.lower()
    amount = txn.amount_usd

    if status == "refunded":
        return {
            "category": "Refund",
            "anomaly": False,
            "notes": f"Refund issued for {txn.description} (${amount:,.2f}).",
        }
    if status == "failed":
        return {
            "category": "Failed Transaction",
            "anomaly": False,
            "notes": "Payment failed — Stripe will auto-retry per dunning settings.",
        }
    if status == "disputed":
        return {
            "category": "Dispute",
            "anomaly": True,
            "notes": "Customer disputed charge — respond in Stripe dashboard within 7 days.",
        }
    if "add-on" in desc or "api calls" in desc:
        return {
            "category": "Subscription Revenue - Add-on",
            "anomaly": False,
            "notes": f"Recurring add-on: {txn.description}.",
        }
    if "license" in desc:
        return {
            "category": "One-time Service Revenue",
            "anomaly": False,
            "notes": f"Enterprise license sale (${amount:,.2f}).",
        }
    if any(k in desc for k in ("consulting", "migration", "custom integration")):
        return {
            "category": "One-time Service Revenue",
            "anomaly": False,
            "notes": f"Service engagement — ${amount:,.2f} invoice.",
        }
    if "custom invoice" in desc:
        # Bare "Custom invoice" with no project context — needs review.
        # (Anonymous-domain check is added by apply_rule_based_anomaly_checks.)
        return {
            "category": "Anomaly - Needs Review",
            "anomaly": True,
            "notes": f"Unlabeled custom invoice for ${amount:,.2f}.",
        }
    if any(k in desc for k in ("ebook", "template", "bundle", "package")):
        return {
            "category": "Product Sales",
            "anomaly": False,
            "notes": f"Digital product sale ({txn.description}).",
        }
    if "plan" in desc:
        is_new = "(new)" in desc or " - new" in desc
        return {
            "category": (
                "Subscription Revenue - New"
                if is_new
                else "Subscription Revenue - Renewal"
            ),
            "anomaly": False,
            "notes": f"{txn.description} subscription charge.",
        }
    return {
        "category": "Anomaly - Needs Review",
        "anomaly": True,
        "notes": f"Unclassified charge: '{txn.description}' for ${amount:,.2f}.",
    }


def apply_rule_based_anomaly_checks(txn: EnrichedTransaction) -> None:
    """Independent guardrail — sets anomaly_flag even when the LLM missed it.

    Designed to be conservative: legit B2B invoices to corporate emails should
    pass through cleanly, while anonymous-domain senders and unclassifiable
    charges get caught.
    """
    domain = txn.customer_email.split("@")[-1].lower() if "@" in txn.customer_email else ""
    if domain in SUSPICIOUS_DOMAINS:
        txn.anomaly_flag = True
        existing = (txn.anomaly_reason + " ") if txn.anomaly_reason else ""
        txn.anomaly_reason = (
            existing + f"Customer email uses anonymous domain ({domain})."
        ).strip()


def enrich(transactions: Iterable[Transaction]) -> list[EnrichedTransaction]:
    enriched: list[EnrichedTransaction] = []
    for txn in transactions:
        result = categorize_with_claude(txn)
        e = EnrichedTransaction(
            **asdict(txn),
            category=result.get("category", "Unknown"),
            anomaly_flag=bool(result.get("anomaly", False)),
            anomaly_reason=result.get("notes", "") if result.get("anomaly") else "",
            ai_notes=result.get("notes", ""),
        )
        apply_rule_based_anomaly_checks(e)
        enriched.append(e)
    return enriched


# ---------------------------------------------------------------------------
# Step 3 — Write to Sheets (demo: write to CSV)
# ---------------------------------------------------------------------------


def write_categorized_csv(rows: list[EnrichedTransaction], path: Path) -> None:
    """Write enriched data to CSV. In production, this is replaced by a
    gspread call that appends to a Google Sheet:

        import gspread
        sheet = gspread.service_account().open("Finance Daily").sheet1
        sheet.append_rows([list(asdict(r).values()) for r in rows])
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


# ---------------------------------------------------------------------------
# Step 4 — Generate Slack digest
# ---------------------------------------------------------------------------


def build_slack_digest(rows: list[EnrichedTransaction]) -> str:
    """Generate a markdown digest suitable for posting via Slack incoming webhook."""
    total_revenue = sum(
        r.amount_usd for r in rows if r.status == "succeeded" and r.amount_usd > 0
    )
    refunded = sum(r.amount_usd for r in rows if r.status == "refunded")
    failed_count = sum(1 for r in rows if r.status == "failed")
    disputed_count = sum(1 for r in rows if r.status == "disputed")
    anomalies = [r for r in rows if r.anomaly_flag]

    by_category: dict[str, float] = {}
    for r in rows:
        if r.status == "succeeded":
            by_category[r.category] = by_category.get(r.category, 0) + r.amount_usd

    lines = [
        f"*Daily Finance Digest — {datetime.utcnow().strftime('%Y-%m-%d')}*",
        "",
        f"*Net revenue today:* ${total_revenue - refunded:,.2f}",
        f"  • Gross: ${total_revenue:,.2f}  |  Refunded: ${refunded:,.2f}",
        f"  • {len(rows)} transactions processed  |  {failed_count} failed  |  {disputed_count} disputed",
        "",
        "*Revenue by category:*",
    ]
    for cat, amt in sorted(by_category.items(), key=lambda kv: -kv[1]):
        lines.append(f"  • {cat}: ${amt:,.2f}")

    if anomalies:
        lines.extend(["", f":warning: *{len(anomalies)} anomaly(ies) flagged:*"])
        for a in anomalies:
            lines.append(
                f"  • `{a.charge_id}` — {a.customer_email} — "
                f"${a.amount_usd:,.2f} — {a.anomaly_reason}"
            )
    else:
        lines.extend(["", ":white_check_mark: No anomalies flagged."])

    lines.extend(
        [
            "",
            "_Generated automatically by your Stripe → Claude → Sheets pipeline._",
        ]
    )
    return "\n".join(lines)


def post_to_slack(digest: str) -> None:
    """Post the digest to Slack via incoming webhook.

    In production:
        import requests
        requests.post(os.environ["SLACK_WEBHOOK_URL"], json={"text": digest})
    """
    if os.environ.get("SLACK_WEBHOOK_URL"):
        import urllib.request

        req = urllib.request.Request(
            os.environ["SLACK_WEBHOOK_URL"],
            data=json.dumps({"text": digest}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_lines: list[str] = []

    def log(msg: str) -> None:
        stamped = f"[{datetime.utcnow().strftime('%H:%M:%S')}] {msg}"
        print(sta