"""Streamlit demo for the Travel Reimbursement Approval Agent.

Run locally from this folder:
    streamlit run app.py

The submitted notebook remains the end-to-end LangChain/Gemini implementation.
This app is a demo surface: it runs the policy decision layer locally so sample
claim reviews are repeatable even when a Gemini API call is unavailable.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from pydantic import BaseModel, ConfigDict, Field


st.set_page_config(
    page_title="Travel Reimbursement Approval Agent",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded",
)


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
        "title": "Eligible categories",
        "text": "Airfare (economy), lodging, meals, ground transport, and conference or registration fees are reimbursable for a documented business purpose.",
    },
    "POL-CAT-02": {
        "title": "Ineligible items",
        "text": "Alcohol and minibar; spa, gym, and personal entertainment; in-room movies, personal shopping, gifts; traffic fines, penalties, late fees; and personal expenses are never reimbursable.",
    },
    "POL-PD-01": {"title": "Meal cap", "text": "Meals are capped at $75 per day."},
    "POL-PD-02": {"title": "Lodging cap", "text": "Lodging is capped at $200 per night."},
    "POL-PD-03": {"title": "Ground transport cap", "text": "Ground transport is capped at $50 per day."},
    "POL-AIR-01": {"title": "Airfare class", "text": "Only economy airfare is reimbursable. Business or first class requires manual review because pre-approval may exist."},
    "POL-RCT-01": {"title": "Receipt requirement", "text": "A receipt is required for any line above $25. Airfare and lodging always require a receipt."},
    "POL-RCT-02": {"title": "Missing receipt", "text": "Missing required receipts route a claim to manual review; they are not silently rejected."},
    "POL-APR-01": {"title": "Auto-approve tier", "text": "Reimbursable total at or below $500 may be auto-approved when compliant."},
    "POL-APR-02": {"title": "Manager tier", "text": "Reimbursable total above $500 and at or below $2,000 is approvable when compliant."},
    "POL-APR-03": {"title": "Director/manual-review tier", "text": "Reimbursable total above $2,000 requires manual review."},
    "POL-TIME-01": {"title": "Submission window", "text": "Claims must be submitted within 30 days of the expense date; late claims require manual review."},
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


class ClaimResult(BaseModel):
    """Exact final-output schema from the assignment."""

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


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def float_money(value: Decimal) -> float:
    return float(value.quantize(MONEY, rounding=ROUND_HALF_UP))


def normalized_category(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def policy_lookup(claim: dict[str, Any]) -> dict[str, Any]:
    """Return policy rules relevant to the current input claim."""
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
    """Validate receipts from the provided attachment flags."""
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
    """Perform deterministic policy calculation; never delegates financial math to an LLM."""
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
                if not isinstance(units, int) or units <= 0:
                    candidate = candidate + amount
                    unresolved.append(f"Line {index} has no reliable {unit_key} value for the {category} cap.")
                    status = "unresolved"
                else:
                    allowed = cap * units
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
    """Check exception conditions, timing, data consistency, and approval tier."""
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
        tier = "Director/manual-review tier"
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
        "timing_note": "Trip start is used as the conservative timing proxy because individual expense dates are not supplied.",
    }


def evaluate_claim(claim: dict[str, Any]) -> tuple[ClaimResult, dict[str, Any]]:
    """Call each local tool and apply deterministic decision precedence."""
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

    # The lookup can retrieve broad context. Final citations include only rules
    # that affected a receipt, calculation, or compliance outcome.
    refs: set[str] = set()
    for evidence in (receipts, calculation, compliance):
        refs.update(evidence.get("policy_refs", []))
    ordered_refs = [rule_id for rule_id in POLICY_ORDER if rule_id in refs]
    tools_used = ["lookup_policy", "check_receipts", "calculate_reimbursement", "check_compliance"]

    if reasons:
        decision = "MANUAL_REVIEW"
        approved = deducted = Decimal("0.00")
        confidence = 0.88 if receipts["missing_docs"] else 0.82
        explanation = (
            "Manual review is required: " + " ".join(reasons) +
            f" Potential reimbursement of ${candidate:.2f} and known provisional deductions of ${provisional:.2f} "
            "are not finalized. Final approved and deducted amounts remain $0.00 pending reviewer action. "
            + compliance["timing_note"]
        )
    elif all_ineligible:
        decision = "REJECT"
        approved, deducted, confidence = Decimal("0.00"), claimed, 0.92
        explanation = f"All claimed items are ineligible under POL-CAT-02. The full ${claimed:.2f} is rejected and deducted. {compliance['timing_note']}"
    elif provisional > 0:
        decision = "PARTIAL_APPROVE"
        approved, deducted, confidence = candidate, provisional, 0.88
        explanation = f"The claim is valid with policy-cap or ineligible-item deductions. Approve ${approved:.2f}; deduct ${deducted:.2f}. {compliance['timing_note']}"
    else:
        decision = "APPROVE"
        approved, deducted, confidence = candidate, Decimal("0.00"), 0.90
        explanation = f"All items are eligible, receipt statuses are complete, and the claim is within the {compliance['approval_tier'].lower()}. Approve ${approved:.2f}. {compliance['timing_note']}"

    result = ClaimResult(
        claim_id=str(claim.get("claim_id", "CUSTOM")),
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
        "PARTIAL_APPROVE": "#e3a008",
        "REJECT": "#d93025",
        "MANUAL_REVIEW": "#6f42c1",
    }[decision]


def money_text(value: float | Decimal) -> str:
    return f"${float(value):,.2f}"


def dashboard(results: list[ClaimResult], claims: list[dict[str, Any]]) -> None:
    claimed_by_id = {claim["claim_id"]: float(sum((money(item["amount"]) for item in claim["expenses"]), Decimal("0.00"))) for claim in claims}
    frame = pd.DataFrame([result.model_dump() for result in results])
    frame["claimed_amount"] = frame["claim_id"].map(claimed_by_id)
    frame["pending_review"] = frame["decision"].eq("MANUAL_REVIEW")
    finalized = ~frame["pending_review"]
    counts = frame["decision"].value_counts().reindex(["APPROVE", "PARTIAL_APPROVE", "REJECT", "MANUAL_REVIEW"], fill_value=0)

    metrics = st.columns(5)
    metrics[0].metric("Claims", len(frame))
    metrics[1].metric("Total claimed", money_text(frame["claimed_amount"].sum()))
    metrics[2].metric("Finalized approved", money_text(frame.loc[finalized, "approved_amount"].sum()))
    metrics[3].metric("Finalized deductions", money_text(frame.loc[finalized, "deducted_amount"].sum()))
    metrics[4].metric("Pending review", int(frame["pending_review"].sum()))
    st.caption("Finalized amounts exclude claims awaiting manual review. Pending claimed amount: " + money_text(frame.loc[~finalized, "claimed_amount"].sum()))

    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    fig.patch.set_facecolor("#ffffff")
    palette = [decision_color(decision) for decision in counts.index]
    axes[0, 0].pie(counts.values, labels=counts.index.str.replace("_", " "), autopct="%1.0f%%", startangle=90, colors=palette)
    axes[0, 0].set_title("Claim count by decision")

    positions = range(len(frame))
    width = 0.25
    axes[0, 1].bar([x - width for x in positions], frame["claimed_amount"], width, label="Claimed", color="#64748b")
    axes[0, 1].bar(positions, frame["approved_amount"], width, label="Finalized approved", color="#0f9d58")
    axes[0, 1].bar([x + width for x in positions], frame["deducted_amount"], width, label="Finalized deducted", color="#d93025")
    axes[0, 1].set_xticks(list(positions), frame["claim_id"])
    axes[0, 1].set_ylabel("USD")
    axes[0, 1].set_title("Claimed vs finalized amounts")
    axes[0, 1].legend(fontsize=8)
    axes[0, 1].grid(axis="y", alpha=0.25)

    axes[1, 0].axis("off")
    table_data = [
        ["Total claimed", money_text(frame["claimed_amount"].sum())],
        ["Finalized approved", money_text(frame.loc[finalized, "approved_amount"].sum())],
        ["Finalized deductions", money_text(frame.loc[finalized, "deducted_amount"].sum())],
        ["Pending manual review", f"{int(frame['pending_review'].sum())} ({money_text(frame.loc[~finalized, 'claimed_amount'].sum())})"],
    ]
    table = axes[1, 0].table(cellText=table_data, colLabels=["Metric", "Value"], cellLoc="left", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.7)
    axes[1, 0].set_title("Summary metrics")

    confidence_colors = [decision_color(decision) for decision in frame["decision"]]
    axes[1, 1].barh(frame["claim_id"], frame["confidence"], color=confidence_colors)
    axes[1, 1].set_xlim(0, 1)
    axes[1, 1].set_xlabel("Confidence in recommendation/routing")
    axes[1, 1].set_title("Decision confidence")
    axes[1, 1].grid(axis="x", alpha=0.25)
    for row in frame.itertuples():
        axes[1, 1].text(row.confidence + 0.015, row.Index, f"{row.confidence:.2f}", va="center")

    plt.tight_layout()
    st.pyplot(fig, width="stretch")
    plt.close(fig)

    display_frame = frame[["claim_id", "decision", "claimed_amount", "approved_amount", "deducted_amount", "pending_review"]].copy()
    for column in ("claimed_amount", "approved_amount", "deducted_amount"):
        display_frame[column] = display_frame[column].map(money_text)
    st.dataframe(display_frame, hide_index=True, width="stretch")


st.markdown(
    """<style>
    .hero {padding: 0.6rem 0 1.1rem 0;}
    .hero h1 {margin-bottom: 0.15rem;}
    .decision {padding: 0.4rem 0.7rem; border-radius: 999px; color: white; display: inline-block; font-weight: 700;}
    .muted {color: #5f6368;}
    </style>""",
    unsafe_allow_html=True,
)
st.markdown("<div class='hero'><h1>✈️ Travel Reimbursement Approval Agent</h1><div class='muted'>Interactive Streamlit showcase for the five Appendix B claims</div></div>", unsafe_allow_html=True)
st.info("Demo mode uses the deterministic policy layer for repeatable results. The companion notebook remains the LangChain + Gemini tool-calling implementation.")

label_to_claim = {f"{claim['claim_id']} — {claim['purpose']}": claim for claim in SAMPLE_CLAIMS}
with st.sidebar:
    st.header("Claim input")
    mode = st.radio("Source", ["Assignment sample", "Custom JSON"], index=0)
    if mode == "Assignment sample":
        selected_label = st.selectbox("Choose a sample claim", list(label_to_claim))
        active_claim = label_to_claim[selected_label]
    else:
        template = json.dumps(SAMPLE_CLAIMS[0], indent=2)
        raw_claim = st.text_area("Claim JSON", value=template, height=500)
        try:
            active_claim = json.loads(raw_claim)
        except json.JSONDecodeError as error:
            active_claim = None
            st.error(f"Invalid JSON: {error.msg}")
    st.divider()
    st.caption("Custom input uses the notebook claim shape: `claim_id`, dates, `expenses`, receipts, day/night counts, and `total_claimed`.")

review_tab, dashboard_tab, policy_tab = st.tabs(["Claim review", "Dashboard", "Policy registry"])

with review_tab:
    if active_claim is None:
        st.stop()
    try:
        result, audit = evaluate_claim(active_claim)
    except Exception as error:
        st.error(f"The claim could not be evaluated: {type(error).__name__}: {error}")
        st.stop()

    st.subheader(f"{result.claim_id} · {active_claim.get('purpose', 'Travel reimbursement claim')}")
    st.markdown(f"<span class='decision' style='background:{decision_color(result.decision)}'>{result.decision.replace('_', ' ')}</span>", unsafe_allow_html=True)
    columns = st.columns(4)
    columns[0].metric("Claimed", money_text(audit["calculate_reimbursement"]["claimed_total"]))
    columns[1].metric("Approved", money_text(result.approved_amount))
    columns[2].metric("Deducted", money_text(result.deducted_amount))
    columns[3].metric("Confidence", f"{result.confidence:.0%}")

    metadata = pd.DataFrame([{
        "Employee": active_claim.get("employee", "—"),
        "Trip": f"{active_claim.get('trip_start', '—')} to {active_claim.get('trip_end', '—')}",
        "Submitted": active_claim.get("submitted", "—"),
        "Approval tier": audit["check_compliance"]["approval_tier"],
    }])
    st.dataframe(metadata, hide_index=True, width="stretch")

    detail_tab, audit_tab, json_tab = st.tabs(["Decision details", "Tool audit", "Validated JSON"])
    with detail_tab:
        st.write(result.explanation)
        if result.missing_docs:
            st.warning("Missing documents: " + "; ".join(result.missing_docs))
        else:
            st.success("No missing mandatory receipts reported for this claim.")
        st.caption("Policy references: " + ", ".join(result.policy_refs))
        st.subheader("Line-item evaluation")
        st.dataframe(pd.DataFrame(audit["calculate_reimbursement"]["line_items"]), hide_index=True, width="stretch")
    with audit_tab:
        st.caption("The app invokes four local equivalents of the notebook tools on the selected claim.")
        for tool_name, evidence in audit.items():
            with st.expander(tool_name, expanded=(tool_name == "check_compliance")):
                st.json(evidence)
    with json_tab:
        st.json(result.model_dump(mode="json"))
        st.download_button(
            "Download decision JSON",
            data=json.dumps(result.model_dump(mode="json"), indent=2),
            file_name=f"{result.claim_id}_decision.json",
            mime="application/json",
        )

with dashboard_tab:
    st.subheader("All sample claims")
    all_results = [evaluate_claim(claim)[0] for claim in SAMPLE_CLAIMS]
    dashboard(all_results, SAMPLE_CLAIMS)

with policy_tab:
    st.subheader("Structured policy registry")
    st.caption("Stable POL-* references used in outputs and audit evidence.")
    for rule_id, rule in POLICY_RULES.items():
        with st.expander(f"{rule_id} · {rule['title']}"):
            st.write(rule["text"])
