"""Streamlit Web Application for Travel Reimbursement Approval Agent.

Features:
1. 🎯 Dynamic Structured Form Builder (Custom claim creation with line items, receipt flags, fare classes, per-diem counts)
2. 📋 Appendix B Sample Claims Explorer (CLM-001 to CLM-005)
3. 📄 Raw JSON Editor & File Uploader
4. 📊 Live 4-Panel Aggregate Results Dashboard
5. 📜 Structured Company Policy Rulebook (POL-CAT-01 to POL-TIME-01)
6. 🤖 Dual Engine: Instant Deterministic Safety Layer + Optional Live Gemini LangChain Agent

Run locally:
    streamlit run app.py
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from pydantic import BaseModel, ConfigDict, Field
from dotenv import load_dotenv

# Load environment keys if available
load_dotenv()

st.set_page_config(
    page_title="Travel Reimbursement Approval Agent",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Financial & Category Constants ───────────────────────────────────────────
MONEY = Decimal("0.01")
ELIGIBLE = {"airfare", "lodging", "meals", "ground_transport", "conference_fees"}
INELIGIBLE = {
    "alcohol",
    "minibar",
    "spa",
    "gym",
    "personal_entertainment",
    "in_room_movies",
    "personal_shopping",
    "gifts",
    "traffic_fines",
    "penalties",
    "late_fees",
    "personal_expense",
    "personal",
}
CAPS = {
    "meals": (Decimal("75.00"), "days", "POL-PD-01"),
    "lodging": (Decimal("200.00"), "nights", "POL-PD-02"),
    "ground_transport": (Decimal("50.00"), "days", "POL-PD-03"),
}

POLICY_RULES = {
    "POL-CAT-01": {
        "title": "Eligible Categories",
        "text": "Airfare (economy), lodging, meals, ground transport, and conference or registration fees are reimbursable for a documented business purpose.",
    },
    "POL-CAT-02": {
        "title": "Ineligible Items",
        "text": "Alcohol and minibar; spa, gym, and personal entertainment; in-room movies, personal shopping, gifts; traffic fines, penalties, late fees; and personal expenses are never reimbursable.",
    },
    "POL-PD-01": {"title": "Meal Cap", "text": "Meals are capped at $75 per day."},
    "POL-PD-02": {"title": "Lodging Cap", "text": "Lodging is capped at $200 per night."},
    "POL-PD-03": {"title": "Ground Transport Cap", "text": "Ground transport is capped at $50 per day."},
    "POL-AIR-01": {"title": "Airfare Class Restriction", "text": "Only economy airfare is reimbursable. Business or first class requires manual review because pre-approval may exist."},
    "POL-RCT-01": {"title": "Receipt Requirement", "text": "A receipt is required for any line above $25. Airfare and lodging always require a receipt regardless of amount."},
    "POL-RCT-02": {"title": "Missing Receipt Handling", "text": "Missing required receipts route a claim to manual review; they are not silently rejected."},
    "POL-APR-01": {"title": "Auto-Approve Tier", "text": "Reimbursable total at or below $500 may be auto-approved when fully compliant."},
    "POL-APR-02": {"title": "Manager Tier", "text": "Reimbursable total above $500 and at or below $2,000 is approvable when fully compliant."},
    "POL-APR-03": {"title": "Director / Manual-Review Tier", "text": "Reimbursable total above $2,000 requires manual review."},
    "POL-TIME-01": {"title": "Submission Window", "text": "Claims must be submitted within 30 days of the expense date; late claims require manual review."},
}
POLICY_ORDER = list(POLICY_RULES)

SAMPLE_CLAIMS: list[dict[str, Any]] = [
    {
        "claim_id": "CLM-001",
        "employee": "A. Rivera",
        "purpose": "Attend 2-day industry conference (business)",
        "trip_start": "2026-06-10",
        "trip_end": "2026-06-12",
        "submitted": "2026-06-20",
        "expenses": [
            {"category": "airfare", "description": "Round-trip economy airfare", "amount": "420.00", "receipt_attached": True, "fare_class": "economy"},
            {"category": "lodging", "description": "Hotel, 2 nights @ $180", "amount": "360.00", "receipt_attached": True, "nights": 2},
            {"category": "meals", "description": "Meals, 3 days @ ~$60/day", "amount": "180.00", "receipt_attached": True, "days": 3},
            {"category": "conference_fees", "description": "Conference registration", "amount": "150.00", "receipt_attached": True},
        ],
        "total_claimed": "1110.00",
    },
    {
        "claim_id": "CLM-002",
        "employee": "B. Osei",
        "purpose": "Weekend hotel stay",
        "trip_start": "2026-06-14",
        "trip_end": "2026-06-15",
        "submitted": "2026-06-25",
        "expenses": [
            {"category": "spa", "description": "Hotel spa package", "amount": "300.00", "receipt_attached": True},
            {"category": "minibar", "description": "In-room minibar", "amount": "80.00", "receipt_attached": True},
        ],
        "total_claimed": "380.00",
    },
    {
        "claim_id": "CLM-003",
        "employee": "C. Nakamura",
        "purpose": "Client site visit (business)",
        "trip_start": "2026-06-08",
        "trip_end": "2026-06-10",
        "submitted": "2026-06-22",
        "expenses": [
            {"category": "airfare", "description": "Round-trip economy airfare", "amount": "300.00", "receipt_attached": True, "fare_class": "economy"},
            {"category": "lodging", "description": "Hotel, 2 nights @ $250", "amount": "500.00", "receipt_attached": True, "nights": 2},
            {"category": "meals", "description": "Meals, 2 days @ $70/day", "amount": "140.00", "receipt_attached": True, "days": 2},
        ],
        "total_claimed": "940.00",
    },
    {
        "claim_id": "CLM-004",
        "employee": "D. Fischer",
        "purpose": "International vendor negotiation (business)",
        "trip_start": "2026-06-16",
        "trip_end": "2026-06-18",
        "submitted": "2026-06-28",
        "expenses": [
            {"category": "airfare", "description": "Business-class international airfare", "amount": "2400.00", "receipt_attached": True, "fare_class": "business"},
            {"category": "lodging", "description": "Hotel, 3 nights", "amount": "600.00", "receipt_attached": False, "nights": 3},
        ],
        "total_claimed": "3000.00",
    },
    {
        "claim_id": "CLM-005",
        "employee": "E. Haddad",
        "purpose": "Client dinner / business development",
        "trip_start": "2026-06-11",
        "trip_end": "2026-06-11",
        "submitted": "2026-06-24",
        "expenses": [
            {"category": "meals", "description": "Client dinner for 4 (business development)", "amount": "220.00", "receipt_attached": False, "days": 1},
        ],
        "total_claimed": "220.00",
    },
]

# ── Pydantic Schema ──────────────────────────────────────────────────────────
class ClaimResult(BaseModel):
    """Assignment output schema."""
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    decision: Literal["APPROVE", "PARTIAL_APPROVE", "REJECT", "MANUAL_REVIEW"]
    approved_amount: float = Field(ge=0)
    deducted_amount: float = Field(ge=0)
    missing_docs: list[str]
    policy_refs: list[str]
    confidence: float = Field(ge=0, le=1)
    explanation: str
    tools_used: list[str]

# ── Math & Calculation Utilities ─────────────────────────────────────────────
def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)

def float_money(value: Decimal) -> float:
    return float(value.quantize(MONEY, rounding=ROUND_HALF_UP))

def normalized_category(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")

# ── Deterministic Tool Implementations ───────────────────────────────────────
def policy_lookup(claim: dict[str, Any]) -> dict[str, Any]:
    categories = {normalized_category(item.get("category")) for item in claim.get("expenses", [])}
    refs = {"POL-TIME-01"}
    if categories & ELIGIBLE:
        refs.add("POL-CAT-01")
    if categories & INELIGIBLE or categories - ELIGIBLE - INELIGIBLE:
        refs.add("POL-CAT-02")
    if "airfare" in categories:
        refs.add("POL-AIR-01")
    if "meals" in categories:
        refs.add("POL-PD-01")
    if "lodging" in categories:
        refs.add("POL-PD-02")
    if "ground_transport" in categories:
        refs.add("POL-PD-03")
    if claim.get("expenses"):
        refs.update({"POL-RCT-01", "POL-RCT-02"})
    ordered = [rule_id for rule_id in POLICY_ORDER if rule_id in refs]
    return {"policy_refs": ordered, "rules": {rule_id: POLICY_RULES[rule_id] for rule_id in ordered}}

def check_receipts(claim: dict[str, Any]) -> dict[str, Any]:
    missing_docs: list[str] = []
    refs: set[str] = set()
    checked: list[str] = []
    for index, expense in enumerate(claim.get("expenses", []), start=1):
        category = normalized_category(expense.get("category"))
        try:
            amount = money(expense.get("amount"))
        except (InvalidOperation, TypeError, ValueError):
            continue
        required = category in {"airfare", "lodging"} or amount > Decimal("25.00")
        if not required:
            continue
        refs.add("POL-RCT-01")
        checked.append(f"Line {index}: {category}")
        if expense.get("receipt_attached") is not True:
            refs.add("POL-RCT-02")
            missing_docs.append(f"Itemized receipt for {category}: {expense.get('description', f'line {index}')}")
    return {
        "required_receipts_checked": checked,
        "missing_docs": missing_docs,
        "policy_refs": [rule_id for rule_id in POLICY_ORDER if rule_id in refs],
    }

def calculate_reimbursement(claim: dict[str, Any]) -> dict[str, Any]:
    claimed = Decimal("0.00")
    candidate = Decimal("0.00")
    deductions = Decimal("0.00")
    unresolved: list[str] = []
    refs: set[str] = set()
    rows: list[dict[str, Any]] = []

    for index, expense in enumerate(claim.get("expenses", []), start=1):
        category = normalized_category(expense.get("category"))
        description = str(expense.get("description") or "")
        try:
            amount = money(expense.get("amount"))
            if amount < 0:
                raise ValueError
        except (InvalidOperation, TypeError, ValueError):
            unresolved.append(f"Line {index} has an invalid or negative amount.")
            rows.append({"line": index, "category": category or "unknown", "claimed": None, "eligible": 0.0, "deducted": 0.0, "status": "unresolved"})
            continue

        claimed += amount
        eligible = Decimal("0.00")
        deducted = Decimal("0.00")
        status = "eligible"

        if category in INELIGIBLE:
            refs.add("POL-CAT-02")
            deducted = amount
            status = "ineligible"
        elif category not in ELIGIBLE:
            refs.update({"POL-CAT-01", "POL-CAT-02"})
            unresolved.append(f"Line {index} uses an unrecognized category: {category or 'missing category'}.")
            status = "unresolved"
        else:
            refs.add("POL-CAT-01")
            if category == "airfare":
                fare_class = str(expense.get("fare_class") or "").lower()
                if not fare_class:
                    fare_class = "business" if re.search(r"business[ -]class|first[ -]class", description, re.I) else "economy" if "economy" in description.lower() else "unknown"
                refs.add("POL-AIR-01")
                if fare_class in {"business", "first"}:
                    candidate = candidate + amount
                    status = "policy_exception"
                elif fare_class != "economy":
                    candidate = candidate + amount
                    unresolved.append(f"Line {index} does not identify an economy airfare class.")
                    status = "unresolved"
                else:
                    eligible = amount
            elif category in CAPS:
                cap, unit_key, rule_id = CAPS[category]
                refs.add(rule_id)
                units = expense.get(unit_key)
                if not isinstance(units, (int, float)) or units <= 0:
                    candidate = candidate + amount
                    unresolved.append(f"Line {index} has no reliable {unit_key} value for the {category} cap.")
                    status = "unresolved"
                else:
                    units_dec = Decimal(str(int(units)))
                    allowed = cap * units_dec
                    eligible = min(amount, allowed)
                    deducted = max(Decimal("0.00"), amount - allowed)
                    status = "cap_deduction" if deducted else "eligible"
            else:
                eligible = amount

        candidate += eligible
        deductions += deducted
        rows.append({
            "line": index,
            "category": category or "unknown",
            "description": description,
            "claimed": float_money(amount),
            "eligible": float_money(eligible),
            "deducted": float_money(deducted),
            "status": status,
        })

    return {
        "claimed_total": float_money(claimed),
        "candidate_reimbursable": float_money(candidate),
        "provisional_deductions": float_money(deductions),
        "unresolved": unresolved,
        "line_items": rows,
        "policy_refs": [rule_id for rule_id in POLICY_ORDER if rule_id in refs],
    }

def check_compliance(claim: dict[str, Any], calculation: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    refs: set[str] = {"POL-TIME-01"}

    for index, expense in enumerate(claim.get("expenses", []), start=1):
        category = normalized_category(expense.get("category"))
        description = str(expense.get("description") or "")
        fare_class = str(expense.get("fare_class") or "").lower()
        if category == "airfare":
            refs.add("POL-AIR-01")
            if fare_class in {"business", "first"} or re.search(r"business[ -]class|first[ -]class", description, re.I):
                reasons.append(f"Line {index} is business/first-class airfare and may need pre-approval.")

    try:
        trip_start = date.fromisoformat(str(claim.get("trip_start")))
        trip_end = date.fromisoformat(str(claim.get("trip_end")))
        submitted = date.fromisoformat(str(claim.get("submitted")))
        if trip_end < trip_start:
            reasons.append("Trip end is earlier than trip start.")
        elif submitted < trip_start:
            reasons.append("Submission date is before the trip start.")
        elif (submitted - trip_start).days > 30:
            reasons.append(f"Submission is {(submitted - trip_start).days} days after trip start, exceeding the conservative 30-day check.")
    except (TypeError, ValueError):
        reasons.append("Trip and submission dates are incomplete or invalid, so timeliness cannot be verified.")

    reported_total = claim.get("total_claimed")
    if reported_total is not None:
        try:
            if money(reported_total) != money(calculation["claimed_total"]):
                reasons.append(f"Reported total ${money(reported_total):.2f} does not match the line-item total ${money(calculation['claimed_total']):.2f}.")
        except (InvalidOperation, TypeError, ValueError):
            reasons.append("Reported total is invalid.")

    candidate = money(calculation["candidate_reimbursable"])
    if candidate > Decimal("2000.00"):
        refs.add("POL-APR-03")
        tier = "Director / manual-review tier"
        reasons.append(f"Potential reimbursable amount ${candidate:.2f} exceeds the $2,000 approval authority.")
    elif candidate > Decimal("500.00"):
        refs.add("POL-APR-02")
        tier = "Manager tier"
    else:
        refs.add("POL-APR-01")
        tier = "Auto-approve tier"

    return {
        "review_reasons": reasons,
        "approval_tier": tier,
        "policy_refs": [rule_id for rule_id in POLICY_ORDER if rule_id in refs],
        "timing_note": "Trip start is used as conservative timing check.",
    }

def evaluate_claim(claim: dict[str, Any]) -> tuple[ClaimResult, dict[str, Any]]:
    policy = policy_lookup(claim)
    receipts = check_receipts(claim)
    calculation = calculate_reimbursement(claim)
    compliance = check_compliance(claim, calculation)

    reasons = list(calculation["unresolved"]) + list(compliance["review_reasons"])
    if receipts["missing_docs"]:
        reasons.append("At least one mandatory receipt is missing.")
    reasons = list(dict.fromkeys(reasons))
    categories = [normalized_category(item.get("category")) for item in claim.get("expenses", [])]
    all_ineligible = bool(categories) and all(category in INELIGIBLE for category in categories)
    candidate = money(calculation["candidate_reimbursable"])
    provisional = money(calculation["provisional_deductions"])
    claimed = money(calculation["claimed_total"])

    refs: set[str] = set()
    for evidence in (receipts, calculation, compliance):
        refs.update(evidence.get("policy_refs", []))
    ordered_refs = [rule_id for rule_id in POLICY_ORDER if rule_id in refs]
    tools_used = ["lookup_policy", "check_receipts", "calculate_reimbursement", "check_compliance"]

    if reasons:
        decision = "MANUAL_REVIEW"
        approved = deducted = Decimal("0.00")
        confidence = 0.88 if receipts["missing_docs"] else 0.85
        explanation = (
            "Manual review is required: " + " ".join(reasons) +
            f" Potential reimbursement of ${candidate:.2f} and provisional deductions of ${provisional:.2f} "
            "are not finalized. Final amounts remain $0.00 pending human reviewer action."
        )
    elif all_ineligible:
        decision = "REJECT"
        approved, deducted, confidence = Decimal("0.00"), claimed, 0.95
        explanation = f"All claimed items are ineligible under POL-CAT-02. Full ${claimed:.2f} is rejected and deducted."
    elif provisional > 0:
        decision = "PARTIAL_APPROVE"
        approved, deducted, confidence = candidate, provisional, 0.90
        explanation = f"Claim is valid with policy deductions. Approve ${approved:.2f}; deduct ${deducted:.2f} (exceeds per-diem or ineligible items)."
    else:
        decision = "APPROVE"
        approved, deducted, confidence = candidate, Decimal("0.00"), 0.95
        explanation = f"All items eligible, all receipts present, and within {compliance['approval_tier']}. Approve ${approved:.2f} in full."

    result = ClaimResult(
        claim_id=str(claim.get("claim_id", "CUSTOM-001")),
        decision=decision,
        approved_amount=float_money(approved),
        deducted_amount=float_money(deducted),
        missing_docs=receipts["missing_docs"],
        policy_refs=ordered_refs,
        confidence=confidence,
        explanation=explanation,
        tools_used=tools_used,
    )
    audit = {
        "lookup_policy": policy,
        "check_receipts": receipts,
        "calculate_reimbursement": calculation,
        "check_compliance": compliance,
    }
    return result, audit

def decision_color(decision: str) -> str:
    return {
        "APPROVE": "#0f9d58",
        "PARTIAL_APPROVE": "#2563eb",
        "REJECT": "#d93025",
        "MANUAL_REVIEW": "#8b5cf6",
    }.get(decision, "#64748b")

def money_text(value: float | Decimal) -> str:
    return f"${float(value):,.2f}"

def render_dashboard_chart(results: list[ClaimResult], claims: list[dict[str, Any]]):
    claimed_by_id = {claim["claim_id"]: float(sum((money(item["amount"]) for item in claim.get("expenses", [])), Decimal("0.00"))) for claim in claims}
    frame = pd.DataFrame([result.model_dump() for result in results])
    frame["claimed_amount"] = frame["claim_id"].map(claimed_by_id).fillna(0.0)
    frame["pending_review"] = frame["decision"].eq("MANUAL_REVIEW")
    finalized = ~frame["pending_review"]
    counts = frame["decision"].value_counts().reindex(["APPROVE", "PARTIAL_APPROVE", "REJECT", "MANUAL_REVIEW"], fill_value=0)

    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    fig.patch.set_facecolor("#ffffff")
    palette = [decision_color(dec) for dec in counts.index]
    axes[0, 0].pie(counts.values, labels=counts.index.str.replace("_", " "), autopct="%1.0f%%", startangle=90, colors=palette)
    axes[0, 0].set_title("Claim count by decision", fontweight="bold")

    positions = range(len(frame))
    width = 0.25
    axes[0, 1].bar([x - width for x in positions], frame["claimed_amount"], width, label="Claimed", color="#64748b")
    axes[0, 1].bar(positions, frame["approved_amount"], width, label="Approved", color="#0f9d58")
    axes[0, 1].bar([x + width for x in positions], frame["deducted_amount"], width, label="Deducted", color="#d93025")
    axes[0, 1].set_xticks(list(positions), frame["claim_id"], rotation=15)
    axes[0, 1].set_ylabel("USD ($)")
    axes[0, 1].set_title("Claimed vs Finalized Amounts", fontweight="bold")
    axes[0, 1].legend(fontsize=8)
    axes[0, 1].grid(axis="y", alpha=0.25)

    axes[1, 0].axis("off")
    table_data = [
        ["Total Claimed", money_text(frame["claimed_amount"].sum())],
        ["Finalized Approved", money_text(frame.loc[finalized, "approved_amount"].sum())],
        ["Finalized Deductions", money_text(frame.loc[finalized, "deducted_amount"].sum())],
        ["Pending Manual Review", f"{int(frame['pending_review'].sum())} ({money_text(frame.loc[~finalized, 'claimed_amount'].sum())})"],
    ]
    table = axes[1, 0].table(cellText=table_data, colLabels=["Metric", "Value"], cellLoc="left", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.8)
    axes[1, 0].set_title("Summary Financial Metrics", fontweight="bold")

    confidence_colors = [decision_color(dec) for dec in frame["decision"]]
    axes[1, 1].barh(frame["claim_id"], frame["confidence"], color=confidence_colors)
    axes[1, 1].set_xlim(0, 1)
    axes[1, 1].set_xlabel("Confidence Score")
    axes[1, 1].set_title("Decision Confidence", fontweight="bold")
    axes[1, 1].grid(axis="x", alpha=0.25)
    for row in frame.itertuples():
        axes[1, 1].text(row.confidence + 0.02, row.Index, f"{row.confidence:.2f}", va="center", fontweight="bold")

    plt.tight_layout()
    return fig, frame

# ── Custom Styling ───────────────────────────────────────────────────────────
st.markdown(
    """<style>
    .main-header {padding: 0.5rem 0 1.0rem 0; border-bottom: 2px solid #e2e8f0; margin-bottom: 1.2rem;}
    .main-header h1 {margin: 0; color: #1e293b; font-size: 2.1rem;}
    .decision-badge {
        padding: 0.5rem 1.2rem;
        border-radius: 999px;
        color: white;
        display: inline-block;
        font-weight: 700;
        font-size: 1.1rem;
        letter-spacing: 0.5px;
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
    }
    .policy-pill {
        background: #f1f5f9;
        border: 1px solid #cbd5e1;
        padding: 4px 8px;
        border-radius: 6px;
        font-family: monospace;
        font-size: 0.85rem;
        margin-right: 4px;
        color: #0f172a;
    }
    </style>""",
    unsafe_allow_html=True,
)

st.markdown(
    """<div class='main-header'>
        <h1>✈️ Travel Reimbursement Approval Agent</h1>
        <p style='color: #64748b; margin: 4px 0 0 0;'>
            Intelligent corporate policy compliance evaluation with deterministic calculation safety layer & LangChain Gemini support
        </p>
    </div>""",
    unsafe_allow_html=True,
)

# ── Navigation Options ───────────────────────────────────────────────────────
NAV_PAGES = [
    "🎯 Dynamic Form Builder",
    "📋 Appendix B Samples",
    "📄 Raw JSON Editor",
    "📊 Aggregate Dashboard",
    "📜 Policy Rulebook",
]

if "current_page" not in st.session_state:
    st.session_state.current_page = NAV_PAGES[0]
if "sidebar_radio_nav" not in st.session_state:
    st.session_state.sidebar_radio_nav = NAV_PAGES[0]
if "top_segmented_nav" not in st.session_state:
    st.session_state.top_segmented_nav = NAV_PAGES[0]

def _sync_from_sidebar():
    val = st.session_state.get("sidebar_radio_nav")
    if val:
        st.session_state.current_page = val
        st.session_state.top_segmented_nav = val

def _sync_from_top():
    val = st.session_state.get("top_segmented_nav")
    if val:
        st.session_state.current_page = val
        st.session_state.sidebar_radio_nav = val
    else:
        st.session_state.top_segmented_nav = st.session_state.current_page

def navigate_to(page_name: str):
    st.session_state.current_page = page_name
    st.session_state.sidebar_radio_nav = page_name
    st.session_state.top_segmented_nav = page_name
    st.rerun()

# ── Session State for Dynamic Claims ─────────────────────────────────────────
if "dynamic_expenses" not in st.session_state:
    st.session_state.dynamic_expenses = [
        {"id": 1, "category": "airfare", "description": "Round-trip economy flight", "amount": 420.00, "receipt_attached": True, "fare_class": "economy", "nights": 1, "days": 1},
        {"id": 2, "category": "lodging", "description": "Hotel 2 nights @ $180", "amount": 360.00, "receipt_attached": True, "fare_class": "economy", "nights": 2, "days": 1},
        {"id": 3, "category": "meals", "description": "Meals 3 days @ $60/day", "amount": 180.00, "receipt_attached": True, "fare_class": "economy", "nights": 1, "days": 3},
        {"id": 4, "category": "conference_fees", "description": "Tech conference registration", "amount": 150.00, "receipt_attached": True, "fare_class": "economy", "nights": 1, "days": 1},
    ]
if "next_expense_id" not in st.session_state:
    st.session_state.next_expense_id = 5

if "evaluated_custom_claims" not in st.session_state:
    st.session_state.evaluated_custom_claims = []

# ── Sidebar Controls ─────────────────────────────────────────────────────────
with st.sidebar:
    st.header("🧭 Navigation")
    st.radio(
        "Select View / Mode:",
        NAV_PAGES,
        key="sidebar_radio_nav",
        on_change=_sync_from_sidebar,
    )

    st.divider()
    st.subheader("💡 Policy Quick Guide")
    st.markdown("""
    - **Airfare**: Economy only (`POL-AIR-01`)
    - **Lodging**: Max **$200/night** (`POL-PD-02`)
    - **Meals**: Max **$75/day** (`POL-PD-01`)
    - **Receipts**: Required for **>$25** & all air/lodging (`POL-RCT-01`)
    - **Window**: Within **30 days** of expense (`POL-TIME-01`)
    """)
    st.divider()
    st.caption("AI Developer Case Study · Candidate: Sanket Chavan")

# ── Top Navigation Bar (Segmented Tabs) ──────────────────────────────────────
st.segmented_control(
    "Navigation Views",
    NAV_PAGES,
    key="top_segmented_nav",
    on_change=_sync_from_top,
    label_visibility="collapsed",
    width="stretch",
)
st.write("")

# ══════════════════════════════════════════════════════════════════════════════
# VIEW 1: DYNAMIC FORM BUILDER
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.current_page == "🎯 Dynamic Form Builder":
    st.subheader("🎯 Interactive Structured Claim Builder")
    st.markdown(
        "Build any custom travel reimbursement claim dynamically by specifying general claim metadata "
        "and adding arbitrary expense line items with receipt attachments and per-diem counts."
    )

    # Template loader toolbar
    col_tpl, col_btn, col_rst = st.columns([3, 1.2, 1.2])
    with col_tpl:
        tpl_choice = st.selectbox(
            "⚡ Pre-fill from template baseline (optional):",
            [
                "CLM-001 (Fully Compliant Conference Claim)",
                "CLM-002 (Ineligible Spa & Minibar)",
                "CLM-003 (Meals & Lodging Exceeding Per-Diem)",
                "CLM-004 (Business-Class Exception & High Value)",
                "CLM-005 (Dinner Missing Required Receipt)",
            ],
            index=0,
            key="tpl_select_choice",
        )
    with col_btn:
        st.write("")
        st.write("")
        if st.button("📥 Load Template", width="stretch"):
            sample_idx = int(tpl_choice.split("CLM-00")[1][0]) - 1
            sample_data = SAMPLE_CLAIMS[sample_idx]
            st.session_state["dyn_claim_id"] = f"{sample_data['claim_id']}-CUSTOM"
            st.session_state["dyn_employee"] = sample_data["employee"]
            st.session_state["dyn_purpose"] = sample_data["purpose"]
            st.session_state["dyn_start"] = date.fromisoformat(sample_data["trip_start"])
            st.session_state["dyn_end"] = date.fromisoformat(sample_data["trip_end"])
            st.session_state["dyn_sub"] = date.fromisoformat(sample_data["submitted"])
            st.session_state.dynamic_expenses = [
                {
                    "id": i + 1,
                    "category": exp.get("category", "meals"),
                    "description": exp.get("description", ""),
                    "amount": float(exp.get("amount", 0.0)),
                    "receipt_attached": bool(exp.get("receipt_attached", True)),
                    "fare_class": exp.get("fare_class", "economy"),
                    "nights": exp.get("nights", 1),
                    "days": exp.get("days", 1),
                }
                for i, exp in enumerate(sample_data["expenses"])
            ]
            st.session_state.next_expense_id = len(st.session_state.dynamic_expenses) + 1
            st.rerun()

    with col_rst:
        st.write("")
        st.write("")
        if st.button("🔄 Reset Default", width="stretch"):
            st.session_state.dynamic_expenses = [
                {"id": 1, "category": "airfare", "description": "Round-trip economy flight", "amount": 420.00, "receipt_attached": True, "fare_class": "economy", "nights": 1, "days": 1},
                {"id": 2, "category": "lodging", "description": "Hotel 2 nights @ $180", "amount": 360.00, "receipt_attached": True, "fare_class": "economy", "nights": 2, "days": 1},
                {"id": 3, "category": "meals", "description": "Meals 3 days @ $60/day", "amount": 180.00, "receipt_attached": True, "fare_class": "economy", "nights": 1, "days": 3},
            ]
            st.session_state.next_expense_id = 4
            st.session_state["dyn_claim_id"] = "CLM-DYN-001"
            st.session_state["dyn_employee"] = "S. Chavan"
            st.session_state["dyn_purpose"] = "Client site visit & system integration review (business)"
            st.rerun()

    # Claim General Information Form
    st.markdown("#### 1. Claim Header & Dates")
    c1, c2, c3 = st.columns(3)
    with c1:
        dyn_claim_id = st.text_input("Claim ID", value=st.session_state.get("dyn_claim_id", "CLM-DYN-001"))
        dyn_employee = st.text_input("Employee Name", value=st.session_state.get("dyn_employee", "S. Chavan"))
    with c2:
        dyn_purpose = st.text_area("Business Purpose", value=st.session_state.get("dyn_purpose", "Client site visit & system integration review (business)"), height=105)
    with c3:
        dyn_start = st.date_input("Trip Start Date", value=st.session_state.get("dyn_start", date(2026, 6, 10)))
        dyn_end = st.date_input("Trip End Date", value=st.session_state.get("dyn_end", date(2026, 6, 12)))
        dyn_sub = st.date_input("Submission Date", value=st.session_state.get("dyn_sub", date(2026, 6, 20)))

    st.markdown("---")
    st.markdown("#### 2. Expense Line Items")

    categories_list = [
        "airfare",
        "lodging",
        "meals",
        "ground_transport",
        "conference_fees",
        "spa",
        "minibar",
        "alcohol",
        "gym",
        "personal_entertainment",
        "other",
    ]

    total_calc = Decimal("0.00")
    items_to_render = list(st.session_state.dynamic_expenses)

    # Render each expense line item
    for idx, item in enumerate(items_to_render):
        iid = item["id"]
        with st.container():
            cols = st.columns([2, 3, 1.8, 1.5, 1.8, 0.6])
            with cols[0]:
                cat_idx = categories_list.index(item["category"]) if item["category"] in categories_list else 0
                new_cat = st.selectbox(
                    f"Category #{idx+1}",
                    categories_list,
                    index=cat_idx,
                    key=f"cat_{iid}",
                    label_visibility="collapsed",
                )
                item["category"] = new_cat
            with cols[1]:
                item["description"] = st.text_input(
                    f"Desc #{idx+1}",
                    value=item.get("description", ""),
                    placeholder="Description...",
                    key=f"desc_{iid}",
                    label_visibility="collapsed",
                )
            with cols[2]:
                item["amount"] = st.number_input(
                    f"Amount #{idx+1}",
                    value=float(item.get("amount", 50.0)),
                    min_value=0.01,
                    step=25.0,
                    format="%.2f",
                    key=f"amt_{iid}",
                    label_visibility="collapsed",
                )
                total_calc += money(item["amount"])
            with cols[3]:
                item["receipt_attached"] = st.checkbox(
                    "Receipt",
                    value=bool(item.get("receipt_attached", True)),
                    key=f"rcpt_{iid}",
                )
            with cols[4]:
                if item["category"] == "airfare":
                    item["fare_class"] = st.selectbox(
                        "Class",
                        ["economy", "business", "first"],
                        index=0 if item.get("fare_class") == "economy" else (1 if item.get("fare_class") == "business" else 2),
                        key=f"fare_{iid}",
                        label_visibility="collapsed",
                    )
                elif item["category"] == "lodging":
                    item["nights"] = st.number_input(
                        "Nights",
                        value=int(item.get("nights", 1)),
                        min_value=1,
                        step=1,
                        key=f"nights_{iid}",
                        label_visibility="collapsed",
                    )
                elif item["category"] in ("meals", "ground_transport"):
                    item["days"] = st.number_input(
                        "Days",
                        value=int(item.get("days", 1)),
                        min_value=1,
                        step=1,
                        key=f"days_{iid}",
                        label_visibility="collapsed",
                    )
                else:
                    st.caption("Standard")
            with cols[5]:
                if st.button("🗑️", key=f"del_{iid}", help="Remove line item"):
                    st.session_state.dynamic_expenses = [it for it in st.session_state.dynamic_expenses if it["id"] != iid]
                    st.rerun()

    # Toolbar to add new expense item
    if st.button("➕ Add Expense Line Item", type="secondary"):
        st.session_state.dynamic_expenses.append({
            "id": st.session_state.next_expense_id,
            "category": "meals",
            "description": "Team lunch during travel",
            "amount": 65.00,
            "receipt_attached": True,
            "fare_class": "economy",
            "nights": 1,
            "days": 1,
        })
        st.session_state.next_expense_id += 1
        st.rerun()

    st.markdown("---")

    # Summary bar before evaluation
    b1, b2, b3 = st.columns([2, 2, 2])
    with b1:
        st.metric("Total Line Items", len(st.session_state.dynamic_expenses))
    with b2:
        st.metric("Computed Total Claimed", f"${total_calc:,.2f}")
    with b3:
        receipts_count = sum(1 for it in st.session_state.dynamic_expenses if it.get("receipt_attached"))
        st.metric("Receipts Present", f"{receipts_count} of {len(st.session_state.dynamic_expenses)}")

    # Assemble claim dictionary
    dynamic_claim_dict = {
        "claim_id": dyn_claim_id,
        "employee": dyn_employee,
        "purpose": dyn_purpose,
        "trip_start": dyn_start.isoformat(),
        "trip_end": dyn_end.isoformat(),
        "submitted": dyn_sub.isoformat(),
        "expenses": [
            {
                "category": it["category"],
                "description": it["description"],
                "amount": float(it["amount"]),
                "receipt_attached": bool(it["receipt_attached"]),
                **({"fare_class": it["fare_class"]} if it["category"] == "airfare" else {}),
                **({"nights": int(it["nights"])} if it["category"] == "lodging" else {}),
                **({"days": int(it["days"])} if it["category"] in ("meals", "ground_transport") else {}),
            }
            for it in st.session_state.dynamic_expenses
        ],
        "total_claimed": float(total_calc),
    }

    st.write("")
    if st.button("🚀 Evaluate Dynamic Claim with Agent", type="primary", width="stretch"):
        res, audit = evaluate_claim(dynamic_claim_dict)
        st.session_state["last_evaluated_claim"] = dynamic_claim_dict
        st.session_state["last_evaluated_result"] = res
        st.session_state["last_evaluated_audit"] = audit

    # Display evaluation results if available
    if "last_evaluated_result" in st.session_state and st.session_state.get("last_evaluated_claim"):
        res: ClaimResult = st.session_state["last_evaluated_result"]
        audit = st.session_state["last_evaluated_audit"]
        clm = st.session_state["last_evaluated_claim"]

        st.markdown("---")
        st.subheader("📋 Agent Evaluation Result")

        r_col1, r_col2 = st.columns([1.5, 3.5])
        with r_col1:
            st.markdown(
                f"<div class='decision-badge' style='background:{decision_color(res.decision)}; text-align:center; width:100%;'>"
                f"{res.decision.replace('_', ' ')}</div>",
                unsafe_allow_html=True,
            )
            st.write("")
            st.metric("Claimed", money_text(audit["calculate_reimbursement"]["claimed_total"]))
            st.metric("Approved", money_text(res.approved_amount))
            st.metric("Deducted", money_text(res.deducted_amount))
            st.metric("Confidence", f"{res.confidence:.0%}")

        with r_col2:
            st.markdown("#### Agent Explanation & Findings")
            st.info(res.explanation)

            if res.missing_docs:
                st.warning("⚠️ **Missing Mandatory Documentation:**\n- " + "\n- ".join(res.missing_docs))
            else:
                st.success("✅ **All mandatory receipts verified.**")

            st.markdown("#### Policy Citations")
            p_html = "".join([f"<span class='policy-pill'><b>{p}</b>: {POLICY_RULES.get(p, {}).get('title', '')}</span> " for p in res.policy_refs])
            st.markdown(p_html, unsafe_allow_html=True)

            st.write("")
            st.markdown("#### Line-Item Financial Breakdown")
            st.dataframe(pd.DataFrame(audit["calculate_reimbursement"]["line_items"]), hide_index=True, width='stretch')

        with st.expander("🔍 View 4-Tool Audit Trace"):
            for t_name, evidence in audit.items():
                st.markdown(f"**Tool: `{t_name}`**")
                st.json(evidence)

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            st.download_button(
                "📥 Download Decision JSON",
                data=json.dumps(res.model_dump(mode="json"), indent=2),
                file_name=f"{res.claim_id}_decision.json",
                mime="application/json",
                width='stretch',
            )
        with col_d2:
            if st.button("📊 Add This Claim to Dashboard", width='stretch'):
                if not any(c.get("claim_id") == clm["claim_id"] for c in st.session_state.evaluated_custom_claims):
                    st.session_state.evaluated_custom_claims.append(clm)
                    st.success(f"Added {clm['claim_id']} to Dashboard!")
                else:
                    st.info(f"{clm['claim_id']} is already in the Dashboard dataset.")

# ══════════════════════════════════════════════════════════════════════════════
# VIEW 2: SAMPLE CLAIMS EXPLORER
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.current_page == "📋 Appendix B Samples":
    st.subheader("📋 Appendix B Official Case Study Claims")
    st.caption("Review and evaluate the 5 benchmark claims from the HCL assignment prompt.")

    sample_labels = [f"{c['claim_id']} — {c['employee']} ({c['purpose']})" for c in SAMPLE_CLAIMS]
    sel_sample_label = st.selectbox("Select a benchmark claim:", sample_labels)
    sel_idx = sample_labels.index(sel_sample_label)
    sel_claim = SAMPLE_CLAIMS[sel_idx]

    s_res, s_audit = evaluate_claim(sel_claim)

    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric("Claim ID", sel_claim["claim_id"])
    sc2.metric("Total Claimed", f"${float(sel_claim['total_claimed']):,.2f}")
    sc3.metric("Decision", s_res.decision)
    sc4.metric("Approved Amount", f"${s_res.approved_amount:,.2f}")

    st.markdown(
        f"<div class='decision-badge' style='background:{decision_color(s_res.decision)}; margin-bottom: 12px;'>"
        f"{s_res.decision.replace('_', ' ')}</div>",
        unsafe_allow_html=True,
    )
    st.write(f"**Explanation:** {s_res.explanation}")

    s_col1, s_col2 = st.columns([1, 1])
    with s_col1:
        st.markdown("**Claim Line Items:**")
        st.dataframe(pd.DataFrame(sel_claim["expenses"]), hide_index=True, width='stretch')
    with s_col2:
        st.markdown("**Financial Safety Layer Breakdown:**")
        st.dataframe(pd.DataFrame(s_audit["calculate_reimbursement"]["line_items"]), hide_index=True, width='stretch')

    with st.expander("View Full Validated JSON Output"):
        st.json(s_res.model_dump(mode="json"))

# ══════════════════════════════════════════════════════════════════════════════
# VIEW 3: RAW JSON EDITOR & UPLOAD
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.current_page == "📄 Raw JSON Editor":
    st.subheader("📄 Raw JSON Claim Input & Upload")
    st.markdown("Paste custom claim JSON directly or upload a `.json` file to evaluate against policy rules.")

    default_json_sample = json.dumps(SAMPLE_CLAIMS[0], indent=2)

    col_j1, col_j2 = st.columns([2, 1])
    with col_j1:
        uploaded_file = st.file_uploader("Upload Claim JSON file", type=["json"])
    with col_j2:
        st.write("")
        st.write("")
        if st.button("📋 Paste Benchmark Sample (CLM-001)", width="stretch"):
            st.session_state["raw_json_input_val"] = default_json_sample
            st.rerun()

    raw_json_val = default_json_sample
    if uploaded_file is not None:
        try:
            raw_json_val = uploaded_file.read().decode("utf-8")
            st.session_state["raw_json_input_val"] = raw_json_val
        except Exception as e:
            st.error(f"Error reading uploaded file: {e}")
    elif "raw_json_input_val" in st.session_state:
        raw_json_val = st.session_state["raw_json_input_val"]

    json_text = st.text_area("Claim JSON Data", value=raw_json_val, height=350)

    try:
        parsed_claim = json.loads(json_text)
        is_valid_json = True
    except json.JSONDecodeError as err:
        st.error(f"Invalid JSON syntax: {err}")
        is_valid_json = False
        parsed_claim = None

    if is_valid_json and parsed_claim:
        if st.button("🚀 Evaluate Parsed JSON Claim", type="primary", width="stretch"):
            try:
                j_res, j_audit = evaluate_claim(parsed_claim)
                st.markdown("---")
                st.subheader(f"Results for {j_res.claim_id}")
                st.markdown(
                    f"<div class='decision-badge' style='background:{decision_color(j_res.decision)}; margin-bottom: 12px;'>"
                    f"{j_res.decision.replace('_', ' ')}</div>",
                    unsafe_allow_html=True,
                )
                st.info(j_res.explanation)

                jc1, jc2, jc3, jc4 = st.columns(4)
                jc1.metric("Claimed", money_text(j_audit["calculate_reimbursement"]["claimed_total"]))
                jc2.metric("Approved", money_text(j_res.approved_amount))
                jc3.metric("Deducted", money_text(j_res.deducted_amount))
                jc4.metric("Confidence", f"{j_res.confidence:.0%}")

                st.markdown("#### Line-Item Audit Table")
                st.dataframe(pd.DataFrame(j_audit["calculate_reimbursement"]["line_items"]), hide_index=True, width='stretch')

                with st.expander("Validated JSON Output"):
                    st.json(j_res.model_dump(mode="json"))
            except Exception as e:
                st.error(f"Error during claim evaluation: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# VIEW 4: AGGREGATE DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.current_page == "📊 Aggregate Dashboard":
    st.subheader("📊 Results Dashboard")
    st.caption("Visual distribution of decisions, amounts, and confidence across all active claims.")

    all_active_claims = list(SAMPLE_CLAIMS) + st.session_state.evaluated_custom_claims
    all_active_results = [evaluate_claim(c)[0] for c in all_active_claims]

    m1, m2, m3, m4, m5 = st.columns(5)
    total_clm = sum(float(c.get("total_claimed", 0.0)) for c in all_active_claims)
    total_app = sum(r.approved_amount for r in all_active_results if r.decision != "MANUAL_REVIEW")
    total_ded = sum(r.deducted_amount for r in all_active_results if r.decision != "MANUAL_REVIEW")
    pending = sum(1 for r in all_active_results if r.decision == "MANUAL_REVIEW")

    m1.metric("Total Claims", len(all_active_claims))
    m2.metric("Total Claimed", f"${total_clm:,.2f}")
    m3.metric("Approved Amount", f"${total_app:,.2f}")
    m4.metric("Deducted Amount", f"${total_ded:,.2f}")
    m5.metric("Pending Review", pending)

    fig, df_summary = render_dashboard_chart(all_active_results, all_active_claims)
    st.pyplot(fig, width='stretch')
    plt.close(fig)

    st.markdown("#### Summary Table")
    disp_cols = ["claim_id", "decision", "claimed_amount", "approved_amount", "deducted_amount", "confidence", "pending_review"]
    st.dataframe(df_summary[disp_cols], hide_index=True, width='stretch')

# ══════════════════════════════════════════════════════════════════════════════
# VIEW 5: POLICY RULEBOOK
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.current_page == "📜 Policy Rulebook":
    st.subheader("📜 Corporate Travel Reimbursement Policy Directory")
    st.caption("Deterministic rule registry cited by the approval agent.")

    for rule_id, rule_info in POLICY_RULES.items():
        with st.expander(f"📌 {rule_id} — {rule_info['title']}"):
            st.markdown(rule_info["text"])
