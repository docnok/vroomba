#!/usr/bin/env python3
"""
Vroomba LLM performance benchmark.

Measures TTFT, tokens/s throughput, and total latency for realistic
autopilot prompts at various history depths. Tests multiple models
if available.

Usage:
    uv run python bench_llm.py
    uv run python bench_llm.py --models gemma4:26b gemma4:e4b
    uv run python bench_llm.py --turns 0 1 5 10
"""

import argparse
import asyncio
import json
import sys
import time

from openai import AsyncOpenAI

# ---------------------------------------------------------------------------
# Build realistic prompts matching Homer autopilot structure
# ---------------------------------------------------------------------------

from vroomba.models import TurnResult

_SCHEMA = json.dumps(TurnResult.model_json_schema(), indent=2)

SYSTEM_PROMPT = f"""\
You are Homer, the autopilot brain of a small RC car called Vroomba.

## Your capabilities
- You control a toy RC car via digital switches: throttle (forward / idle / reverse) \
and steering (left / idle / right). There is NO speed control — forward is full speed, \
reverse is full speed, steering is full lock. Controls are ON or OFF.
- You have NO sensors. No camera, no distance sensor, no GPS. You are blind.
- You must rely entirely on dead reckoning: reasoning about how long each control \
was active to estimate distance traveled and rotation.

## How turns work
- You operate in a turn-based loop. Each turn, you receive:
  - The current timestamp and seconds elapsed since the last turn.
  - The full history of your previous turns (what you commanded and your reasoning).
- The control you choose will be held for the ENTIRE duration until your next turn. \
The duration of a turn is variable (typically 1-5 seconds) because it depends on \
LLM inference time. You MUST factor this in — a "forward" command during a 3-second \
turn moves the car ~3 seconds worth of travel.

## Your output
You MUST respond with a single JSON object matching this schema:
```json
{_SCHEMA}
```

### Field guide:
- **control**: The throttle and steering for this turn.
- **summary**: Brief reasoning — what you're doing and why, progress estimate, \
anything useful for future turns. Keep it concise (1-3 sentences).
- **user_message**: Set this (non-null) ONLY if you need to communicate something \
to the user (completion, asking for help, something unexpected). Usually null.
- **yield_to_user**: Set true to pause and wait for user input. Use sparingly.
- **task_complete**: Set true when you believe the directive is fulfilled.

## Important notes
- The car is small (~1/16 scale). At full speed it covers roughly 1-2 feet per second.
- Turning at full lock while moving forward creates a wide arc.
- When task_complete is true, ALWAYS set control to idle/idle.
- Be conservative.
"""

# Fake turn history entries (realistic summaries)
FAKE_TURNS = [
    ("forward", "idle", "Starting forward motion toward the goal. Estimating ~2s of travel."),
    ("forward", "idle", "Continuing forward. Approximately 1.5 feet traveled so far."),
    ("forward", "left", "Slight left correction to stay on course."),
    ("idle", "left", "Rotating left in place to adjust heading ~45 degrees."),
    ("forward", "idle", "Resuming forward motion after heading correction."),
    ("forward", "right", "Gentle right arc to sweep the area."),
    ("idle", "idle", "Pausing to reassess. Traveled roughly 5 feet forward with some turns."),
    ("forward", "idle", "Continuing forward exploration."),
    ("reverse", "idle", "Backing up briefly — may have reached an edge."),
    ("forward", "left", "Turning left to explore a new direction."),
    ("forward", "idle", "Heading roughly north-west now based on dead reckoning."),
    ("forward", "right", "Sweeping right to cover more area."),
    ("idle", "right", "Rotating right in place for heading adjustment."),
    ("forward", "idle", "Forward again. Estimated ~8 feet total travel."),
    ("forward", "idle", "Still moving forward. Should be nearing 10 feet."),
]


def build_messages(n_turns: int, directive: str = "Go forward for five feet") -> list[dict]:
    """Build a realistic message list with n_turns of history."""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    msgs.append({"role": "user", "content": f"Directive: {directive}"})
    msgs.append({
        "role": "assistant",
        "content": "Plan: I'll drive forward for approximately 2-3 seconds, "
                   "estimating speed at ~1.5 ft/s. I'll monitor elapsed time "
                   "each turn and stop when I estimate 5 feet traveled."
    })

    if n_turns > 0:
        history_lines = []
        for i in range(min(n_turns, len(FAKE_TURNS))):
            throttle, steering, summary = FAKE_TURNS[i]
            elapsed = 1.5 + (i % 3) * 0.5  # vary between 1.5-2.5s
            history_lines.append(
                f"Turn {i+1} (+{elapsed:.1f}s): ({throttle}/{steering}) — {summary}"
            )
        msgs.append({
            "role": "assistant",
            "content": "Turn history:\n" + "\n".join(history_lines),
        })

    turn_num = n_turns + 1
    if n_turns > 0:
        elapsed_info = f"Elapsed since last turn: 2.1s"
    else:
        elapsed_info = "This is the first turn."

    msgs.append({
        "role": "user",
        "content": f"Turn {turn_num}. {elapsed_info}\nRespond with the JSON control object for this turn.",
    })

    return msgs


def estimate_tokens(messages: list[dict]) -> int:
    """Rough token count (~4 chars per token for English)."""
    total_chars = sum(len(m["content"]) for m in messages)
    return total_chars // 4


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

async def bench_one(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict],
    use_json_format: bool = True,
    reasoning_effort: str = "none",
) -> dict:
    """Run a single benchmark call. Returns timing dict."""
    t_start = time.monotonic()
    first_token_time = None
    output_tokens = 0
    chunks = []

    response_format = {"type": "json_object"} if use_json_format else None

    extra_body = {}
    if reasoning_effort:
        extra_body["reasoning_effort"] = reasoning_effort

    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        response_format=response_format,
        temperature=0.7,
        stream=True,
        max_tokens=256,
        extra_body=extra_body if extra_body else None,
    )

    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            if first_token_time is None:
                first_token_time = time.monotonic()
            content = chunk.choices[0].delta.content
            chunks.append(content)
            # Rough token estimate per chunk
            output_tokens += max(1, len(content) // 4)

    t_end = time.monotonic()
    total_text = "".join(chunks)

    ttft = (first_token_time - t_start) if first_token_time else None
    total = t_end - t_start
    gen_time = (t_end - first_token_time) if first_token_time else None

    # Try to count actual output tokens from text
    actual_output_tokens = max(1, len(total_text) // 4)

    # Validate JSON if applicable
    json_valid = False
    pydantic_valid = False
    if use_json_format:
        try:
            data = json.loads(total_text)
            json_valid = True
            TurnResult.model_validate(data)
            pydantic_valid = True
        except Exception:
            pass

    return {
        "ttft_s": round(ttft, 3) if ttft else None,
        "gen_time_s": round(gen_time, 3) if gen_time else None,
        "total_s": round(total, 3),
        "output_tokens_est": actual_output_tokens,
        "tokens_per_s": round(actual_output_tokens / gen_time, 1) if gen_time and gen_time > 0 else None,
        "json_valid": json_valid,
        "pydantic_valid": pydantic_valid,
        "output_len": len(total_text),
        "output_preview": total_text[:200],
    }


async def run_benchmarks(models: list[str], turn_counts: list[int], base_url: str, reasoning_effort: str = "none"):
    """Run benchmarks across models and turn counts."""
    client = AsyncOpenAI(base_url=base_url, api_key="ollama")

    print("=" * 75)
    print("VROOMBA LLM BENCHMARK")
    print(f"reasoning_effort={reasoning_effort}  max_tokens=256")
    print("=" * 75)

    for model in models:
        print(f"\n{'─' * 75}")
        print(f"MODEL: {model}")
        print(f"{'─' * 75}")

        for n_turns in turn_counts:
            messages = build_messages(n_turns)
            input_tokens = estimate_tokens(messages)

            print(f"\n  ▸ {n_turns} turns history ({input_tokens} est. input tokens)")

            # Run 2 iterations (first may be cold)
            for i in range(2):
                label = "warm" if i > 0 else "cold"
                try:
                    result = await bench_one(client, model, messages, reasoning_effort=reasoning_effort)
                    ttft = f"{result['ttft_s']:.2f}s" if result['ttft_s'] else "N/A"
                    tps = f"{result['tokens_per_s']:.1f}" if result['tokens_per_s'] else "N/A"
                    valid = "✓" if result['pydantic_valid'] else ("json✓" if result['json_valid'] else "✗")
                    print(
                        f"    [{label}] TTFT={ttft:>7s}  "
                        f"gen={result['gen_time_s']:.2f}s  "
                        f"total={result['total_s']:.2f}s  "
                        f"out≈{result['output_tokens_est']}tok  "
                        f"tps={tps:>6s}  "
                        f"schema={valid}"
                    )
                except Exception as e:
                    print(f"    [{label}] ERROR: {e}")

        # Also benchmark the planning call (non-JSON, typically shorter)
        print(f"\n  ▸ Planning call (no JSON format)")
        plan_msgs = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "New directive: Go forward for five feet\n\n"
                    "Briefly acknowledge the directive and outline your plan "
                    "(2-4 sentences). Do NOT output JSON — just plain text."
                ),
            },
        ]
        for i in range(2):
            label = "warm" if i > 0 else "cold"
            try:
                result = await bench_one(client, model, plan_msgs, use_json_format=False, reasoning_effort=reasoning_effort)
                ttft = f"{result['ttft_s']:.2f}s" if result['ttft_s'] else "N/A"
                tps = f"{result['tokens_per_s']:.1f}" if result['tokens_per_s'] else "N/A"
                print(
                    f"    [{label}] TTFT={ttft:>7s}  "
                    f"gen={result['gen_time_s']:.2f}s  "
                    f"total={result['total_s']:.2f}s  "
                    f"out≈{result['output_tokens_est']}tok  "
                    f"tps={tps:>6s}"
                )
            except Exception as e:
                print(f"    [{label}] ERROR: {e}")

    print(f"\n{'=' * 75}")
    print("ANALYSIS")
    print("=" * 75)
    print("""
For the autopilot turn loop to work smoothly:
  - TTFT < 2s:  Car starts acting quickly
  - Total < 5s: Turns are short enough for meaningful control
  - Total < 10s: Within current timeout (configurable)

If total > 5s consistently, options:
  1. Use a smaller/faster model (e.g. gemma4:e4b at 8B params)
  2. Reduce system prompt size (currently ~1.5k tokens)  
  3. Use streaming to apply control at TTFT (before full response)
  4. Adjust timeout upward + warn user about latency
  5. Pre-warm model by keeping it loaded (ollama keep_alive)
""")


def main():
    parser = argparse.ArgumentParser(description="Vroomba LLM benchmark")
    parser.add_argument(
        "--models", nargs="+",
        default=["gemma4:26b", "gemma4:e4b"],
        help="Models to benchmark",
    )
    parser.add_argument(
        "--turns", nargs="+", type=int,
        default=[0, 3, 10],
        help="Turn history depths to test",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:11434/v1",
        help="Ollama API base URL",
    )
    parser.add_argument(
        "--reasoning",
        default="none",
        choices=["none", "low", "medium", "high"],
        help="Reasoning effort level (default: none)",
    )
    args = parser.parse_args()

    asyncio.run(run_benchmarks(args.models, args.turns, args.base_url, args.reasoning))


if __name__ == "__main__":
    main()
