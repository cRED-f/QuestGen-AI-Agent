"""Safe, actionable diagnostics for academic-agent failures.

Never echo provider exception text (which may contain URLs, request bodies,
headers or credentials) to the browser. Full traces remain in FastAPI logs.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class AgentFailure(Exception):
    code: str
    stage: str
    status_code: int
    guidance: str

    def __str__(self) -> str:
        return f"{self.stage}: {self.code}"


def classify(exc: Exception, stage: str) -> AgentFailure:
    """Classify by exception type/status; never return raw provider content."""
    from fastapi import HTTPException
    from httpx import ConnectError, ConnectTimeout, ReadTimeout, TimeoutException, HTTPStatusError

    chain = []
    seen = set()
    current: BaseException | None = exc
    while current is not None and len(chain) < 8 and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__

    if any(isinstance(item, (ImportError, ModuleNotFoundError)) for item in chain):
        return AgentFailure('missing_dependencies', stage, 503,
            'Agent dependencies are missing. Install backend/requirements.txt in the Python environment running FastAPI.')
    for item in chain:
        if isinstance(item, HTTPException):
            # Credentials are configured by the workspace; do not imply a model outage.
            if item.status_code in (401, 403, 422):
                return AgentFailure('configuration', stage, 422,
                    'Check the selected model, provider URL and saved API credentials in Model settings.')
        if isinstance(item, HTTPStatusError):
            status = item.response.status_code
            if status in (401, 403):
                return AgentFailure('provider_auth', stage, 502,
                    'The model provider rejected authorization. Check the saved API key or local gateway virtual key.')
            if status == 404:
                return AgentFailure('model_not_found', stage, 502,
                    'The provider could not find the selected model or endpoint. Re-select a listed model and verify the provider URL.')
            if status == 429:
                return AgentFailure('rate_limited', stage, 503,
                    'The provider is rate-limiting requests. Retry after its limit resets or select another model.')
            if status in (400, 422):
                return AgentFailure('provider_rejected_request', stage, 502,
                    'The provider rejected the agent request. Use a model that supports tool/function calling and verify the provider endpoint.')
            if status >= 500:
                return AgentFailure('provider_unavailable', stage, 503,
                    'The model provider returned a server error. Check its availability and retry.')
    if any(isinstance(item, (ConnectTimeout, ReadTimeout, TimeoutException, asyncio.TimeoutError)) for item in chain):
        return AgentFailure('timeout', stage, 504,
            'The agent or model request timed out. Try again or use a faster tool-capable model.')
    if any(isinstance(item, (ConnectionError, ConnectError)) for item in chain):
        return AgentFailure('provider_unreachable', stage, 503,
            'QuestGen cannot connect to the model provider. Check the local server or provider connection.')

    # LangChain/OpenAI SDK errors expose typed status_code without extending HTTPStatusError.
    for item in chain:
        status = getattr(item, 'status_code', None)
        if isinstance(status, int):
            if status in (401, 403):
                return AgentFailure('provider_auth', stage, 502,
                    'Model authorization failed. Check the API key or local gateway virtual key in Model settings.')
            if status == 404:
                return AgentFailure('model_not_found', stage, 502,
                    'The selected model or API route was not found. Check Model settings.')
            if status == 429:
                return AgentFailure('rate_limited', stage, 503,
                    'The provider is rate-limiting requests. Retry later or select another model.')
            if status in (400, 422):
                return AgentFailure('provider_rejected_request', stage, 502,
                    'The model provider rejected the request. Agent Mode requires a model with reliable tool calling.')
            if status >= 500:
                return AgentFailure('provider_unavailable', stage, 503,
                    'The model provider returned a server error. Retry after checking its status.')

    lower = ' '.join(str(item).lower()[:700] for item in chain)
    if any(term in lower for term in ('tool calls', 'tool calling', 'function calling', 'does not support tools', 'tool_use')):
        return AgentFailure('tool_calls_unsupported', stage, 502,
            'The selected model appears unable to use tools. Choose a model with tool/function-calling support.')
    if any(term in lower for term in ('recursion limit', 'graphrecursionerror')):
        return AgentFailure('step_limit', stage, 502,
            'The agent reached its tool-step limit. Narrow the request or try a model that follows tool instructions.')
    if any(term in lower for term in ('json', 'structured', 'parse')) and stage == 'topic_analysis':
        return AgentFailure('invalid_topic_analysis', stage, 502,
            'The model did not return valid structured topic analysis. Choose a model with reliable JSON output.')
    if 'user-facing response' in lower or 'no final' in lower or 'empty response' in lower:
        return AgentFailure('empty_answer', stage, 502,
            'The model returned no usable answer. Try a model that reliably returns final text after using tools.')
    return AgentFailure('agent_failure', stage, 502,
        'The academic agent encountered an error. Use the reference ID to find its full traceback in the FastAPI log.')
