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

MODEL_OPTIONS = [
    {
        "provider": "ollama",
        "model": "gemma4:26b",
        "name": "gemma4:26b",
        "description": "Ollama · local",
    },
    {
        "provider": "thinktank",
        "model": "gpt-5.6-luna",
        "name": "gpt-5.6-luna",
        "description": "ThinkTank · minimal reasoning",
    },
    {
        "provider": "thinktank",
        "model": "gpt-5.6-sol",
        "name": "gpt-5.6-sol",
        "description": "ThinkTank · minimal reasoning",
    },
    {
        "provider": "thinktank",
        "model": "gpt-5.6-terra",
        "name": "gpt-5.6-terra",
        "description": "ThinkTank · minimal reasoning",
    },
]


def _build_client() -> AsyncOpenAI:
    if settings.llm_provider == "thinktank":
        return AsyncOpenAI(
            base_url=settings.thinktank_base_url,
            api_key=settings.thinktank_bearer_token or "thinktank-not-configured",
            default_headers={
                "Authorization": f"Bearer {settings.thinktank_bearer_token}",
                "Content-Type": "application/json",
                "X-TGT-APPLICATION": settings.thinktank_application,
            },
        )
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


def reset_client() -> None:
    """Force the next request to use the current provider configuration."""
    global _client
    _client = None


def _token_limit() -> dict[str, int]:
    """Return the completion-token parameter accepted by the active provider."""
    parameter = "max_completion_tokens" if settings.llm_provider == "thinktank" else "max_tokens"
    return {parameter: settings.llm_max_tokens}


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
    extra_body = {"reasoning_effort": settings.llm_reasoning_effort}
    if settings.llm_provider == "ollama":
        extra_body["num_image_tokens"] = settings.llm_image_tokens
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        response_format={"type": "json_schema", "json_schema": {"name": result_model.__name__, "schema": result_model.model_json_schema()}},
        extra_body=extra_body,
        **_token_limit(),
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
        extra_body={"reasoning_effort": settings.llm_reasoning_effort},
        **_token_limit(),
    )
    return response.choices[0].message.content or ""



