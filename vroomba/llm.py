"""Async LLM client — OpenAI-compatible (works with ollama, vLLM, etc.)."""

import json
import logging
from typing import TypeVar

from langfuse.openai import AsyncOpenAI
from ollama import AsyncClient

from vroomba.config import settings
from vroomba.models import TurnResult

log = logging.getLogger(__name__)

ResultT = TypeVar("ResultT", bound=TurnResult)


def _build_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
    )


_client: AsyncOpenAI | None = None
_ollama_client: AsyncClient | None = AsyncClient()

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
    
async def complete_ollama(messages: list[dict], result_model: type[ResultT] = TurnResult) -> ResultT:
    response = await _ollama_client.chat(
        model=settings.llm_model,
        messages=messages,
        format=result_model.model_json_schema(),
        think=False,
        options={"num_image_tokens": settings.llm_image_tokens}
    )

    result = result_model.model_validate_json(response.message.content)


    prompt_eval_count = response.get("prompt_eval_count", 0)
    prompt_eval_dur_ns = response.get("prompt_eval_duration", 0)
    eval_count = response.get("eval_count", 0)
    eval_dur_ns = response.get("eval_duration", 0)
    total_dur_ns = response.get("total_duration", 0)
    load_dur_ns = response.get("load_duration", 0)

    log.info(f"Ollama response: prompt_eval_count={prompt_eval_count} prompt_eval_dur_ms={prompt_eval_dur_ns/1e6:.2f} eval_count={eval_count} eval_dur_ms={eval_dur_ns/1e6:.2f} total_dur_ms={total_dur_ns/1e6:.2f} load_dur_ms={load_dur_ns/1e6:.2f}")
    
    return result


async def complete(messages: list[dict], result_model: type[ResultT] = TurnResult) -> ResultT:
    """Send chat completion, parse structured TurnResult JSON."""
    client = get_client()
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        response_format={"type": "json_schema", "json_schema": {"name": result_model.__name__, "schema": result_model.model_json_schema()}},
        extra_body={"reasoning_effort": "none", "num_image_tokens": settings.llm_image_tokens},
        max_tokens=settings.llm_max_tokens,
    )
    raw = response.choices[0].message.content or ""
    data = json.loads(raw)
    return result_model.model_validate(data)


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



