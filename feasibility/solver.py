"""Schedule generation, ledger simulation, and minimum-funds search."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import combinations

from feasibility.engine import (
    AdditionalFunds,
    FundsOption,
    Result,
    ScheduleRow,
)
from feasibility.models import (
    Client,
    CreditorRules,
    Offer,
    default_first_payment_date,
    monthly_payment_dates,
    offer_total_cents,
    program_fee_cents,
    round_half_up,
)


@dataclass(frozen=True)
class SimResult:
    ok: bool
    rows: list[ScheduleRow]
    fee_first_date: date | None
    fee_first_amount: int
    failure_date: date | None = None
    failure_reason: str | None = None


def resolve_first_payment_date(client: Client, offer: Offer) -> date:
    return offer.first_payment_date or default_first_payment_date(client)


def max_creditor_payments(client: Client, offer: Offer, rules: CreditorRules) -> int:
    start = resolve_first_payment_date(client, offer)
    horizon = client.last_draft_date
    cap = min(rules.max_payments, rules.max_terms)
    count = 0
    for i in range(cap):
        d = monthly_payment_dates(start, i + 1)[-1]
        if d > horizon:
            break
        count += 1
    return count


def cadence_dates(client: Client, offer: Offer, k: int) -> list[date]:
    start = resolve_first_payment_date(client, offer)
    dates = monthly_payment_dates(start, k)
    horizon = client.last_draft_date
    return [d for d in dates if d <= horizon]


def tier_floor(payment_num: int, rules: CreditorRules) -> int:
    floor = rules.min_payment_cents
    for from_pay, min_c in rules.min_payment_tiers:
        if payment_num >= from_pay:
            floor = max(floor, min_c)
    return floor


def min_allowed_payment(payment_num: int, token_used: int, rules: CreditorRules) -> int:
    floor = tier_floor(payment_num, rules)
    if token_used >= rules.max_token_pays:
        floor = max(floor, rules.min_payment_cents + 1)
    return floor


def count_tokens(payments: list[int], rules: CreditorRules) -> int:
    return sum(1 for p in payments if p == rules.min_payment_cents)


def validate_payments(payments: list[int], rules: CreditorRules) -> bool:
    token_used = 0
    prev = 0
    for i, p in enumerate(payments):
        mn = min_allowed_payment(i + 1, token_used, rules)
        if p < mn:
            return False
        if p < prev:
            return False
        if p == rules.min_payment_cents:
            token_used += 1
        prev = p
    return True


def distinct_segments(payments: list[int]) -> int:
    if not payments:
        return 0
    segments = 1
    for i in range(1, len(payments)):
        if payments[i] != payments[i - 1]:
            segments += 1
    return segments


def build_even_payments(offer_total: int, k: int, rules: CreditorRules) -> list[int] | None:
    base = offer_total // k
    rem = offer_total % k
    payments = [base] * k
    for i in range(rem):
        payments[k - 1 - i] += 1
    if not validate_payments(payments, rules):
        return None
    return payments


def build_balloon_payments(offer_total: int, k: int, rules: CreditorRules) -> list[int] | None:
    if k < 1:
        return None
    payments: list[int] = []
    token_used = 0
    for i in range(k - 1):
        mn = min_allowed_payment(i + 1, token_used, rules)
        payments.append(mn)
        if mn == rules.min_payment_cents:
            token_used += 1
    last = offer_total - sum(payments)
    mn_last = min_allowed_payment(k, token_used, rules)
    if last < mn_last:
        return None
    if k > 1 and last < payments[-1]:
        return None
    payments.append(last)
    if not validate_payments(payments, rules):
        return None
    return payments


def _min_payment_vector(k: int, rules: CreditorRules) -> list[int] | None:
    payments: list[int] = []
    token_used = 0
    for i in range(k):
        mn = min_allowed_payment(i + 1, token_used, rules)
        payments.append(mn)
        if mn == rules.min_payment_cents:
            token_used += 1
    return payments


def _distribute_extra(
    payments: list[int],
    extra: int,
    max_segments: int,
    rules: CreditorRules,
) -> list[int] | None:
    """Add ``extra`` cents to payments, preferring later indices, within segment cap."""
    if extra < 0:
        return None
    if extra == 0:
        return payments[:]

    result = payments[:]
    n = len(result)
    best: list[int] | None = None

    # Try every way to split indices into <= max_segments non-empty groups.
    for num_segments in range(1, min(max_segments, n) + 1):
        for cuts in combinations(range(1, n), num_segments - 1):
            bounds = [0, *cuts, n]
            groups = [list(range(bounds[i], bounds[i + 1])) for i in range(num_segments)]
            trial = result[:]
            remaining = extra
            # Fill from last group backward so earlier payments stay low.
            for group in reversed(groups):
                if not group or remaining <= 0:
                    continue
                per = remaining // len(group)
                rem = remaining % len(group)
                for idx in group:
                    trial[idx] += per
                for j in range(rem):
                    trial[group[-1 - j]] += 1
                remaining = 0
            if remaining != 0:
                continue
            if not validate_payments(trial, rules):
                continue
            if distinct_segments(trial) > max_segments:
                continue
            if best is None or trial[-1] > best[-1] or (
                trial[-1] == best[-1] and trial < best
            ):
                best = trial
    return best


def build_staircase_payments(
    offer_total: int,
    k: int,
    rules: CreditorRules,
) -> list[int] | None:
    mins = _min_payment_vector(k, rules)
    if mins is None:
        return None
    base_sum = sum(mins)
    if base_sum > offer_total:
        return None
    return _distribute_extra(mins, offer_total - base_sum, rules.max_segments, rules)


def pay_shape_for(rules: CreditorRules) -> str:
    if rules.even_pays:
        return "even"
    if rules.is_ballooning_allowed:
        return "balloon"
    return "staircase"


def build_creditor_payments(
    offer_total: int,
    k: int,
    rules: CreditorRules,
) -> list[int] | None:
    if rules.even_pays:
        return build_even_payments(offer_total, k, rules)
    if rules.is_ballooning_allowed:
        return build_balloon_payments(offer_total, k, rules)
    return build_staircase_payments(offer_total, k, rules)


def future_draft_dates(client: Client) -> list[date]:
    return sorted(
        e.date
        for e in client.ledger
        if e.type == "credit" and e.date > client.as_of_date
    )


def ledger_credits(client: Client, extra_per_draft: int = 0) -> dict[date, int]:
    credits: dict[date, int] = {}
    for e in client.ledger:
        if e.date <= client.as_of_date:
            continue
        if e.type == "credit":
            add = extra_per_draft if e.date in future_draft_dates(client) else 0
            credits[e.date] = credits.get(e.date, 0) + e.amount_cents + add
        elif e.type == "debit":
            credits[e.date] = credits.get(e.date, 0) - e.amount_cents
    return credits


def apply_lump_sum(
    net_by_date: dict[date, int],
    lump_date: date,
    lump_amount: int,
) -> dict[date, int]:
    out = dict(net_by_date)
    out[lump_date] = out.get(lump_date, 0) + lump_amount
    return out



def simulate_schedule(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
    payment_dates: list[date],
    creditor_payments: list[int],
    extra_per_draft: int = 0,
    lump_date: date | None = None,
    lump_amount: int = 0,
) -> SimResult:
    horizon = client.last_draft_date
    fee_total = program_fee_cents(offer, rules)
    start = resolve_first_payment_date(client, offer)

    # Creditor plan keyed by date.
    creditor_by_date = dict(zip(payment_dates, creditor_payments))

    # Candidate cadence dates: payment dates plus trailing fee-only EOM dates through horizon.
    cadence: list[date] = list(payment_dates)
    if fee_total > 0:
        last = payment_dates[-1]
        idx = 1
        while True:
            nxt = monthly_payment_dates(start, len(payment_dates) + idx)[-1]
            if nxt <= last:
                idx += 1
                continue
            if nxt > horizon:
                break
            cadence.append(nxt)
            idx += 1
    cadence = sorted(set(cadence))

    net = ledger_credits(client, extra_per_draft)
    if lump_date is not None and lump_amount:
        net = apply_lump_sum(net, lump_date, lump_amount)

    remaining_fee = fee_total
    balance = client.current_balance_cents
    rows: list[ScheduleRow] = []
    fee_first_date: date | None = None
    fee_first_amount = 0

    all_dates = sorted(set(net.keys()) | set(cadence))
    for d in all_dates:
        if d > horizon:
            if net.get(d, 0) != 0 or d in cadence:
                return SimResult(
                    False,
                    [],
                    None,
                    0,
                    failure_reason="activity scheduled past horizon",
                )
            continue

        balance += net.get(d, 0)

        if d not in cadence:
            continue

        creditor = creditor_by_date.get(d, 0)
        bank = rules.bank_fee_cents if creditor > 0 else 0
        balance -= creditor + bank

        fee = 0
        if d >= start and remaining_fee > 0:
            fee = min(remaining_fee, max(0, balance))
            remaining_fee -= fee
            balance -= fee

        if fee > 0 and fee_first_date is None:
            fee_first_date = d
            fee_first_amount = fee

        if creditor > 0 or fee > 0 or bank > 0:
            rows.append(
                ScheduleRow(
                    date=d,
                    creditor_payment_cents=creditor,
                    program_fee_cents=fee,
                    bank_fee_cents=bank,
                    balance_cents=balance,
                )
            )

        if balance < 0:
            return SimResult(
                False,
                rows,
                fee_first_date,
                fee_first_amount,
                failure_date=d,
                failure_reason=f"negative balance on {d.isoformat()}",
            )

    if remaining_fee > 0:
        fail_date = rows[-1].date if rows else start
        return SimResult(
            False,
            rows,
            fee_first_date,
            fee_first_amount,
            failure_date=fail_date,
            failure_reason="could not collect full program fee by horizon",
        )

    return SimResult(True, rows, fee_first_date, fee_first_amount)


def schedule_score(sim: SimResult) -> tuple:
    """Higher is better: earlier/larger first fee, then fewer creditor dollars early."""
    if not sim.ok:
        return (-1,)
    first = sim.fee_first_date or date.max
    return (sim.fee_first_amount, -first.toordinal())


def try_feasible(client: Client, offer: Offer, rules: CreditorRules) -> Result | None:
    result, _ = _search_feasible(client, offer, rules)
    return result


def _search_feasible(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
) -> tuple[Result | None, dict]:
    """Return best feasible result and search metadata for explain mode."""
    total = offer_total_cents(offer)
    max_k = max_creditor_payments(client, offer, rules)
    best: tuple[tuple, Result] | None = None
    best_k: int | None = None
    best_payments: list[int] | None = None
    first_failure_k: int | None = None
    first_failure_reason: str | None = None

    for k in range(max_k, 0, -1):
        dates = cadence_dates(client, offer, k)
        if len(dates) != k:
            continue
        payments = build_creditor_payments(total, k, rules)
        if payments is None or sum(payments) != total:
            continue
        sim = simulate_schedule(client, offer, rules, dates, payments)
        if not sim.ok:
            if first_failure_k is None:
                first_failure_k = k
                first_failure_reason = sim.failure_reason or "simulation failed"
            continue
        result = Result(
            feasible=True,
            pay_shape_used=pay_shape_for(rules),
            schedule=sim.rows,
            additional_funds=None,
        )
        score = schedule_score(sim)
        if best is None or score > best[0]:
            best = (score, result)
            best_k = k
            best_payments = payments

    meta = {
        "best_k": best_k,
        "best_payments": best_payments,
        "first_failure_k": first_failure_k,
        "first_failure_reason": first_failure_reason,
    }
    return (best[1] if best else None), meta


def lump_guardrail(offer: Offer, amount: int) -> tuple[bool, str]:
    limit = round_half_up(0.65 * offer_total_cents(offer))
    if amount > limit:
        return False, f"exceeds 65% of offer total ({limit} cents)"
    return True, ""


def increment_guardrail(client: Client, amount: int) -> tuple[bool, str]:
    limit = max(10000, round_half_up(0.40 * client.draft_amount_cents))
    if amount > limit:
        return False, f"exceeds increment guardrail ({limit} cents)"
    return True, ""


def can_feasible_with_adjustment(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
    extra_per_draft: int = 0,
    lump_date: date | None = None,
    lump_amount: int = 0,
) -> bool:
    total = offer_total_cents(offer)
    max_k = max_creditor_payments(client, offer, rules)
    for k in range(1, max_k + 1):
        dates = cadence_dates(client, offer, k)
        if len(dates) != k:
            continue
        payments = build_creditor_payments(total, k, rules)
        if payments is None or sum(payments) != total:
            continue
        sim = simulate_schedule(
            client,
            offer,
            rules,
            dates,
            payments,
            extra_per_draft=extra_per_draft,
            lump_date=lump_date,
            lump_amount=lump_amount,
        )
        if sim.ok:
            return True
    return False


def min_lump_sum(client: Client, offer: Offer, rules: CreditorRules) -> FundsOption:
    horizon = client.last_draft_date
    candidate_dates = sorted(
        set(future_draft_dates(client))
        | {resolve_first_payment_date(client, offer), client.first_draft_date}
    )
    candidate_dates = [d for d in candidate_dates if d <= horizon]

    best_amount: int | None = None
    best_date: date | None = None

    for d in candidate_dates:
        lo, hi = 0, offer_total_cents(offer) + program_fee_cents(offer, rules)
        if not can_feasible_with_adjustment(client, offer, rules, lump_date=d, lump_amount=hi):
            hi *= 2
        while lo < hi:
            mid = (lo + hi) // 2
            if can_feasible_with_adjustment(client, offer, rules, lump_date=d, lump_amount=mid):
                hi = mid
            else:
                lo = mid + 1
        if can_feasible_with_adjustment(client, offer, rules, lump_date=d, lump_amount=lo):
            if best_amount is None or lo < best_amount or (lo == best_amount and d < best_date):
                best_amount = lo
                best_date = d

    amount = best_amount if best_amount is not None else 0
    within, reason = lump_guardrail(offer, amount)
    return FundsOption(
        amount_cents=amount,
        within_guardrail=within,
        reason=reason,
        date=best_date or client.first_draft_date,
    )


def min_monthly_increment(client: Client, offer: Offer, rules: CreditorRules) -> FundsOption:
    drafts = future_draft_dates(client)
    n = len(drafts)
    hi = max(client.draft_amount_cents, 10000)
    while not can_feasible_with_adjustment(client, offer, rules, extra_per_draft=hi):
        hi *= 2
        if hi > 10_000_000:
            break
    lo = 0
    while lo < hi:
        mid = (lo + hi) // 2
        if can_feasible_with_adjustment(client, offer, rules, extra_per_draft=mid):
            hi = mid
        else:
            lo = mid + 1
    amount = lo if can_feasible_with_adjustment(client, offer, rules, extra_per_draft=lo) else 0
    within, reason = increment_guardrail(client, amount)
    return FundsOption(
        amount_cents=amount,
        within_guardrail=within,
        reason=reason,
        num_drafts=n,
    )


def evaluate(client: Client, offer: Offer, rules: CreditorRules) -> Result:
    feasible = try_feasible(client, offer, rules)
    if feasible is not None:
        return feasible

    lump = min_lump_sum(client, offer, rules)
    inc = min_monthly_increment(client, offer, rules)
    return Result(
        feasible=False,
        pay_shape_used=None,
        schedule=None,
        additional_funds=AdditionalFunds(lump_sum=lump, monthly_increment=inc),
    )


def evaluate_with_explanation(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
) -> tuple[Result, "ExplainReport"]:
    from feasibility.explain import (
        ExplainReport,
        fee_milestone_from_schedule,
        format_level_summary,
    )

    fee_total = program_fee_cents(offer, rules)
    feasible, meta = _search_feasible(client, offer, rules)

    if feasible is not None:
        shape = feasible.pay_shape_used or pay_shape_for(rules)
        payments = meta["best_payments"] or []
        milestone_date, milestone_cents = fee_milestone_from_schedule(
            feasible.schedule or [],
            fee_total,
        )
        report = ExplainReport(
            feasible=True,
            payment_count=meta["best_k"],
            shape=shape,
            level_summary=format_level_summary(payments, shape),
            fee_milestone_date=milestone_date,
            fee_milestone_cents=milestone_cents,
            fee_total_cents=fee_total,
            first_failure_k=meta["first_failure_k"],
            first_failure_reason=meta["first_failure_reason"],
        )
        return feasible, report

    result = evaluate(client, offer, rules)
    af = result.additional_funds
    note = None
    if af is not None:
        note = (
            f"Minimum extra funding: lump {af.lump_sum.amount_cents} cents on "
            f"{af.lump_sum.date}, or +{af.monthly_increment.amount_cents} cents "
            f"on each of {af.monthly_increment.num_drafts} drafts."
        )
    report = ExplainReport(
        feasible=False,
        fee_total_cents=fee_total,
        first_failure_k=meta["first_failure_k"],
        first_failure_reason=meta["first_failure_reason"],
        infeasible_note=note,
    )
    return result, report
