"""
Console-backed model service for console.x.ai /v1/responses.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, AsyncGenerator, AsyncIterable, Dict, List, Optional

import orjson

from app.core.config import get_config
from app.core.exceptions import AppException, ErrorType, UpstreamException, ValidationException
from app.core.logger import logger
from app.services.grok.services.model import ModelService
from app.services.grok.utils.retry import pick_token, rate_limited, transient_upstream
from app.services.grok.utils.stream import wrap_stream_with_usage
from app.services.reverse.console_responses import ConsoleResponsesReverse
from app.services.reverse.protocol.xai_console import (
    ConsoleResponseState,
    build_console_chat_payload,
    build_console_responses_payload,
    format_sse,
    iter_sse_events,
    maybe_json_loads,
)
from app.services.reverse.utils.session import ResettableSession
from app.services.token import EffortType, get_token_manager


class ConsoleChatStreamProcessor:
    def __init__(self, public_model: str):
        self.public_model = public_model
        self.state = ConsoleResponseState(public_model)
        self.created = int(time.time())
        self.response_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        self.role_sent = False
        self.tool_call_emitted: Dict[str, bool] = {}
        self.tool_call_argument_length: Dict[str, int] = {}
        self.text_emitted_length = 0

    def _sse(
        self,
        *,
        content: Optional[str] = None,
        role: Optional[str] = None,
        finish: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        usage: Optional[Dict[str, Any]] = None,
        annotations: Optional[List[Dict[str, Any]]] = None,
        search_sources: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        delta: Dict[str, Any] = {}
        if role is not None:
            delta["role"] = role
            delta["content"] = ""
        elif tool_calls is not None:
            delta["tool_calls"] = tool_calls
        elif content is not None:
            delta["content"] = content

        payload: Dict[str, Any] = {
            "id": self.response_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.public_model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish,
                }
            ],
        }
        if usage is not None:
            payload["usage"] = usage
        if annotations is not None:
            payload["annotations"] = annotations
        if search_sources is not None:
            payload["search_sources"] = search_sources
        return f"data: {orjson.dumps(payload).decode()}\n\n"

    def _ensure_role(self) -> Optional[str]:
        if self.role_sent:
            return None
        self.role_sent = True
        return self._sse(role="assistant")

    def _tool_index(self, call_id: str) -> int:
        return self.state.tool_indexes.get(call_id, 0)

    def _emit_tool_call_seed(self, call_id: str, name: Optional[str]) -> Optional[str]:
        if self.tool_call_emitted.get(call_id):
            return None
        self.tool_call_emitted[call_id] = True
        return self._sse(
            tool_calls=[
                {
                    "index": self._tool_index(call_id),
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name or "",
                        "arguments": "",
                    },
                }
            ]
        )

    async def process(self, raw_stream: AsyncIterable[Any]) -> AsyncGenerator[str, None]:
        async for event_name, data in iter_sse_events(raw_stream):
            if data is None:
                continue
            parsed = maybe_json_loads(data)
            if parsed == "[DONE]":
                continue
            if not isinstance(parsed, dict):
                continue

            event_type = parsed.get("type") or event_name or ""
            self.state.apply_event(event_type, parsed)

            role_chunk = self._ensure_role()
            if role_chunk:
                yield role_chunk

            if event_type.endswith("output_text.delta"):
                delta = parsed.get("delta") or ""
                if delta:
                    self.text_emitted_length += len(delta)
                    yield self._sse(content=str(delta))
                continue

            if event_type.endswith("output_item.added") or event_type.endswith("output_item.done"):
                item = parsed.get("item")
                if isinstance(item, dict) and item.get("type") in {"function_call", "tool_call"}:
                    call_id = item.get("call_id") or item.get("id") or ""
                    name = item.get("name")
                    if call_id:
                        seed = self._emit_tool_call_seed(call_id, name)
                        if seed:
                            yield seed
                        arguments = item.get("arguments")
                        full_arguments = ""
                        if arguments is not None:
                            full_arguments = arguments if isinstance(arguments, str) else orjson.dumps(arguments).decode()
                        prev_len = self.tool_call_argument_length.get(call_id, 0)
                        if full_arguments and len(full_arguments) > prev_len:
                            delta_args = full_arguments[prev_len:]
                            self.tool_call_argument_length[call_id] = len(full_arguments)
                            yield self._sse(
                                tool_calls=[
                                    {
                                        "index": self._tool_index(call_id),
                                        "function": {"arguments": delta_args},
                                    }
                                ]
                            )
                continue

            if event_type.endswith("function_call_arguments.delta"):
                call_id = parsed.get("call_id") or parsed.get("item_id") or ""
                delta = str(parsed.get("delta") or "")
                if call_id:
                    if not self.tool_call_emitted.get(call_id):
                        name = None
                        call = self.state.tool_calls.get(call_id)
                        if call:
                            name = call.get("function", {}).get("name")
                        seed = self._emit_tool_call_seed(call_id, name)
                        if seed:
                            yield seed
                    if delta:
                        self.tool_call_argument_length[call_id] = self.tool_call_argument_length.get(call_id, 0) + len(delta)
                        yield self._sse(
                            tool_calls=[
                                {
                                    "index": self._tool_index(call_id),
                                    "function": {"arguments": delta},
                                }
                            ]
                        )
                continue

        full_text = self.state.rendered_text()
        if full_text and self.text_emitted_length < len(full_text):
            missing = full_text[self.text_emitted_length :]
            if missing:
                role_chunk = self._ensure_role()
                if role_chunk:
                    yield role_chunk
                yield self._sse(content=missing)

        if self.state.tool_calls:
            for tool_call in self.state.ordered_tool_calls():
                call_id = tool_call.get("id") or ""
                name = tool_call.get("function", {}).get("name")
                arguments = tool_call.get("function", {}).get("arguments") or ""
                if call_id and not self.tool_call_emitted.get(call_id):
                    seed = self._emit_tool_call_seed(call_id, name)
                    if seed:
                        yield seed
                prev_len = self.tool_call_argument_length.get(call_id, 0)
                if isinstance(arguments, str) and len(arguments) > prev_len:
                    yield self._sse(
                        tool_calls=[
                            {
                                "index": self._tool_index(call_id),
                                "function": {"arguments": arguments[prev_len:]},
                            }
                        ]
                    )
                    self.tool_call_argument_length[call_id] = len(arguments)

        finish_reason = "tool_calls" if self.state.tool_calls else "stop"
        yield self._sse(
            finish=finish_reason,
            usage=self.state.chat_usage(),
            annotations=list(self.state.annotations),
            search_sources=list(self.state.search_sources),
        )
        yield "data: [DONE]\n\n"


class ConsoleResponsesStreamProxy:
    def __init__(self, public_model: str):
        self.state = ConsoleResponseState(public_model)
        self.public_model = public_model

    async def process(self, raw_stream: AsyncIterable[Any]) -> AsyncGenerator[str, None]:
        saw_done = False
        async for event_name, data in iter_sse_events(raw_stream):
            if data is None:
                continue
            parsed = maybe_json_loads(data)
            if parsed == "[DONE]":
                saw_done = True
                continue
            if isinstance(parsed, dict):
                event_type = parsed.get("type") or event_name or ""
                self.state.apply_event(event_type, parsed)
                if event_type == "response.completed" and isinstance(parsed.get("response"), dict):
                    parsed["response"] = self.state.to_console_response_object()
                yield format_sse(event_name, parsed)
            else:
                yield format_sse(event_name, data)
        if not saw_done:
            yield "data: [DONE]\n\n"


class ConsoleService:
    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8", "ignore")).hexdigest()[:12]

    @staticmethod
    def _raise_no_token(last_error: Optional[Exception]) -> None:
        if last_error and rate_limited(last_error):
            raise AppException(
                message="No available tokens. Please try again later.",
                error_type=ErrorType.RATE_LIMIT.value,
                code="rate_limit_exceeded",
                status_code=429,
            )
        if last_error:
            raise last_error
        raise AppException(
            message="No available tokens. Please try again later.",
            error_type=ErrorType.RATE_LIMIT.value,
            code="rate_limit_exceeded",
            status_code=429,
        )

    @staticmethod
    async def _pick_alternative_available(token_mgr, model: str, tried_tokens: set[str]) -> bool:
        for pool_name in ModelService.pool_candidates_for_model(model):
            if token_mgr.get_token(pool_name, exclude=tried_tokens):
                return True
        return False

    @staticmethod
    async def chat_completions(
        *,
        model: str,
        messages: List[Dict[str, Any]],
        stream: bool = False,
        reasoning_effort: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Any = None,
        parallel_tool_calls: Optional[bool] = None,
    ) -> Any:
        model_info = ModelService.get(model)
        if not model_info or not model_info.is_console():
            raise ValidationException(f"Unknown console model: {model}")

        payload = build_console_chat_payload(
            console_model=model_info.console_model,
            messages=messages,
            stream=bool(stream),
            temperature=temperature,
            top_p=top_p,
            tools=tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_effort=reasoning_effort,
        )
        return await ConsoleService._execute_with_retry(
            model=model,
            stream=bool(stream),
            payload=payload,
            chat_mode=True,
        )

    @staticmethod
    async def responses(
        *,
        model: str,
        input_value: Any,
        instructions: Optional[str] = None,
        stream: bool = False,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Any = None,
        parallel_tool_calls: Optional[bool] = None,
        reasoning_effort: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        user: Optional[str] = None,
        store: Optional[bool] = None,
        previous_response_id: Optional[str] = None,
        truncation: Optional[str] = None,
    ) -> Any:
        model_info = ModelService.get(model)
        if not model_info or not model_info.is_console():
            raise ValidationException(f"Unknown console model: {model}")

        payload = build_console_responses_payload(
            console_model=model_info.console_model,
            input_value=input_value,
            instructions=instructions,
            stream=bool(stream),
            temperature=temperature,
            top_p=top_p,
            tools=tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            metadata=metadata,
            user=user,
            store=store,
            previous_response_id=previous_response_id,
            truncation=truncation,
        )
        return await ConsoleService._execute_with_retry(
            model=model,
            stream=bool(stream),
            payload=payload,
            chat_mode=False,
        )

    @staticmethod
    async def _execute_with_retry(
        *,
        model: str,
        stream: bool,
        payload: Dict[str, Any],
        chat_mode: bool,
    ) -> Any:
        token_mgr = await get_token_manager()
        await token_mgr.reload_if_stale()

        tried_tokens: set[str] = set()
        # Real Codex /v1/responses traffic can trigger a blank upstream 400 on
        # otherwise valid console accounts.  This is account/session-specific:
        # the same normalized payload succeeds when routed to another account.
        # Keep the standard config as a floor, but allow one stream to rotate
        # through enough accounts to find a usable session instead of forcing
        # Codex into reconnect loops.
        max_token_retries = max(int(get_config("retry.max_retry") or 3), 41)
        last_error: Optional[Exception] = None

        for attempt in range(max_token_retries):
            token = await pick_token(token_mgr, model, tried_tokens)
            if not token:
                ConsoleService._raise_no_token(last_error)

            tried_tokens.add(token)
            service = ResettableSession(impersonate=get_config("proxy.browser"))
            try:
                raw_result = await ConsoleResponsesReverse.request(
                    service,
                    token,
                    payload,
                    stream=stream,
                )
                if stream:
                    if chat_mode:
                        processor = ConsoleChatStreamProcessor(model)
                        return wrap_stream_with_usage(
                            processor.process(raw_result), token_mgr, token, model
                        )
                    proxy = ConsoleResponsesStreamProxy(model)
                    return wrap_stream_with_usage(
                        proxy.process(raw_result), token_mgr, token, model
                    )

                state = ConsoleResponseState(model)
                if isinstance(raw_result, dict):
                    state.apply_response_object(raw_result)
                if chat_mode:
                    result = state.to_chat_completion()
                else:
                    result = state.to_console_response_object()

                try:
                    model_info = ModelService.get(model)
                    effort = (
                        EffortType.HIGH
                        if (model_info and model_info.cost.value == "high")
                        else EffortType.LOW
                    )
                    await token_mgr.consume(token, effort)
                except Exception as exc:
                    logger.warning("Failed to record console usage: %s", exc)
                return result

            except UpstreamException as exc:
                last_error = exc
                if rate_limited(exc):
                    await token_mgr.mark_rate_limited(token)
                    logger.warning(
                        "Console token %s rate limited (%s), trying next token (%s/%s)",
                        ConsoleService._token_hash(token),
                        (exc.details or {}).get("status"),
                        attempt + 1,
                        max_token_retries,
                    )
                    continue

                details = exc.details or {}
                status = details.get("status")
                body = str(details.get("body") or "").strip()
                if status in {400, 500, 502, 503, 504} and not body:
                    if not await ConsoleService._pick_alternative_available(token_mgr, model, tried_tokens):
                        raise
                    logger.warning(
                        "Blank console %s for token %s, trying next token (%s/%s)",
                        status,
                        ConsoleService._token_hash(token),
                        attempt + 1,
                        max_token_retries,
                    )
                    continue

                if transient_upstream(exc):
                    if not await ConsoleService._pick_alternative_available(token_mgr, model, tried_tokens):
                        raise
                    logger.warning(
                        "Transient console upstream error for token %s, trying next token (%s/%s): %s",
                        ConsoleService._token_hash(token),
                        attempt + 1,
                        max_token_retries,
                        exc,
                    )
                    continue
                raise

        ConsoleService._raise_no_token(last_error)


__all__ = ["ConsoleService"]
