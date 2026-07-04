"""Run the scripted conversation suite and print the results table.

Usage: uv run python -m evals.run_evals [scenario-name-substring]
Makes real LLM calls; a full run takes a few minutes."""

import sys

from pydantic_ai.exceptions import ModelHTTPError

from evals.harness import run_scenario
from evals.scenarios import SCENARIOS


def main() -> int:
    selector = sys.argv[1] if len(sys.argv) > 1 else ""
    chosen = [s for s in SCENARIOS if selector.lower() in s.name.lower()]

    results = []
    for i, scenario in enumerate(chosen):
        try:
            ok, details, _ = run_scenario(scenario, i)
        except ModelHTTPError:
            # a 503 demand spike outlasted the per-call retries; one fresh attempt
            # for the whole scenario before letting the run die
            print(f"[retry] {scenario.name}: transient model error, rerunning")
            ok, details, _ = run_scenario(scenario, i)
        results.append((scenario, ok))
        marker = "PASS" if ok else "FAIL"
        print(f"[{marker}] {scenario.name}")
        if not ok:
            for line in details:
                print("   " + line.replace("\n", "\n   "))
        print()

    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    recov = [(s, ok) for s, ok in results if s.recovery]
    recov_passed = sum(1 for _, ok in recov if ok)
    print("=" * 50)
    print(f"task success: {passed}/{total}")
    if recov:
        print(f"recovery:     {recov_passed}/{len(recov)}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
