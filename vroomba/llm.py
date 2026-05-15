"""Async LLM client — OpenAI-compatible (works with ollama, vLLM, etc.)."""

from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI
from pydantic import ValidationError

from vroomba.config import settings
from vroomba.models import PlanResult, TurnResult

log = logging.getLogger(__name__)


def _build_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
    )


_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


async def is_available() -> bool:
    """Health-check: can we reach the LLM backend?"""
    try:
        client = get_client()
        await client.models.list()
        return True
    except Exception:
        return False

#TODO: better error handling?
async def complete(messages: list[dict], retries: int = 1) -> TurnResult:
    """Send chat completion, parse structured TurnResult JSON.

    On parse failure, retries once with a correction prompt.
    """
    client = get_client()

    for attempt in range(1 + retries):
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            response_format={"type": "json_schema", "json_schema": TurnResult.model_json_schema()},
            extra_body={"reasoning_effort": "none"},
            max_tokens=settings.llm_max_tokens,
        )
        raw = response.choices[0].message.content or ""
        try:
            data = json.loads(raw)
            return TurnResult.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            log.warning("LLM output parse error (attempt %d): %s", attempt + 1, exc)
            if attempt < retries:
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            "Your previous output was not valid JSON matching the "
                            "required schema. Please try again, outputting ONLY the "
                            "JSON object with the correct fields."
                        ),
                    },
                ]
    raise ValueError(f"LLM failed to produce valid TurnResult after {1 + retries} attempts")


async def chat(messages: list[dict]) -> str:
    """Simple chat completion returning raw text (used for planning)."""
    client = get_client()
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        extra_body={"reasoning_effort": "none"},
        max_tokens=settings.llm_max_tokens,
    )
    return response.choices[0].message.content or ""


async def complete_plan(messages: list[dict]) -> PlanResult:
    """Chat completion parsed as PlanResult JSON."""
    client = get_client()
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        response_format={"type": "json_schema", "json_schema": PlanResult.model_json_schema()},
        extra_body={"reasoning_effort": "none"},
        max_tokens=settings.llm_max_tokens,
    )
    raw = response.choices[0].message.content or ""
    data = json.loads(raw)
    return PlanResult.model_validate(data)
