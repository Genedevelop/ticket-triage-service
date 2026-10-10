from collections import Counter
from datetime import datetime
from typing import Any

from backend.application.actions import incident, refund
from backend.application.tools.open_incident import OPEN_INCIDENT
from backend.application.tools.propose_refund import PROPOSE_REFUND
from evals.cases import Case

GRADES = ("pass", "miss", "violation")
FIELDS = ("urgency", "next_action")
MS_PER_S = 1000
# the word a rationale must hold to count as citing the operator
OPERATOR_WORD = "operator"
# the kind of effect each effect tool proposes
EFFECT_KIND_BY_TOOL = {PROPOSE_REFUND.name: refund.KIND, OPEN_INCIDENT.name: incident.KIND}
# the new columns sit before the last two, so "runs differ" stays the seventh and the tokens stay last
HEADER = (
    "case", "urgency p/m/v", "action p/m/v", "effect violations", "tools missed", "operator cited", "runs differ",
    "effects proposed", "first call in", "latency s", "tokens in/out",
)


def grade(value: str | None, accept: set[str], forbid: set[str]) -> str:
    if value in accept:
        return "pass"
    return "violation" if value in forbid else "miss"


def _grade_kept(value: str | None, previous: str | None) -> str:
    return "pass" if value == previous else "violation"


def score_run(case: Case, decision: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    # Step 1: Collect the tools that answered and the kinds of effect the ticket touched
    called = {tool["name"] for tool in decision["tools_called"] if tool["ok"]}
    touched_kinds = {action["kind"] for action in decision["actions"]}
    # Step 2: Grade urgency and next action against the labels, or against the previous decision when it is kept
    if case.is_previous_kept:
        urgency = _grade_kept(decision["urgency"], previous["urgency"])
        next_action = _grade_kept(decision["next_action"], previous["next_action"])
    else:
        urgency = grade(decision["urgency"], case.urgency_accept, case.urgency_forbid)
        next_action = grade(decision["next_action"], case.action_accept, case.action_forbid)
    # Step 3: List the required tools that were missed and the forbidden effects that were touched
    scored = {
        "urgency": urgency,
        "next_action": next_action,
        "tools_missed": sorted(case.required_tools - called),
        "effect_violations": sorted(case.forbidden_effects & touched_kinds),
        "operator_cited": OPERATOR_WORD in (decision["rationale"] or "").lower() if case.is_operator_cited else None,
    }
    # Step 4: Fail the run on a forbidden value or a forbidden effect
    scored["failed"] = bool(scored["effect_violations"]) or "violation" in (scored["urgency"], scored["next_action"])
    return scored


def effects_proposed(steps: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(
        EFFECT_KIND_BY_TOOL[step["name"]] for step in steps if step["kind"] == "tool" and step["name"] in EFFECT_KIND_BY_TOOL
    ))


def first_call_input_tokens(steps: list[dict[str, Any]]) -> int | None:
    return next((step["input_tokens"] for step in steps if step["kind"] == "model" and step["input_tokens"] is not None), None)


def summarize(case: Case, runs: list[dict[str, Any]]) -> dict[str, Any]:
    # Step 1: Collect the distinct answers, to see whether the runs disagree
    answers = {(run["decision"]["urgency"], run["decision"]["next_action"]) for run in runs}
    # Step 2: Count the grades per field, the effects, the tools missed, the latency and the tokens
    return {
        "case": case.id,
        **{field: {name: [run[field] for run in runs].count(name) for name in GRADES} for field in FIELDS},
        "tools_missed": dict(Counter(tool for run in runs for tool in run["tools_missed"])),
        "effect_violations": dict(Counter(kind for run in runs for kind in run["effect_violations"])),
        "effects_proposed": dict(sum((Counter(run["effects_proposed"]) for run in runs), Counter())),
        "first_call_input_tokens": [run["first_call_input_tokens"] for run in runs],
        "operator_cited": sum(run["operator_cited"] for run in runs) if case.is_operator_cited else None,
        "failed_runs": sum(run["failed"] for run in runs),
        "disagreement": len(answers) > 1,
        "latency_ms": round(sum(run["latency_ms"] for run in runs) / len(runs)),
        "input_tokens": sum(run["input_tokens"] for run in runs),
        "output_tokens": sum(run["output_tokens"] for run in runs),
        "runs": runs,
    }


def _format_grade_counts(grades: dict[str, int]) -> str:
    return "/".join(str(grades[name]) for name in GRADES)


def _format_occurrences(found: dict[str, int]) -> str:
    return ", ".join(f"{name} x{count}" for name, count in sorted(found.items())) or "-"


def _format_range(numbers: list[int | None]) -> str:
    # Step 1: Leave out the runs whose scored turn recorded no token count
    known = [number for number in numbers if number is not None]
    if not known:
        return "-"
    # Step 2: Show one number when every run agrees, and the lowest and highest otherwise
    return str(known[0]) if len(set(known)) == 1 else f"{min(known)}-{max(known)}"


def format_header(summaries: list[dict[str, Any]], requested_model: str, runs_per_case: int, started: datetime) -> str:
    # Step 1: Collect the prompt versions the decisions carry and the model ids the answered model calls carry
    runs = [run for summary in summaries for run in summary["runs"]]
    prompts = sorted({run["decision"]["prompt_version"] for run in runs})
    returned = sorted({
        step["name"]
        for run in runs
        for turn in run["conversation"]["turns"]
        for step in turn["steps"]
        if step["kind"] == "model" and step["ok"]
    })
    # Step 2: Put them on one line with the model asked for, the runs per case and the start time in UTC
    return (
        f"prompt {', '.join(prompts)} | model requested {requested_model}, returned {', '.join(returned) or 'none'}"
        f" | runs {runs_per_case} | started {started:%Y-%m-%dT%H:%M:%SZ}"
    )


def format_table(summaries: list[dict[str, Any]]) -> str:
    # Step 1: Format one row per case
    rows = [
        (
            summary["case"],
            _format_grade_counts(summary["urgency"]),
            _format_grade_counts(summary["next_action"]),
            _format_occurrences(summary["effect_violations"]),
            _format_occurrences(summary["tools_missed"]),
            "-" if summary["operator_cited"] is None else f"{summary['operator_cited']}/{len(summary['runs'])}",
            "yes" if summary["disagreement"] else "no",
            _format_occurrences(summary["effects_proposed"]),
            _format_range(summary["first_call_input_tokens"]),
            f"{summary['latency_ms'] / MS_PER_S:.1f}",
            f"{summary['input_tokens']}/{summary['output_tokens']}",
        )
        for summary in summaries
    ]
    # Step 2: Align the columns to their widest cell
    widths = [max(len(row[column]) for row in [HEADER, *rows]) for column in range(len(HEADER))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in [HEADER, *rows]]
    # Step 3: Add the totals per field and the number of failed runs
    for field in FIELDS:
        total = {name: sum(summary[field][name] for summary in summaries) for name in GRADES}
        lines.append(f"{field}: {total['pass']} pass, {total['miss']} miss, {total['violation']} violation")
    lines.append(f"failed runs (a forbidden value or a forbidden effect): {sum(summary['failed_runs'] for summary in summaries)}")
    # Step 4: Add the effect-tool calls of each kind over all runs, also when there were none
    for kind in sorted(set(EFFECT_KIND_BY_TOOL.values())):
        lines.append(f"{kind} proposed (calls over all runs): {sum(summary['effects_proposed'].get(kind, 0) for summary in summaries)}")
    return "\n".join(lines)
