# take-away-assignment-Retape-AI

# Settlement Feasibility & Fee Engine — Take-home

Welcome, and thanks for taking the time. The full problem is in
[`ASSIGNMENT.md`](./ASSIGNMENT.md). This README is just orientation.

## The task in one line

Given a client's escrow account, a settlement offer, and a creditor's rules,
decide whether the offer is affordable (and schedule it, collecting our fee as
early as allowed) or — if not — compute the minimum extra funding needed.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Layout

```
hiring_takehome/
├── ASSIGNMENT.md            # full specification — read this
├── feasibility/
│   ├── models.py            # data models, JSON loaders, date/EOM helpers (provided)
│   └── engine.py            # >>> implement evaluate_offer here <<< (+ Result shape)
├── cases/                   # four example cases (client.json / offer.json / creditor_rules.json)
│   ├── case1_feasible_even
│   ├── case2_infeasible_minima
│   ├── case3_balloon
│   └── case4_tiers
├── tests/
│   ├── test_smoke.py        # scaffolding sanity tests (pass out of the box)
│   └── test_cases.py        # example expectations — make these pass, then add your own
├── run.py                   # python run.py cases/<case>
└── requirements.txt
```

## Run

```bash
# evaluate a single case (prints the Result as JSON)
python run.py cases/case1_feasible_even

# tests
pytest -q
```

Out of the box, `tests/test_smoke.py` passes and `tests/test_cases.py` fails —
the latter is your target. Go beyond those four cases with your own tests.

## What to submit

Your implementation, your tests, and a short README section describing:
- your approach and the alternatives you considered,
- **your interpretation of the payment shapes** (even / staircase / balloon — we
  left these loosely defined on purpose),
- assumptions you made, and known edge cases / limitations.

Budget ~5–6 hours. Prefer a correct, well-tested core over breadth. When in
doubt, write down your assumption and keep going.

---

## Approach

The solver in `feasibility/solver.py` separates **schedule generation** from
**ledger simulation**:

1. Enumerate payment counts `k` up to `min(max_terms, max_payments)` whose
   cadence dates fit within the horizon (`last_draft_date`).
2. Build creditor payment vectors per creditor flags:
   - **Even:** equal split; remainder cents on the latest payments.
   - **Balloon:** first `k−1` payments at position floors (token/tier aware);
     final payment absorbs the remainder.
   - **Staircase:** start from the minimum feasible vector, then distribute the
     remaining cents across at most `max_segments` level groups, preferring
     later payments so early cash is free for fee collection.
3. **Fee front-loading:** walk cadence dates chronologically; after credits and
   creditor/bank debits, allocate `min(remaining_fee, available_balance)` on each
   date (including fee-only trailing cadence dates if needed).
4. Pick the feasible schedule with the largest fee collected on the earliest date.
5. If nothing is feasible, binary-search the minimum lump sum (earliest useful
   draft/payment dates) and minimum uniform draft increment separately, then
   evaluate guardrails.

`round_half_up` is implemented explicitly in `models.py` (Python's built-in
`round` uses banker's rounding).

## Payment-shape interpretation

- **Objective:** maximize early program-fee collection; creditor payments stay as
  low as rules allow early on.
- **Even (`even_pays`):** all payments equal; `k` chosen by trying all valid
  counts and keeping the best fee-front-loaded feasible schedule.
- **Balloon (`is_ballooning_allowed`):** non-final payments sit at floors;
  token pays consume the base minimum first; the balloon must still be ≥
  the prior payment and meet the final position floor (tiers can force the
  balloon above token minimum).
- **Staircase (neither flag):** at most `max_segments` distinct levels; extra
  cents above the minimum vector are pushed to later segment groups. Tier
  step-ups (e.g. payment 7+ ≥ $50) are enforced in the minimum vector before
  distributing surplus.

## Assumptions

- Offer JSON uses `current_balance_cents` (matches provided loader/cases).
- Fee-only cadence dates after the last creditor payment may carry remaining
  program fee when the first payment date cannot absorb it all.
- Same-day ordering: all ledger credits, then creditor payment, bank fee, then
  program fee.

## Known edge cases

- Existing ledger debits (case3) reduce available cash on those dates.
- EOM cadence (Jan 31 → Feb 28/29) uses helpers in `models.py`.
- Lump sum and monthly increment minima are independent and may imply different
  totals; guardrails are reported but amounts are still returned.

## Explain mode

Optional flag prints a short summary before the JSON:

```bash
python run.py cases/case4_tiers --explain
```
