"""Extended engine tests beyond the four provided cases."""

from __future__ import annotations

from datetime import date

from feasibility.engine import evaluate_offer
from feasibility.models import (
    Client,
    CreditorRules,
    LedgerEntry,
    Offer,
    load_case,
    offer_total_cents,
)
from feasibility.solver import increment_guardrail, lump_guardrail


def _distinct_payment_levels(schedule) -> int:
    pays = [row.creditor_payment_cents for row in schedule if row.creditor_payment_cents]
    return len(set(pays))


def test_exact_sum_on_case1():
    client, offer, rules = load_case("cases/case1_feasible_even")
    r = evaluate_offer(client, offer, rules)
    assert r.feasible and r.schedule
    total = sum(row.creditor_payment_cents for row in r.schedule)
    assert total == offer_total_cents(offer)


def test_no_program_fee_before_first_creditor_payment():
    client, offer, rules = load_case("cases/case1_feasible_even")
    r = evaluate_offer(client, offer, rules)
    assert r.feasible and r.schedule
    first_creditor = min(
        row.date for row in r.schedule if row.creditor_payment_cents > 0
    )
    for row in r.schedule:
        if row.date < first_creditor:
            assert row.program_fee_cents == 0


def test_horizon_respected():
    client, offer, rules = load_case("cases/case1_feasible_even")
    r = evaluate_offer(client, offer, rules)
    assert r.feasible and r.schedule
    assert all(row.date <= client.last_draft_date for row in r.schedule)


def test_tier_floor_in_schedule():
    client, offer, rules = load_case("cases/case4_tiers")
    r = evaluate_offer(client, offer, rules)
    assert r.feasible and r.schedule
    pays = [row.creditor_payment_cents for row in r.schedule if row.creditor_payment_cents]
    assert all(p >= 5000 for p in pays[6:])


def test_balance_never_negative_all_cases():
    for case in (
        "case1_feasible_even",
        "case3_balloon",
        "case4_tiers",
    ):
        client, offer, rules = load_case(f"cases/{case}")
        r = evaluate_offer(client, offer, rules)
        assert r.feasible and r.schedule
        assert all(row.balance_cents >= 0 for row in r.schedule)


def test_infeasible_guardrails_present():
    client, offer, rules = load_case("cases/case2_infeasible_minima")
    r = evaluate_offer(client, offer, rules)
    assert r.feasible is False
    af = r.additional_funds
    assert af is not None
    assert af.lump_sum.within_guardrail is not None
    assert af.monthly_increment.within_guardrail is not None


def test_same_day_credits_before_debits():
    """Debit on a draft day still allows credit to land first."""
    client = Client(
        draft_amount_cents=10000,
        draft_day=1,
        first_draft_date=date(2026, 1, 1),
        last_draft_date=date(2026, 7, 1),
        as_of_date=date(2025, 12, 31),
        current_balance_cents=0,
        ledger=[
            LedgerEntry(date(2026, 1, 1), 10000, "credit"),
            LedgerEntry(date(2026, 2, 1), 10000, "credit"),
            LedgerEntry(date(2026, 2, 1), 15000, "debit"),
            LedgerEntry(date(2026, 3, 1), 10000, "credit"),
            LedgerEntry(date(2026, 4, 1), 10000, "credit"),
            LedgerEntry(date(2026, 5, 1), 10000, "credit"),
            LedgerEntry(date(2026, 6, 1), 10000, "credit"),
            LedgerEntry(date(2026, 7, 1), 10000, "credit"),
        ],
    )
    offer = Offer(
        creditor="BalloonCo",
        current_balance_cents=60000,
        original_balance_cents=60000,
        settlement_pct=0.5,
        first_payment_date=date(2026, 1, 31),
    )
    rules = CreditorRules(
        max_terms=6,
        max_payments=6,
        min_payment_cents=2500,
        max_token_pays=6,
        min_payment_tiers=[],
        even_pays=False,
        is_ballooning_allowed=True,
        max_segments=4,
        bank_fee_cents=0,
        program_fee_pct=0.0,
    )
    r = evaluate_offer(client, offer, rules)
    assert r.feasible is True
    assert r.pay_shape_used == "balloon"


def test_balance_hits_exactly_zero():
    client, offer, rules = load_case("cases/case1_feasible_even")
    r = evaluate_offer(client, offer, rules)
    assert r.feasible and r.schedule
    assert any(row.balance_cents == 0 for row in r.schedule)


def test_token_pay_cap_respected():
    client = Client(
        draft_amount_cents=20000,
        draft_day=1,
        first_draft_date=date(2026, 1, 1),
        last_draft_date=date(2026, 6, 1),
        as_of_date=date(2025, 12, 31),
        current_balance_cents=0,
        ledger=[
            LedgerEntry(date(2026, d, 1), 20000, "credit")
            for d in range(1, 7)
        ],
    )
    offer = Offer(
        creditor="TokenCo",
        current_balance_cents=50000,
        original_balance_cents=50000,
        settlement_pct=1.0,
        first_payment_date=date(2026, 1, 31),
    )
    rules = CreditorRules(
        max_terms=6,
        max_payments=6,
        min_payment_cents=2500,
        max_token_pays=2,
        min_payment_tiers=[],
        even_pays=False,
        is_ballooning_allowed=True,
        max_segments=4,
        bank_fee_cents=0,
        program_fee_pct=0.0,
    )
    r = evaluate_offer(client, offer, rules)
    assert r.feasible and r.schedule
    pays = [row.creditor_payment_cents for row in r.schedule if row.creditor_payment_cents]
    assert sum(1 for p in pays if p == 2500) <= 2


def test_max_segments_cap_respected():
    client, offer, rules = load_case("cases/case4_tiers")
    r = evaluate_offer(client, offer, rules)
    assert r.feasible and r.schedule
    assert _distinct_payment_levels(r.schedule) <= rules.max_segments


def test_lump_guardrail_rejects_over_limit():
    offer = Offer(
        creditor="GuardCo",
        current_balance_cents=10000,
        original_balance_cents=10000,
        settlement_pct=1.0,
    )
    within, reason = lump_guardrail(offer, 7000)
    assert within is False
    assert reason


def test_increment_guardrail_rejects_over_limit():
    client = Client(
        draft_amount_cents=5000,
        draft_day=1,
        first_draft_date=date(2026, 1, 1),
        last_draft_date=date(2026, 3, 1),
        as_of_date=date(2025, 12, 31),
        current_balance_cents=0,
        ledger=[],
    )
    within, reason = increment_guardrail(client, 11000)
    assert within is False
    assert reason
