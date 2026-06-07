"""OpenAI-compatible routing for free console.x.ai model aliases."""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncGenerator, AsyncIterable

import orjson

from app.control.account.enums import FeedbackKind
from app.control.model.console_free import (
    ConsoleFreeModel,
    get_console_free_model,
    resolve_reasoning_effort,
)
from app.control.model.registry import resolve as resolve_model
from app.dataplane.proxy import get_proxy_runtime
from app.dataplane.proxy.adapters.headers import build_console_headers
from app.dataplane.proxy.adapters.session import ResettableSession, build_session_kwargs
from app.dataplane.reverse.protocol.xai_console import (
    ConsoleResponseState,
    build_console_chat_payload,
    build_console_responses_payload,
    format_sse,
    iter_sse_events,
    maybe_json_loads,
)
from app.platform.config.snapshot import get_config
from app.platform.errors import RateLimitError, UpstreamError, ValidationError
from app.platform.logging.logger import logger
from app.platform.runtime.clock import now_s

CONSOLE_RESPONSES_API = "https://console.x.ai/v1/responses"
_CONSOLE_ORIGIN = "https://console.x.ai"


@dataclass
class _LimitResult:
    allowed: bool
    token_hash: str
    remaining: int = 0
    retry_after: float = 0.0
    reason: str = ""


@dataclass
class _Window:
    start: float
    count: int = 0
    cooldown_until: float = 0.0


class _ConsoleRateLimiter:
    def __init__(self) -> None:
        self._windows: dict[str, _Window] = {}

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8", "ignore")).hexdigest()[:12]

    def _window(self, token_hash: str, window_sec: int) -> tuple[_Window, float]:
        now = time.time()
        win = self._windows.get(token_hash)
        if win is None or now >= win.start + window_sec:
            win = _Window(start=now)
            self._windows[token_hash] = win
        return win, now

    def check(self, token: str, *, limit_count: int, window_sec: int) -> _LimitResult:
        limit_count = max(1, int(limit_count))
        window_sec = max(1, int(window_sec))
        token_hash = self.token_hash(token)
        win, now = self._window(token_hash, window_sec)
        reset_at = win.start + window_sec
        if now < win.cooldown_until:
            return _LimitResult(
                False,
                token_hash,
                max(0, limit_count - win.count),
                max(0.0, win.cooldown_until - now),
                "cooldown",
            )
        if win.count >= limit_count:
            win.cooldown_until = max(win.cooldown_until, reset_at)
            return _LimitResult(
                False,
                token_hash,
                0,
                max(0.0, reset_at - now),
                "window_exhausted",
            )
        return _LimitResult(True, token_hash, limit_count - win.count, max(0.0, reset_at - now), "")

    def record_success(self, token: str, *, limit_count: int, window_sec: int) -> _LimitResult:
        res = self.check(token, limit_count=limit_count, window_sec=window_sec)
        if not res.allowed:
            return res
        win = self._windows[res.token_hash]
        win.count += 1
        return _LimitResult(True, res.token_hash, max(0, limit_count - win.count), res.retry_after, "")

    def mark_cooldown(self, token: str, *, window_sec: int) -> _LimitResult:
        token_hash = self.token_hash(token)
        win, now = self._window(token_hash, max(1, int(window_sec)))
        win.cooldown_until = max(win.cooldown_until, win.start + max(1, int(window_sec)))
        return _LimitResult(False, token_hash, 0, max(0.0, win.cooldown_until - now), "upstream_429")


_console_rate_limiter = _ConsoleRateLimiter()


def is_console_free_model(model: str) -> bool:
    return get_console_free_model(model) is not None


def _cfg_int(key: str, default: int) -> int:
    try:
        return max(1, int(get_config(key, default)))
    except Exception:
        return default


def _cfg_bool(key: str, default: bool) -> bool:
    value = get_config(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _default_effort() -> str:
    return str(get_config("console_free.default_effort", "medium") or "medium").strip().lower() or "medium"


def _limit_count() -> int:
    return _cfg_int("console_free.rate_limit_count", 30)


def _limit_window_sec() -> int:
    return _cfg_int("console_free.rate_limit_window_sec", 900)


def _max_retry_tokens() -> int:
    return _cfg_int("console_free.max_retry_tokens", 20)


def _reasoning_effort(spec: ConsoleFreeModel, request_effort: str | None) -> str | None:
    if not spec.include_reasoning:
        return None
    return resolve_reasoning_effort(spec, request_effort, _default_effort())


def _max_output_tokens(spec: ConsoleFreeModel, requested: int | None = None) -> int | None:
    if requested is not None and requested > 0:
        return requested
    return spec.max_output_tokens


def _feedback_for_status(status: int | None) -> FeedbackKind:
    if status == 401:
        return FeedbackKind.UNAUTHORIZED
    if status in {402, 429}:
        return FeedbackKind.RATE_LIMITED
    if status == 403:
        return FeedbackKind.FORBIDDEN
    return FeedbackKind.SERVER_ERROR


def _should_retry_status(status: int | None) -> bool:
    return status in {401, 402, 403, 408, 409, 425, 429, 500, 502, 503, 504}


def _status_from_error(exc: BaseException | None) -> int | None:
    if isinstance(exc, UpstreamError):
        try:
            return int(exc.status)
        except Exception:
            return None
    return None


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(response, "content", b"")
    if isinstance(content, bytes):
        return content.decode("utf-8", "replace")
    return str(content or "")


async def _console_request(token: str, payload: dict[str, Any], *, stream: bool) -> Any:
    proxy = await get_proxy_runtime()
    lease = await proxy.acquire(clearance_origin=_CONSOLE_ORIGIN)
    headers = build_console_headers(token, lease=lease, stream=stream)
    session_kwargs = build_session_kwargs(lease=lease)
    timeout_s = float(get_config("chat.timeout", 120.0) or 120.0)
    payload_bytes = orjson.dumps(payload)
    payload_sha = hashlib.sha256(payload_bytes).hexdigest()[:16]

    session = ResettableSession(**session_kwargs)
    try:
        response = await session.post(
            CONSOLE_RESPONSES_API,
            headers=headers,
            data=payload_bytes,
            timeout=timeout_s,
            stream=stream,
        )
        if response.status_code not in {200, 201}:
            body = _response_text(response)[:800]
            logger.warning(
                "console_free upstream rejected: status={} payload_sha={} body={}",
                response.status_code,
                payload_sha,
                body[:240].replace("\n", "\\n"),
            )
            raise UpstreamError(
                f"Console upstream returned {response.status_code}",
                status=response.status_code,
                body=body,
            )

        if not stream:
            body = _response_text(response)
            try:
                return orjson.loads(body)
            except orjson.JSONDecodeError as exc:
                raise UpstreamError(
                    "Console upstream returned invalid JSON",
                    status=502,
                    body=body[:800],
                ) from exc

        async def _stream() -> AsyncGenerator[str, None]:
            try:
                async for line in response.aiter_lines():
                    yield line
            finally:
                await session.close()

        return _stream()
    except UpstreamError:
        await session.close()
        raise
    except Exception as exc:
        await session.close()
        raise UpstreamError(
            f"Console transport failed: {exc}",
            status=502,
            body=str(exc)[:800],
        ) from exc
    finally:
        if not stream:
            await session.close()


class _ConsoleChatStreamProcessor:
    def __init__(self, public_model: str):
        self.public_model = public_model
        self.state = ConsoleResponseState(public_model)
        self.created = int(time.time())
        self.response_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        self.role_sent = False
        self.tool_call_emitted: dict[str, bool] = {}
        self.tool_call_argument_length: dict[str, int] = {}
        self.text_emitted_length = 0

    def _sse(
        self,
        *,
        content: str | None = None,
        role: str | None = None,
        finish: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        usage: dict[str, Any] | None = None,
        annotations: list[dict[str, Any]] | None = None,
        search_sources: list[dict[str, Any]] | None = None,
    ) -> str:
        delta: dict[str, Any] = {}
        if role is not None:
            delta["role"] = role
            delta["content"] = ""
        elif tool_calls is not None:
            delta["tool_calls"] = tool_calls
        elif content is not None:
            delta["content"] = content

        payload: dict[str, Any] = {
            "id": self.response_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.public_model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage is not None:
            payload["usage"] = usage
        if annotations is not None:
            payload["annotations"] = annotations
        if search_sources is not None:
            payload["search_sources"] = search_sources
        return f"data: {orjson.dumps(payload).decode()}\n\n"

    def _ensure_role(self) -> str | None:
        if self.role_sent:
            return None
        self.role_sent = True
        return self._sse(role="assistant")

    def _tool_index(self, call_id: str) -> int:
        return self.state.tool_indexes.get(call_id, 0)

    def _emit_tool_call_seed(self, call_id: str, name: str | None) -> str | None:
        if self.tool_call_emitted.get(call_id):
            return None
        self.tool_call_emitted[call_id] = True
        return self._sse(
            tool_calls=[
                {
                    "index": self._tool_index(call_id),
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name or "", "arguments": ""},
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
                    self.text_emitted_length += len(str(delta))
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
                            yield self._sse(tool_calls=[{"index": self._tool_index(call_id), "function": {"arguments": delta_args}}])
                continue

            if event_type.endswith("function_call_arguments.delta"):
                call_id = parsed.get("call_id") or parsed.get("item_id") or ""
                delta = str(parsed.get("delta") or "")
                if call_id:
                    if not self.tool_call_emitted.get(call_id):
                        call = self.state.tool_calls.get(call_id)
                        name = call.get("function", {}).get("name") if call else None
                        seed = self._emit_tool_call_seed(call_id, name)
                        if seed:
                            yield seed
                    if delta:
                        self.tool_call_argument_length[call_id] = self.tool_call_argument_length.get(call_id, 0) + len(delta)
                        yield self._sse(tool_calls=[{"index": self._tool_index(call_id), "function": {"arguments": delta}}])
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
                    yield self._sse(tool_calls=[{"index": self._tool_index(call_id), "function": {"arguments": arguments[prev_len:]}}])
                    self.tool_call_argument_length[call_id] = len(arguments)

        finish_reason = "tool_calls" if self.state.tool_calls else "stop"
        yield self._sse(
            finish=finish_reason,
            usage=self.state.chat_usage(),
            annotations=list(self.state.annotations),
            search_sources=list(self.state.search_sources),
        )
        yield "data: [DONE]\n\n"


class _ConsoleResponsesStreamProxy:
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


def _chat_completion_from_state(public_model: str, state: ConsoleResponseState) -> dict[str, Any]:
    tool_calls = state.ordered_tool_calls()
    message: dict[str, Any] = {"role": "assistant", "content": state.rendered_text()}
    if tool_calls:
        message["tool_calls"] = tool_calls
    payload: dict[str, Any] = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": public_model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": state.chat_usage(),
    }
    if state.annotations:
        payload["annotations"] = list(state.annotations)
    if state.search_sources:
        payload["search_sources"] = list(state.search_sources)
    return payload


async def _reserve_console_account(directory, public_model: str, excluded: list[str]):
    spec = resolve_model(public_model)
    return await directory.reserve_any(
        spec.pool_candidates(),
        exclude_tokens=excluded or None,
        now_s_override=now_s(),
    )


async def _execute_nonstream(
    *,
    public_model: str,
    payload: dict[str, Any],
    chat_mode: bool,
) -> dict[str, Any]:
    if not _cfg_bool("console_free.enabled", True):
        raise ValidationError("console_free is disabled", param="model")

    from app.dataplane.account import _directory as directory

    if directory is None:
        raise RateLimitError("Account directory not initialised")

    excluded: list[str] = []
    last_error: BaseException | None = None
    for attempt in range(_max_retry_tokens()):
        lease = await _reserve_console_account(directory, public_model, excluded)
        if lease is None:
            break
        token = lease.token
        excluded.append(token)
        limit = _console_rate_limiter.check(token, limit_count=_limit_count(), window_sec=_limit_window_sec())
        if not limit.allowed:
            await directory.release(lease)
            last_error = RateLimitError("Console token is cooling down")
            logger.warning(
                "console_free token locally limited: token={} reason={} retry_after={} attempt={}",
                limit.token_hash,
                limit.reason,
                int(limit.retry_after),
                attempt + 1,
            )
            continue

        success = False
        fail_exc: BaseException | None = None
        try:
            raw = await _console_request(token, payload, stream=False)
            state = ConsoleResponseState(public_model)
            if isinstance(raw, dict):
                state.apply_response_object(raw)
            _console_rate_limiter.record_success(token, limit_count=_limit_count(), window_sec=_limit_window_sec())
            success = True
            return _chat_completion_from_state(public_model, state) if chat_mode else state.to_console_response_object()
        except UpstreamError as exc:
            fail_exc = exc
            last_error = exc
            status = _status_from_error(exc)
            if status in {402, 429}:
                _console_rate_limiter.mark_cooldown(token, window_sec=_limit_window_sec())
            if not _should_retry_status(status):
                raise
            logger.warning(
                "console_free retry: model={} status={} token={} attempt={}/{}",
                public_model,
                status,
                _ConsoleRateLimiter.token_hash(token),
                attempt + 1,
                _max_retry_tokens(),
            )
        finally:
            await directory.release(lease)
            if not success and fail_exc is not None:
                await directory.feedback(
                    token,
                    _feedback_for_status(_status_from_error(fail_exc)),
                    1,
                    now_s_val=now_s(),
                )

    if last_error:
        raise last_error
    raise RateLimitError("No available console-free accounts")


async def _execute_stream(
    *,
    public_model: str,
    payload: dict[str, Any],
    chat_mode: bool,
) -> AsyncGenerator[str, None]:
    if not _cfg_bool("console_free.enabled", True):
        raise ValidationError("console_free is disabled", param="model")

    from app.dataplane.account import _directory as directory

    if directory is None:
        raise RateLimitError("Account directory not initialised")

    excluded: list[str] = []
    last_error: BaseException | None = None
    for attempt in range(_max_retry_tokens()):
        lease = await _reserve_console_account(directory, public_model, excluded)
        if lease is None:
            break
        token = lease.token
        excluded.append(token)
        limit = _console_rate_limiter.check(token, limit_count=_limit_count(), window_sec=_limit_window_sec())
        if not limit.allowed:
            await directory.release(lease)
            last_error = RateLimitError("Console token is cooling down")
            continue

        success = False
        fail_exc: BaseException | None = None
        try:
            raw_stream = await _console_request(token, payload, stream=True)
            processor = _ConsoleChatStreamProcessor(public_model) if chat_mode else _ConsoleResponsesStreamProxy(public_model)
            async for chunk in processor.process(raw_stream):
                yield chunk
            _console_rate_limiter.record_success(token, limit_count=_limit_count(), window_sec=_limit_window_sec())
            success = True
            return
        except UpstreamError as exc:
            fail_exc = exc
            last_error = exc
            status = _status_from_error(exc)
            if status in {402, 429}:
                _console_rate_limiter.mark_cooldown(token, window_sec=_limit_window_sec())
            if not _should_retry_status(status):
                raise
            logger.warning(
                "console_free stream retry: model={} status={} token={} attempt={}/{}",
                public_model,
                status,
                _ConsoleRateLimiter.token_hash(token),
                attempt + 1,
                _max_retry_tokens(),
            )
        finally:
            await directory.release(lease)
            if not success and fail_exc is not None:
                await directory.feedback(
                    token,
                    _feedback_for_status(_status_from_error(fail_exc)),
                    1,
                    now_s_val=now_s(),
                )

    if last_error:
        raise last_error
    raise RateLimitError("No available console-free accounts")


async def chat_completions(
    *,
    model: str,
    messages: list[dict[str, Any]],
    stream: bool = False,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
    parallel_tool_calls: bool | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any] | AsyncGenerator[str, None]:
    spec = get_console_free_model(model)
    if spec is None:
        raise ValidationError(f"Unknown console-free model: {model}", param="model")

    payload = build_console_chat_payload(
        console_model=spec.upstream_model,
        messages=messages,
        stream=bool(stream),
        temperature=temperature,
        top_p=top_p,
        tools=tools if spec.enable_search_tools else None,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        reasoning_effort=_reasoning_effort(spec, reasoning_effort),
        max_output_tokens=_max_output_tokens(spec, max_tokens),
        store=False,
    )
    if stream:
        return _execute_stream(public_model=model, payload=payload, chat_mode=True)
    return await _execute_nonstream(public_model=model, payload=payload, chat_mode=True)


async def responses(
    *,
    model: str,
    input_value: Any,
    instructions: str | None = None,
    stream: bool = False,
    temperature: float | None = None,
    top_p: float | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
    parallel_tool_calls: bool | None = None,
    reasoning_effort: str | None = None,
    max_output_tokens: int | None = None,
    metadata: dict[str, Any] | None = None,
    user: str | None = None,
    store: bool | None = None,
    previous_response_id: str | None = None,
    truncation: str | None = None,
) -> dict[str, Any] | AsyncGenerator[str, None]:
    spec = get_console_free_model(model)
    if spec is None:
        raise ValidationError(f"Unknown console-free model: {model}", param="model")

    payload = build_console_responses_payload(
        console_model=spec.upstream_model,
        input_value=input_value,
        instructions=instructions,
        stream=bool(stream),
        temperature=temperature,
        top_p=top_p,
        tools=tools if spec.enable_search_tools else None,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        reasoning_effort=_reasoning_effort(spec, reasoning_effort),
        max_output_tokens=_max_output_tokens(spec, max_output_tokens),
        metadata=metadata,
        user=user,
        store=False if store is None else store,
        previous_response_id=previous_response_id,
        truncation=truncation,
    )
    if stream:
        return _execute_stream(public_model=model, payload=payload, chat_mode=False)
    return await _execute_nonstream(public_model=model, payload=payload, chat_mode=False)


__all__ = ["chat_completions", "is_console_free_model", "responses"]
