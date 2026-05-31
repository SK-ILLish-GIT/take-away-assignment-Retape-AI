"""Human-readable explanation of schedule selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from feasibility.engine import Result, ScheduleRow


@dataclass(frozen=True)
class ExplainReport:
    feasible: bool
    payment_count: int | None = None
    shape: str | None = None
    level_summary: str | None = None
    fee_milestone_date: date | None = None
    fee_milestone_cents: int = 0
    fee_total_cents: int = 0
    first_failure_k: int | None = None
    first_failure_reason: str | None = None
    infeasible_note: str | None = None


def fmt_dollars(cents: int) -> str:
    if cents % 100 == 0:
        return f"${cents // 100}"
    return f"${cents / 100:.2f}"


def format_level_summary(payments: list[int], shape: str) -> str:
    if not payments:
        return "no creditor payments"
    levels = sorted(set(payments))
    if shape == "even":
        avg = sum(payments) // len(payments)
        return f"{len(payments)} equal payments (~{fmt_dollars(avg)})"
    chain = " → ".join(fmt_dollars(p) for p in levels)
    label = "level" if len(levels) == 1 else "levels"
    return f"{len(levels)} {label} ({chain})"


def fee_milestone_from_schedule(
    schedule: list[ScheduleRow],
    fee_total: int,
) -> tuple[date | None, int]:
    fee_rows = [r for r in schedule if r.program_fee_cents > 0]
    if not fee_rows or fee_total <= 0:
        return None, 0

    milestones: list[tuple[date, int]] = []
    cum = 0
    for row in fee_rows:
        cum += row.program_fee_cents
        milestones.append((row.date, cum))

    if len(milestones) == 1:
        return milestones[0]

    last_date, last_cum = milestones[-1]
    prev_date, prev_cum = milestones[-2]
    tail = last_cum - prev_cum
    if last_cum >= fee_total and tail <= max(prev_cum * 0.25, 1):
        return prev_date, prev_cum
    return last_date, last_cum


def format_explain(report: ExplainReport) -> str:
    lines: list[str] = []

    if report.feasible:
        lines.append(
            f"k={report.payment_count}, shape={report.shape}, {report.level_summary}"
        )
        if report.fee_total_cents > 0:
            month = (
                report.fee_milestone_date.strftime("%B")
                if report.fee_milestone_date
                else "?"
            )
            lines.append(
                f"Fee front-loaded: {fmt_dollars(report.fee_milestone_cents)} by {month} "
                f"(of {fmt_dollars(report.fee_total_cents)} total)"
            )
        else:
            lines.append("No program fee for this offer.")
    else:
        lines.append("No feasible schedule for any payment count k.")
        if report.infeasible_note:
            lines.append(report.infeasible_note)

    if report.first_failure_k is not None and report.first_failure_reason:
        lines.append(
            f"First infeasible attempt: k={report.first_failure_k}, "
            f"{report.first_failure_reason}"
        )

    return "\n".join(lines)
