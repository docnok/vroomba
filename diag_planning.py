#!/usr/bin/env python3
"""Diagnostic script for planning-stage LLM failures.

Sends the same planning prompts the autopilot uses, captures raw LLM
responses, and reports exactly where parsing breaks down.

Usage:
    uv run python diag_planning.py                  # run 5 plans + 5 steps
    uv run python diag_planning.py --runs 20        # more samples
    uv run python diag_planning.py --step-only      # skip plan, test steps only
    uv run python diag_planning.py --plan-only      # skip steps, test plans only
    uv run python diag_planning.py --no-schema      # skip json_schema response_format
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from dataclasses import dataclass, field

from openai import AsyncOpenAI
from pydantic import ValidationError

from vroomba.config import settings
from vroomba.models import PlanResult, TurnResult

# ── Prompts (mirrored from homer.py) ────────────────────────────────────────

IDENTITY = """\
You are Homer, autopilot of a small RC car. You are blind; no sensors.

Controls: throttle (fwd/idle/rev) × steering (left/idle/right). All on/off, no speed control.
Dead reckoning only: use elapsed time per turn to estimate distance/rotation.
Car speed: ~1-2 ft/s. Full-lock steering while moving = wide arc. Idle throttle + steering = slow rotate.\
"""

PLAN_INSTRUCTIONS = """\
Before executing a directive you first produce a plan. The car is stationary during planning — do not emit controls.
Break the directive into timed segments: estimate distances, rotation angles, and durations.

Respond with JSON: {"ack":"...","plan":"..."}
- ack: Short friendly acknowledgment for the user (1 sentence, like a robot assistant would say aloud).
- plan: Your internal execution plan (2-3 sentences). Include estimated durations and distances.\
"""

STEP_INSTRUCTIONS = """\
Each turn you receive elapsed seconds since last turn. Your control holds until next turn.
Turn duration varies (1-3s typical); factor this into distance estimates.

Respond with JSON: {"control":{"throttle":"fwd","steering":"idle"},"summary":"...","msg":null,"yield_to_user":false,"done":false}
- summary: 1 sentence max. What you're doing and why.
- msg: only set if you must tell the user something. Usually null, always set if yield_to_user or done is true.
- yield_to_user: true to pause for user input. Rare, use in cases of high uncertainty.
- done: true when directive is complete. Set control to idle/idle when done.
Be conservative. Undershoot rather than overshoot.\
"""

PLAN_SYSTEM = f"{IDENTITY}\n\n{PLAN_INSTRUCTIONS}"
STEP_SYSTEM = f"{IDENTITY}\n\n{STEP_INSTRUCTIONS}"

STEP_USER_T1 = "T1. First turn. JSON:"
STEP_USER_T2 = (
    "T1 +1.8s ↑ fwd/idle — Driving forward to cover ~3 feet.\n"
    "T2. Elapsed: measuring JSON:"
)

DIRECTIVES = [
    "Drive forward about 3 feet, then turn right 90 degrees.",
    "Go backwards for 2 seconds.",
    "Do a little circle.",
    "Drive forward, stop, turn left, then drive forward again.",
    "Wiggle side to side while moving forward.",
]


# ── Data ────────────────────────────────────────────────────────────────────

@dataclass
class Trial:
    label: str
    kind: str  # "plan" or "step"
    used_schema: bool
    raw: str = ""
    elapsed_s: float = 0.0
    json_ok: bool = False
    json_error: str = ""
    validate_ok: bool = False
    validate_error: str = ""
    api_error: str = ""
    parsed: dict | None = None


@dataclass
class Report:
    trials: list[Trial] = field(default_factory=list)

    def summary(self) -> str:
        lines: list[str] = []
        plans = [t for t in self.trials if t.kind == "plan"]
        steps = [t for t in self.trials if t.kind == "step"]

        for group_name, group in [("PLAN", plans), ("STEP", steps)]:
            if not group:
                continue
            n = len(group)
            api_fails = sum(1 for t in group if t.api_error)
            json_fails = sum(1 for t in group if not t.json_ok and not t.api_error)
            val_fails = sum(1 for t in group if t.json_ok and not t.validate_ok)
            ok = sum(1 for t in group if t.validate_ok)
            times = [t.elapsed_s for t in group if not t.api_error]
            avg_t = sum(times) / len(times) if times else 0

            lines.append(f"\n{'='*60}")
            lines.append(f"  {group_name} requests  (n={n},  avg {avg_t:.2f}s)")
            lines.append(f"{'='*60}")
            lines.append(f"  ✅ Fully valid:     {ok}/{n}")
            lines.append(f"  ❌ API error:       {api_fails}/{n}")
            lines.append(f"  ❌ JSON parse fail: {json_fails}/{n}")
            lines.append(f"  ❌ Schema invalid:  {val_fails}/{n}")

        # detail on failures
        failures = [t for t in self.trials if not t.validate_ok]
        if failures:
            lines.append(f"\n{'='*60}")
            lines.append("  FAILURE DETAILS")
            lines.append(f"{'='*60}")
            for t in failures:
                lines.append(f"\n--- {t.label} (schema={t.used_schema}) ---")
                if t.api_error:
                    lines.append(f"  API error: {t.api_error}")
                elif not t.json_ok:
                    lines.append(f"  JSON error: {t.json_error}")
                    lines.append(f"  Raw output ({len(t.raw)} chars):")
                    lines.append(indent(t.raw[:1000]))
                else:
                    lines.append(f"  Validation error: {t.validate_error}")
                    lines.append(f"  Parsed JSON:")
                    lines.append(indent(json.dumps(t.parsed, indent=2)[:600]))

        # Show a few successes for sanity
        successes = [t for t in self.trials if t.validate_ok]
        if successes:
            lines.append(f"\n{'='*60}")
            lines.append("  SAMPLE SUCCESSES (first 3)")
            lines.append(f"{'='*60}")
            for t in successes[:3]:
                lines.append(f"\n--- {t.label} ({t.elapsed_s:.2f}s) ---")
                lines.append(indent(json.dumps(t.parsed, indent=2)[:400]))

        return "\n".join(lines)


def indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


# ── Test runners ────────────────────────────────────────────────────────────

async def run_plan_trial(
    client: AsyncOpenAI,
    directive: str,
    idx: int,
    use_schema: bool,
) -> Trial:
    trial = Trial(label=f"plan-{idx}", kind="plan", used_schema=use_schema)

    messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {
            "role": "user",
            "content": f"Plan the following user directive.\n\nDirective: {directive}",
        },
    ]

    kwargs: dict = dict(
        model=settings.llm_model,
        messages=messages,
        max_tokens=settings.llm_max_tokens,
    )
    if use_schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "PlanResult", "schema": PlanResult.model_json_schema()},
        }
    kwargs["extra_body"] = {"reasoning_effort": "none"}

    t0 = time.monotonic()
    try:
        response = await client.chat.completions.create(**kwargs)
        trial.elapsed_s = time.monotonic() - t0
        trial.raw = response.choices[0].message.content or ""
    except Exception as exc:
        trial.elapsed_s = time.monotonic() - t0
        trial.api_error = f"{type(exc).__name__}: {exc}"
        return trial

    # JSON parse
    try:
        trial.parsed = json.loads(trial.raw)
        trial.json_ok = True
    except json.JSONDecodeError as exc:
        trial.json_error = str(exc)
        return trial

    # Pydantic validation
    try:
        PlanResult.model_validate(trial.parsed)
        trial.validate_ok = True
    except ValidationError as exc:
        trial.validate_error = str(exc)

    return trial


async def run_step_trial(
    client: AsyncOpenAI,
    step_user_msg: str,
    idx: int,
    use_schema: bool,
) -> Trial:
    trial = Trial(label=f"step-{idx}", kind="step", used_schema=use_schema)

    messages = [
        {"role": "system", "content": STEP_SYSTEM},
        {"role": "user", "content": "Directive: Drive forward about 3 feet, then turn right 90 degrees."},
        {"role": "user", "content": step_user_msg},
    ]

    kwargs: dict = dict(
        model=settings.llm_model,
        messages=messages,
        max_tokens=settings.llm_max_tokens,
    )
    if use_schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "TurnResult", "schema": TurnResult.model_json_schema()},
        }
    kwargs["extra_body"] = {"reasoning_effort": "none"}

    t0 = time.monotonic()
    try:
        response = await client.chat.completions.create(**kwargs)
        trial.elapsed_s = time.monotonic() - t0
        trial.raw = response.choices[0].message.content or ""
    except Exception as exc:
        trial.elapsed_s = time.monotonic() - t0
        trial.api_error = f"{type(exc).__name__}: {exc}"
        return trial

    # JSON parse
    try:
        trial.parsed = json.loads(trial.raw)
        trial.json_ok = True
    except json.JSONDecodeError as exc:
        trial.json_error = str(exc)
        return trial

    # Pydantic validation
    try:
        TurnResult.model_validate(trial.parsed)
        trial.validate_ok = True
    except ValidationError as exc:
        trial.validate_error = str(exc)

    return trial


# ── Main ────────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose LLM planning failures")
    parser.add_argument("--runs", type=int, default=5, help="Number of trials per test type")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--step-only", action="store_true")
    parser.add_argument("--no-schema", action="store_true", help="Skip json_schema response_format")
    args = parser.parse_args()

    print(f"Model:    {settings.llm_model}")
    print(f"Base URL: {settings.llm_base_url}")
    print(f"Runs:     {args.runs}")
    print(f"Schema:   {'off' if args.no_schema else 'on'}")
    print()

    client = AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
    )

    # Basic connectivity check
    print("Checking LLM connectivity... ", end="", flush=True)
    try:
        await client.models.list()
        print("OK")
    except Exception as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)

    report = Report()
    use_schema = not args.no_schema

    # Plan trials
    if not args.step_only:
        print(f"\nRunning {args.runs} plan trials...")
        for i in range(args.runs):
            directive = DIRECTIVES[i % len(DIRECTIVES)]
            print(f"  plan-{i}: \"{directive[:50]}...\" ", end="", flush=True)
            trial = await run_plan_trial(client, directive, i, use_schema)
            status = "✅" if trial.validate_ok else ("⚠ API" if trial.api_error else ("❌ JSON" if not trial.json_ok else "❌ schema"))
            print(f"{status} ({trial.elapsed_s:.2f}s)")
            report.trials.append(trial)

    # Step trials
    if not args.plan_only:
        print(f"\nRunning {args.runs} step trials...")
        step_msgs = [STEP_USER_T1, STEP_USER_T2]
        for i in range(args.runs):
            msg = step_msgs[i % len(step_msgs)]
            label = "T1" if "First turn" in msg else "T2"
            print(f"  step-{i} ({label}): ", end="", flush=True)
            trial = await run_step_trial(client, msg, i, use_schema)
            status = "✅" if trial.validate_ok else ("⚠ API" if trial.api_error else ("❌ JSON" if not trial.json_ok else "❌ schema"))
            print(f"{status} ({trial.elapsed_s:.2f}s)")
            report.trials.append(trial)

    # If schema was on, also run a comparison batch without it
    if use_schema and not args.no_schema:
        print(f"\n--- Comparison: same prompts WITHOUT json_schema ---")
        if not args.step_only:
            print(f"\nRunning {args.runs} plan trials (no schema)...")
            for i in range(args.runs):
                directive = DIRECTIVES[i % len(DIRECTIVES)]
                print(f"  plan-ns-{i}: ", end="", flush=True)
                trial = await run_plan_trial(client, directive, i + 100, use_schema=False)
                trial.label = f"plan-no-schema-{i}"
                status = "✅" if trial.validate_ok else ("⚠ API" if trial.api_error else ("❌ JSON" if not trial.json_ok else "❌ schema"))
                print(f"{status} ({trial.elapsed_s:.2f}s)")
                report.trials.append(trial)

        if not args.plan_only:
            print(f"\nRunning {args.runs} step trials (no schema)...")
            for i in range(args.runs):
                msg = step_msgs[i % len(step_msgs)]
                label = "T1" if "First turn" in msg else "T2"
                print(f"  step-ns-{i} ({label}): ", end="", flush=True)
                trial = await run_step_trial(client, msg, i + 100, use_schema=False)
                trial.label = f"step-no-schema-{i}"
                status = "✅" if trial.validate_ok else ("⚠ API" if trial.api_error else ("❌ JSON" if not trial.json_ok else "❌ schema"))
                print(f"{status} ({trial.elapsed_s:.2f}s)")
                report.trials.append(trial)

    print(report.summary())


if __name__ == "__main__":
    asyncio.run(main())
