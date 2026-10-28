"""Run the model test set. From the backend folder:

    python -m evals.run_evals                     all suites, configured model
    python -m evals.run_evals --suite orders --model <name>
    python -m evals.run_evals --dry-run           check the cases and scorer, no model

Cases are synthetic only. Each case goes through the same functions the app uses (app/llm/tasks.py).
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from app import config
from app.llm import cache, client, tasks, usage

from .score import SCORERS

HERE = Path(__file__).resolve().parent
SUITES = ("orders", "drawings", "instructions")


def load_cases(suite: str) -> list[dict]:
    path = HERE / "cases" / f"{suite}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_case(suite: str, case: dict, model: str | None) -> dict:
    """Return the model's answer as a plain dict."""
    if suite == "orders":
        return tasks.read_order(case["input"], model=model).model_dump()
    if suite == "drawings":
        return tasks.read_drawing(case["input"], model=model).model_dump()
    ctx = case["context"]
    return tasks.read_instruction(case["input"], ctx["cargo_tanks"], ctx["grades"], ctx["current"],
                                  model=model).model_dump()


def dry_answer(suite: str, case: dict) -> dict:
    """The expected answer in the model's output shape, to check the scorer."""
    exp = case["expected"]
    notes = ["dry run note"] if exp.get("needs_note") else []
    key = {"orders": "parcels", "drawings": "layout", "instructions": "constraints"}[suite]
    return {key: exp[key], "notes": notes}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", choices=SUITES)
    ap.add_argument("--model", help="model name to test (default: the configured one)")
    ap.add_argument("--case", action="append", help="only run these case ids (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="score the expected answers, no model calls")
    ap.add_argument("--no-report", action="store_true", help="do not write a JSON report")
    args = ap.parse_args(argv)

    if not args.dry_run and not config.model_configured():
        print("No model configured. Set MODEL_BASE_URL, MODEL_API_KEY and MODEL_NAME in backend/.env, "
              "or use --dry-run.", file=sys.stderr)
        return 2

    cache.enabled = False  # every case must reach the model, so scores and token counts are real
    report = {"model": "dry-run" if args.dry_run else (args.model or config.MODEL_NAME),
              "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "suites": {}}
    all_passed = True
    for suite in [args.suite] if args.suite else SUITES:
        rows = []
        for case in load_cases(suite):
            if args.case and case["id"] not in args.case:
                continue
            t0 = time.perf_counter()
            with usage.track() as used:
                try:
                    answer = dry_answer(suite, case) if args.dry_run else run_case(suite, case, args.model)
                    problems = SCORERS[suite](case["expected"], answer)
                except client.ModelError as e:
                    answer, problems = None, [f"model error: {e}"]
            rows.append({"id": case["id"], "passed": not problems, "problems": problems,
                         "seconds": round(time.perf_counter() - t0, 2), "answer": answer,
                         "tokens_in": used.prompt_tokens, "tokens_out": used.completion_tokens,
                         "model_calls": used.calls})
        if not rows:
            continue
        passed = sum(r["passed"] for r in rows)
        t_in, t_out = sum(r["tokens_in"] for r in rows), sum(r["tokens_out"] for r in rows)
        all_passed &= passed == len(rows)
        report["suites"][suite] = {"passed": passed, "total": len(rows), "tokens_in": t_in, "tokens_out": t_out,
                                   "cases": rows}
        print(f"\n{suite}: {passed}/{len(rows)} passed ({100 * passed / len(rows):.0f}%), "
              f"tokens in {t_in:,} / out {t_out:,}")
        for r in rows:
            print(f"  {'PASS' if r['passed'] else 'FAIL'}  {r['id']}" + ("" if r["passed"] else f"  {'; '.join(r['problems'])}"))

    if not args.no_report:
        out = HERE / "results" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nReport: {out}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
