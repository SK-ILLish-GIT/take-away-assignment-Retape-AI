"""CLI: evaluate one case folder and print the Result as JSON.

    python run.py cases/case1_feasible_even
    python run.py cases/case4_tiers --explain
"""

from __future__ import annotations

import json
import sys

from feasibility.explain import format_explain
from feasibility.models import load_case


def main(argv: list[str]) -> int:
    args = argv[1:]
    explain = False
    if "--explain" in args:
        explain = True
        args.remove("--explain")

    if len(args) != 1:
        print("usage: python run.py <case_dir> [--explain]", file=sys.stderr)
        return 2

    client, offer, rules = load_case(args[0])

    if explain:
        from feasibility.solver import evaluate_with_explanation

        result, report = evaluate_with_explanation(client, offer, rules)
        print(format_explain(report))
        print()
    else:
        from feasibility.engine import evaluate_offer

        result = evaluate_offer(client, offer, rules)

    print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
