"""Async LLM client — OpenAI-compatible (works with ollama, vLLM, etc.)."""

import json
import logging

from openai import AsyncOpenAI

from vroomba.config import settings
from vroomba.models import TurnResult

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


async def complete(messages: list[dict]) -> TurnResult:
    """Send chat completion, parse structured TurnResult JSON."""
    client = get_client()
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        response_format={"type": "json_schema", "json_schema": {"name": "TurnResult", "schema": TurnResult.model_json_schema()}},
        extra_body={"reasoning_effort": "none", "num_image_tokens": settings.llm_image_tokens},
        max_tokens=settings.llm_max_tokens,
    )
    raw = response.choices[0].message.content or ""
    data = json.loads(raw)
    return TurnResult.model_validate(data)


async def chat(messages: list[dict]) -> str:
    """Simple chat completion returning raw text."""
    client = get_client()
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        extra_body={"reasoning_effort": "none"},
        max_tokens=settings.llm_max_tokens,
    )
    return response.choices[0].message.content or ""



