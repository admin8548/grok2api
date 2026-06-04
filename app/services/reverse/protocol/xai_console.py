"""Helpers for console.x.ai /v1/responses request/response adaptation."""

from __future__ import annotations

import re
import time
import uuid
from copy import deepcopy
from typing import Any, AsyncGenerator, AsyncIterable
from urllib.parse import urlparse

import orjson

_URL_RE = re.compile(r'https?://[^\s<>()"\']+')
_SEARCH_TOOL_TYPES = {"web_search", "web_search_2025_08_26"}
_MESSAGE_TEXT_TYPES = {"text", "input_text", "output_text"}
_MESSAGE_IMAGE_TYPES = {"image", "image_url", "input_image", "output_image"}
_XML_TAG_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _now_ts() -> int:
    return int(time.time())


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _json_dumps(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return orjson.dumps(value).decode()
    except Exception:
        return str(value)


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _extract_urls(text: str) -> list[str]:
    if not isinstance(text, str) or not text:
        return []
    return [match.group(0).rstrip('.,);]') for match in _URL_RE.finditer(text)]


def _extract_xml_tag(text: str, tag: str) -> str | None:
    if not isinstance(text, str) or not text:
        return None
    pattern = _XML_TAG_RE_CACHE.get(tag)
    if pattern is None:
        pattern = re.compile(rf"<{re.escape(tag)}>(.*?)</{re.escape(tag)}>", re.S)
        _XML_TAG_RE_CACHE[tag] = pattern
    match = pattern.search(text)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        item_type = item.get("type")
        if item_type in _MESSAGE_TEXT_TYPES:
            text = item.get("text") or item.get("content") or ""
            if text:
                parts.append(str(text))
            continue
        if item_type == "input_audio":
            audio = item.get("input_audio") or item.get("audio") or {}
            transcript = audio.get("transcript")
            if transcript:
                parts.append(str(transcript))
            continue
        if item_type in {"file", "input_file"}:
            file_obj = item.get("file") or item
            file_name = (
                file_obj.get("filename")
                or file_obj.get("file_name")
                or file_obj.get("file_id")
            )
            if file_name:
                parts.append(f"[file:{file_name}]")
    return "\n".join(part for part in parts if part)


def _normalize_content_parts(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return [{"type": "input_text", "text": str(content)}]

    parts: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, str):
            if item:
                parts.append({"type": "input_text", "text": item})
            continue
        if not isinstance(item, dict):
            parts.append({"type": "input_text", "text": str(item)})
            continue

        item_type = item.get("type")
        if item_type in _MESSAGE_TEXT_TYPES:
            text = item.get("text") or item.get("content") or ""
            if text:
                parts.append({"type": "input_text", "text": str(text)})
            continue

        if item_type in _MESSAGE_IMAGE_TYPES:
            image_value = item.get("image_url")
            url = ""
            detail = None
            if isinstance(image_value, dict):
                url = image_value.get("url") or ""
                detail = image_value.get("detail")
            elif isinstance(image_value, str):
                url = image_value
            else:
                url = item.get("url") or item.get("image") or ""
                detail = item.get("detail")
            if url:
                payload: dict[str, Any] = {"type": "input_image", "image_url": url}
                if detail:
                    payload["detail"] = detail
                parts.append(payload)
            continue

        if item_type in {"file", "input_file"}:
            file_obj = item.get("file") if item_type == "file" else item
            file_obj = file_obj or {}
            payload: dict[str, Any] = {"type": "input_file"}
            if file_obj.get("file_data"):
                payload["file_data"] = file_obj["file_data"]
            if file_obj.get("file_id"):
                payload["file_id"] = file_obj["file_id"]
            if file_obj.get("filename"):
                payload["filename"] = file_obj["filename"]
            if len(payload) > 1:
                parts.append(payload)
            continue

        if item_type in {"input_audio", "audio"}:
            audio = item.get("input_audio") or item.get("audio") or item
            data = audio.get("data")
            if data:
                parts.append({"type": "input_audio", "audio": {"data": data}})
            continue

    return parts


def _normalize_console_tool(tool: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(tool, dict):
        return None
    tool_type = str(tool.get("type") or "").strip()
    if tool_type in _SEARCH_TOOL_TYPES:
        # Console /v1/responses accepts the built-in search tool, but rejects
        # OpenAI/Codex-side extension fields such as external_web_access with
        # a blank HTTP 400.  Send the minimal console shape only.
        return {"type": "web_search"}
    if tool_type == "function":
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = fn.get("name") or tool.get("name")
        if not name:
            return None
        normalized = {
            "type": "function",
            "name": name,
            "description": fn.get("description") or tool.get("description") or "",
            "parameters": fn.get("parameters") or tool.get("parameters") or {},
        }
        if tool.get("strict") is not None:
            normalized["strict"] = tool.get("strict")
        elif fn.get("strict") is not None:
            normalized["strict"] = fn.get("strict")
        return normalized

    # console.x.ai rejects OpenAI/Codex-only built-in tool types (for example
    # computer/local-shell style tools) with 422.  The public compatibility
    # layer must accept those fields, but they are not valid console upstream
    # request fields, so do not pass them through.  Custom callable tools should
    # be sent as {"type":"function", ...}; those are handled above.
    return None


def ensure_console_web_search(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for tool in tools or []:
        normalized_tool = _normalize_console_tool(tool)
        if normalized_tool is not None:
            normalized.append(normalized_tool)
    has_search = any(
        str(tool.get("type") or "").strip() in _SEARCH_TOOL_TYPES
        for tool in normalized
    )
    if not has_search:
        normalized.append({"type": "web_search"})
    return normalized


def normalize_console_tool_choice(tool_choice: Any) -> Any:
    # Matrix-tested against console /v1/responses:
    #   - "auto" and "none" are accepted.
    #   - forced function choices shaped as {"type":"function","name":...}
    #     are accepted when the referenced function tool is present.
    # Keep only known OpenAI Responses-compatible shapes; omit unknown values
    # rather than poisoning the upstream request.
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        value = tool_choice.strip()
        if value in {"auto", "none", "required"}:
            return value
        return None
    if isinstance(tool_choice, dict):
        choice_type = str(tool_choice.get("type") or "").strip()
        if choice_type == "function" and tool_choice.get("name"):
            return {"type": "function", "name": str(tool_choice["name"])}
    return None


def _normalize_tool_call_item(call: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(call, dict):
        return None
    fn = call.get("function") or {}
    name = fn.get("name") or call.get("name")
    if not name:
        return None
    arguments = fn.get("arguments") if isinstance(fn, dict) else call.get("arguments")
    return {
        "type": "function_call",
        "call_id": call.get("id") or _new_id("call"),
        "name": name,
        "arguments": _json_dumps(arguments or ""),
    }


def openai_messages_to_console_input(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    instructions_parts: list[str] = []
    input_items: list[dict[str, Any]] = []

    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = (message.get("role") or "user").strip() or "user"
        content = message.get("content")

        if role in {"system", "developer"}:
            text = _content_text(content).strip()
            if text:
                if role == "developer":
                    instructions_parts.append(f"Developer:\n{text}")
                else:
                    instructions_parts.append(text)
            continue

        if role == "tool":
            output = _content_text(content)
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id") or _new_id("call"),
                    "output": output,
                }
            )
            continue

        parts = _normalize_content_parts(content)
        if parts:
            input_items.append({"type": "message", "role": role, "content": parts})

        if role == "assistant":
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    item = _normalize_tool_call_item(tool_call)
                    if item:
                        input_items.append(item)

    instructions = "\n\n".join(part for part in instructions_parts if part).strip() or None
    return input_items, instructions


def normalize_console_input(input_value: Any) -> list[dict[str, Any]]:
    if input_value is None:
        return []
    if isinstance(input_value, str):
        return [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": input_value}]}]
    if isinstance(input_value, dict):
        input_value = [input_value]
    elif not isinstance(input_value, list):
        return [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": str(input_value)}]}]

    normalized: list[dict[str, Any]] = []
    pending_user_parts: list[dict[str, Any]] = []

    def _flush_pending() -> None:
        nonlocal pending_user_parts
        if pending_user_parts:
            normalized.append({"type": "message", "role": "user", "content": pending_user_parts})
            pending_user_parts = []

    for item in input_value:
        if isinstance(item, str):
            pending_user_parts.append({"type": "input_text", "text": item})
            continue
        if not isinstance(item, dict):
            pending_user_parts.append({"type": "input_text", "text": str(item)})
            continue

        if item.get("type") == "message":
            _flush_pending()
            normalized.append(
                {
                    "type": "message",
                    "role": item.get("role") or "user",
                    "content": _normalize_content_parts(item.get("content")),
                }
            )
            continue

        if "role" in item and "content" in item:
            _flush_pending()
            normalized.append(
                {
                    "type": "message",
                    "role": item.get("role") or "user",
                    "content": _normalize_content_parts(item.get("content")),
                }
            )
            continue

        item_type = item.get("type")
        if item_type in {
            "function_call",
            "function_call_output",
            "tool_output",
            "function_output",
            "tool_call_output",
            "input_tool_output",
        }:
            _flush_pending()
            call_id = item.get("call_id") or item.get("tool_call_id") or item.get("id") or _new_id("call")
            if item_type == "function_call":
                normalized.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": item.get("name") or (item.get("function") or {}).get("name"),
                        "arguments": _json_dumps(item.get("arguments") or (item.get("function") or {}).get("arguments") or ""),
                    }
                )
            else:
                normalized.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": _json_dumps(item.get("output") or item.get("content") or ""),
                    }
                )
            continue

        parts = _normalize_content_parts(item)
        if parts:
            pending_user_parts.extend(parts)

    _flush_pending()
    return normalized


def _system_input_message(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "system",
        "content": [{"type": "input_text", "text": text}],
    }


def _leading_system_insert_index(items: list[dict[str, Any]]) -> int:
    idx = 0
    while idx < len(items):
        item = items[idx]
        if not isinstance(item, dict) or str(item.get("role") or "").strip() != "system":
            break
        idx += 1
    return idx


def _input_item_text_len(item: dict[str, Any]) -> int:
    if not isinstance(item, dict):
        return len(str(item))
    total = 0
    if item.get("content") is not None:
        total += len(_content_text(item.get("content")))
    if item.get("output") is not None:
        total += len(str(item.get("output") or ""))
    if item.get("arguments") is not None:
        total += len(str(item.get("arguments") or ""))
    return total


def _truncate_middle(text: str, limit: int) -> str:
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= limit:
        return text
    marker = f"\n...[truncated {len(text) - limit} chars by compatibility layer]...\n"
    head_len = max(0, int(limit * 0.65))
    tail_len = max(0, limit - head_len - len(marker))
    return text[:head_len] + marker + (text[-tail_len:] if tail_len else "")


def _truncate_large_tool_payloads(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Large shell outputs/arguments from old Codex turns can push console responses
    # into blank 500s. Keep head+tail context but cap any single old item.
    changed = False
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        new_item = item
        if item.get("type") == "function_call_output":
            output = str(item.get("output") or "")
            if len(output) > 20000:
                new_item = deepcopy(item)
                new_item["output"] = _truncate_middle(output, 20000)
                changed = True
        elif item.get("type") == "function_call":
            arguments = str(item.get("arguments") or "")
            if len(arguments) > 20000:
                new_item = deepcopy(item)
                new_item["arguments"] = _truncate_middle(arguments, 20000)
                changed = True
        out.append(new_item)
    return out if changed else items


def _compact_oversized_console_input(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Console upstream accepts normal Codex function histories, but very long
    # accumulated /v1/responses histories (hundreds of items / ~MB JSON) can stall
    # then return blank 500s. Preserve compact system context and the recent tail;
    # local tools/worktree remain the source of truth for older execution state.
    items = _truncate_large_tool_payloads(items)
    max_items = 180
    max_text_chars = 420_000
    min_tail_items = 60
    total_chars = sum(_input_item_text_len(item) for item in items)
    if len(items) <= max_items and total_chars <= max_text_chars:
        return items

    system_items = [
        item for item in items
        if isinstance(item, dict) and str(item.get("role") or "").strip() == "system"
    ]
    non_system = [
        item for item in items
        if not (isinstance(item, dict) and str(item.get("role") or "").strip() == "system")
    ]

    tail_budget = max(min_tail_items, max_items - len(system_items) - 1)
    tail = non_system[-tail_budget:]
    # Avoid starting the retained tail with an orphaned tool/function output.
    while tail and isinstance(tail[0], dict) and tail[0].get("type") == "function_call_output":
        tail = tail[1:]

    def _tail_total() -> int:
        return sum(_input_item_text_len(item) for item in system_items) + sum(
            _input_item_text_len(item) for item in tail
        )

    while len(tail) > min_tail_items and _tail_total() > max_text_chars:
        tail = tail[1:]
        while tail and isinstance(tail[0], dict) and tail[0].get("type") == "function_call_output":
            tail = tail[1:]

    omitted = max(0, len(non_system) - len(tail))
    omitted_chars = max(0, total_chars - _tail_total())
    notice = _system_input_message(
        "Compatibility note: older Codex conversation/tool history was compacted "
        f"before forwarding upstream (omitted {omitted} items, about {omitted_chars} chars). "
        "Use the current worktree, files, and tool inspection as authoritative for older state."
    )
    return system_items + [notice] + tail


def build_console_chat_payload(
    *,
    console_model: str,
    messages: list[dict[str, Any]],
    stream: bool,
    temperature: float | None,
    top_p: float | None,
    tools: list[dict[str, Any]] | None,
    tool_choice: Any,
    parallel_tool_calls: bool | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    input_items, instructions = openai_messages_to_console_input(messages)
    payload: dict[str, Any] = {
        "model": console_model,
        "input": input_items,
        "stream": bool(stream),
        "tools": ensure_console_web_search(tools),
    }
    if instructions:
        payload["instructions"] = instructions
    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p
    if parallel_tool_calls is not None:
        payload["parallel_tool_calls"] = parallel_tool_calls
    normalized_tool_choice = normalize_console_tool_choice(tool_choice)
    if normalized_tool_choice is not None:
        payload["tool_choice"] = normalized_tool_choice
    if reasoning_effort is not None:
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload


def build_console_responses_payload(
    *,
    console_model: str,
    input_value: Any,
    instructions: str | None,
    stream: bool,
    temperature: float | None,
    top_p: float | None,
    tools: list[dict[str, Any]] | None,
    tool_choice: Any,
    parallel_tool_calls: bool | None,
    reasoning_effort: str | None,
    max_output_tokens: int | None,
    metadata: dict[str, Any] | None,
    user: str | None,
    store: bool | None,
    previous_response_id: str | None,
    truncation: str | None,
) -> dict[str, Any]:
    # console /v1/responses is much stricter than the public OpenAI
    # Responses API.  In particular, large Codex instructions in the dedicated
    # `instructions` field (or re-labeled as ordinary user text) can be rejected
    # with HTTP 400.  The reference console implementation keeps system/developer
    # context as leading input messages with role=system, so follow that shape.
    combined_instructions = instructions or None
    filtered_input_value = input_value

    normalized_tools = ensure_console_web_search(tools)
    if "multi-agent" in (console_model or ""):
        # console responses rejects grok-4.20-multi-agent function tools.
        normalized_tools = [
            tool for tool in normalized_tools
            if str(tool.get("type") or "").strip() in _SEARCH_TOOL_TYPES
        ] or [{"type": "web_search"}]
    # Do not reduce Codex's normal function-tool set. Matrix testing showed the
    # full function-tool bundle is accepted. Non-function/namespace tools are
    # filtered in _normalize_console_tool because console /v1/responses rejects
    # that schema with 422, but valid function tools must be preserved so Codex
    # can inspect and edit the local project.

    normalized_input = normalize_console_input(filtered_input_value)
    for msg in normalized_input:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user").strip() or "user"
        if role in {"system", "developer"}:
            # console accepts system but not OpenAI's newer developer role.
            msg["role"] = "system"

    if combined_instructions:
        normalized_input.insert(
            0,
            {
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text", "text": combined_instructions}],
            },
        )
        combined_instructions = None

    # Codex injects an XML-like <environment_context> block as a user message.
    # The console upstream can reject that exact block with HTTP 400. It is local
    # execution metadata, not the user's actual prompt, so do not forward the
    # raw XML. Keep a short cwd hint so the model knows local tools can inspect
    # the current project.
    env_hint: str | None = None
    codex_goal_hint: str | None = None
    codex_internal_omitted = 0
    turn_abort_seen = False
    filtered_input = []
    for msg in normalized_input:
        text = _content_text(msg.get("content")) if isinstance(msg, dict) else ""
        stripped_text = text.lstrip()
        if (
            isinstance(msg, dict)
            and str(msg.get("role") or "").strip() == "user"
            and stripped_text.startswith("<environment_context>")
        ):
            cwd = _extract_xml_tag(text, "cwd")
            if cwd:
                env_hint = (
                    f"Current working directory: {cwd}. "
                    "Use local tools such as exec_command to inspect project files when needed."
                )
            continue
        if (
            isinstance(msg, dict)
            and str(msg.get("role") or "").strip() == "user"
            and stripped_text.startswith("<codex_internal_context")
        ):
            objective = _extract_xml_tag(text, "objective")
            if objective:
                codex_goal_hint = _truncate_middle(objective.strip(), 1000)
            codex_internal_omitted += 1
            continue
        if (
            isinstance(msg, dict)
            and str(msg.get("role") or "").strip() == "user"
            and stripped_text.startswith("<turn_aborted>")
        ):
            turn_abort_seen = True
            continue
        filtered_input.append(msg)
    normalized_input = filtered_input

    system_text_len = sum(
        len(_content_text(msg.get("content")))
        for msg in normalized_input
        if isinstance(msg, dict) and str(msg.get("role") or "").strip() == "system"
    )
    if system_text_len > 8000:
        # Real Codex sends a very large system/developer prompt.  The console
        # upstream can reject that content itself with a blank HTTP 400, even
        # when the JSON shape is valid.  Keep the user conversation intact, but
        # replace oversized system context with a compact compatibility preamble.
        normalized_input = [
            msg for msg in normalized_input
            if not (isinstance(msg, dict) and str(msg.get("role") or "").strip() == "system")
        ]
        normalized_input.insert(
            0,
            {
                "type": "message",
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You are a helpful coding assistant running inside Codex. "
                            "Use available local tools to inspect, edit, and verify files "
                            "in the workspace. When the user asks for a code or file change, "
                            "actually modify the files with tool calls (for example shell "
                            "redirection, sed, or a small script), then verify with commands "
                            "such as cat, tests, or git diff. Do not stop after only printing "
                            "logs, analysis, or a plan. Never end the task with only tool "
                            "calls or command output; after verification, produce a final "
                            "assistant message that summarizes the actual file changes and "
                            "verification result. Preserve the user's language."
                        ),
                    }
                ],
            },
        )

    if env_hint:
        normalized_input.insert(_leading_system_insert_index(normalized_input), _system_input_message(env_hint))

    if codex_goal_hint:
        normalized_input.insert(
            _leading_system_insert_index(normalized_input),
            _system_input_message(
                "Codex internal goal-continuation context was compacted. "
                f"Latest active goal objective: {codex_goal_hint}"
            ),
        )
    elif codex_internal_omitted:
        normalized_input.insert(
            _leading_system_insert_index(normalized_input),
            _system_input_message("Codex internal goal-continuation context was omitted from upstream payload."),
        )

    if turn_abort_seen:
        normalized_input.insert(
            _leading_system_insert_index(normalized_input),
            _system_input_message("The previous Codex turn was interrupted by the user; inspect current files/tool state before continuing."),
        )

    normalized_input = _compact_oversized_console_input(normalized_input)

    payload: dict[str, Any] = {
        "model": console_model,
        "input": normalized_input,
        "stream": bool(stream),
        "tools": normalized_tools,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p
    if parallel_tool_calls is not None:
        payload["parallel_tool_calls"] = parallel_tool_calls
    normalized_tool_choice = normalize_console_tool_choice(tool_choice)
    if normalized_tool_choice is not None:
        payload["tool_choice"] = normalized_tool_choice
    # The public OpenAI-compatible endpoint accepts reasoning.effort, but
    # console /v1/responses currently rejects it with HTTP 400 for
    # grok-4.20-reasoning. Thinking emission is controlled locally by
    # emit_think, so do not forward reasoning upstream.
    _ = reasoning_effort
    if max_output_tokens is not None and max_output_tokens > 0:
        payload["max_output_tokens"] = max_output_tokens
    # max_output_tokens <= 0 is accepted by some clients as "unspecified", but
    # console upstream rejects it with HTTP 400.
    #
    # Matrix-tested passthrough behavior:
    #   - metadata={} rejects with blank HTTP 400, so keep metadata as a no-op.
    #   - previous_response_id rejects with 404 when the ID is not upstream-
    #     valid; this proxy does not maintain upstream response storage mapping,
    #     so keep it as a compatibility no-op.
    #   - store, user, and truncation are accepted by console upstream.
    _ = (metadata, previous_response_id)
    if store is not None:
        payload["store"] = store
    if user is not None:
        payload["user"] = user
    if truncation is not None:
        payload["truncation"] = truncation
    return payload


def _default_chat_usage() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_tokens_details": {
            "cached_tokens": 0,
            "text_tokens": 0,
            "audio_tokens": 0,
            "image_tokens": 0,
        },
        "completion_tokens_details": {
            "text_tokens": 0,
            "audio_tokens": 0,
            "reasoning_tokens": 0,
        },
    }


class ConsoleResponseState:
    def __init__(self, public_model: str):
        self.public_model = public_model
        self.response_id = _new_id("resp")
        self.created_at = _now_ts()
        self.status = "in_progress"
        self.output_text_parts: list[str] = []
        self.final_output_text: str | None = None
        self.reasoning_parts: list[str] = []
        self.final_reasoning_text: str | None = None
        self.annotations: list[dict[str, Any]] = []
        self.search_sources: list[dict[str, Any]] = []
        self.images: list[str] = []
        self.usage: dict[str, Any] = {}
        self.tool_calls: dict[str, dict[str, Any]] = {}
        self.tool_order: list[str] = []
        self.tool_indexes: dict[str, int] = {}
        self.raw_response: dict[str, Any] | None = None
        self._has_web_search_sources = False
        self._source_urls: set[str] = set()
        self._annotation_keys: set[bytes] = set()

    def _append_annotation(self, annotation: Any) -> None:
        if not isinstance(annotation, dict):
            return
        try:
            key = orjson.dumps(annotation, option=orjson.OPT_SORT_KEYS)
        except Exception:
            key = str(annotation).encode()
        if key in self._annotation_keys:
            return
        self._annotation_keys.add(key)
        self.annotations.append(annotation)

    def _add_source(self, url: str, title: str | None = None) -> None:
        url = (url or "").strip()
        if not _is_http_url(url) or url in self._source_urls:
            return
        self._source_urls.add(url)
        source: dict[str, Any] = {"url": url}
        if title:
            source["title"] = str(title)
        self.search_sources.append(source)

    def _extract_sources_from_annotation(self, annotation: dict[str, Any]) -> None:
        for key in ("url", "uri", "href", "source_url", "sourceUrl", "canonical_url", "canonicalUrl"):
            value = annotation.get(key)
            if _is_http_url(value):
                self._add_source(str(value), annotation.get("title") or annotation.get("label"))
        for text_key in ("text", "title", "label", "content"):
            value = annotation.get(text_key)
            if isinstance(value, str):
                for url in _extract_urls(value):
                    self._add_source(url, annotation.get("title") or annotation.get("label"))

    def _extract_sources_from_value(self, value: Any) -> None:
        if isinstance(value, dict):
            direct_url = None
            for key in ("url", "uri", "href", "source_url", "sourceUrl", "canonical_url", "canonicalUrl", "link"):
                candidate = value.get(key)
                if _is_http_url(candidate):
                    direct_url = str(candidate)
                    break
            if direct_url:
                self._add_source(direct_url, value.get("title") or value.get("name") or value.get("label"))
            for item in value.values():
                self._extract_sources_from_value(item)
            return
        if isinstance(value, list):
            for item in value:
                self._extract_sources_from_value(item)
            return
        if isinstance(value, str):
            for url in _extract_urls(value):
                self._add_source(url)

    def record_tool_call(
        self,
        *,
        call_id: str | None,
        name: str | None,
        arguments_delta: str = "",
        arguments_full: str | None = None,
    ) -> None:
        normalized_call_id = call_id or _new_id("call")
        call = self.tool_calls.get(normalized_call_id)
        if not call:
            call = {
                "id": normalized_call_id,
                "type": "function",
                "function": {"name": name, "arguments": ""},
            }
            self.tool_calls[normalized_call_id] = call
            self.tool_indexes[normalized_call_id] = len(self.tool_order)
            self.tool_order.append(normalized_call_id)
        if name and not call["function"].get("name"):
            call["function"]["name"] = name
        if arguments_full is not None:
            call["function"]["arguments"] = arguments_full
        elif arguments_delta:
            call["function"]["arguments"] += arguments_delta

    def _record_message_item(self, item: dict[str, Any]) -> None:
        content = item.get("content") or []
        if isinstance(content, dict):
            content = [content]
        text_parts: list[str] = []
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"output_text", "text", "input_text"}:
                text = part.get("text") or part.get("content") or ""
                if text:
                    text_parts.append(str(text))
                for annotation in part.get("annotations") or []:
                    self._append_annotation(annotation)
                    self._extract_sources_from_annotation(annotation)
            elif part_type in {"output_image", "image", "image_url", "input_image"}:
                image_url = part.get("image_url")
                if isinstance(image_url, dict):
                    image_url = image_url.get("url")
                if not image_url:
                    image_url = part.get("url")
                if isinstance(image_url, str) and image_url:
                    self.images.append(image_url)
            elif part_type and "reasoning" in part_type:
                reasoning_text = part.get("text") or part.get("summary") or part.get("content") or ""
                if reasoning_text:
                    self.reasoning_parts.append(str(reasoning_text))
        for annotation in item.get("annotations") or []:
            self._append_annotation(annotation)
            self._extract_sources_from_annotation(annotation)
        self._extract_sources_from_value(item.get("search_sources"))
        if text_parts:
            self.final_output_text = "".join(text_parts)

    def _record_output_item(self, item: dict[str, Any]) -> None:
        item_type = item.get("type")
        if item_type == "message":
            self._record_message_item(item)
            return
        if item_type in {"function_call", "tool_call"}:
            self.record_tool_call(
                call_id=item.get("call_id") or item.get("id"),
                name=item.get("name"),
                arguments_full=_json_dumps(item.get("arguments") or ""),
            )
            return
        if item_type == "web_search_call":
            before = len(self.search_sources)
            self._extract_sources_from_value(item)
            if len(self.search_sources) > before:
                self._has_web_search_sources = True
            return
        if item_type and "reasoning" in item_type:
            reasoning_text = item.get("summary") or item.get("text") or item.get("content") or ""
            if reasoning_text:
                self.final_reasoning_text = str(reasoning_text)

    def apply_response_object(self, response: dict[str, Any]) -> None:
        if not isinstance(response, dict):
            return
        self.raw_response = deepcopy(response)
        self.response_id = response.get("id") or self.response_id
        self.created_at = response.get("created_at") or response.get("created") or self.created_at
        self.status = response.get("status") or self.status
        usage = response.get("usage")
        if isinstance(usage, dict):
            self.usage = deepcopy(usage)
        self._extract_sources_from_value(response.get("search_sources"))
        for item in response.get("output") or []:
            if isinstance(item, dict):
                self._record_output_item(item)
        if not self._has_web_search_sources:
            for annotation in self.annotations:
                self._extract_sources_from_annotation(annotation)

    def apply_event(self, event_type: str, payload: dict[str, Any]) -> None:
        event_type = (event_type or payload.get("type") or "").strip()

        usage = payload.get("usage")
        if isinstance(usage, dict):
            self.usage = deepcopy(usage)

        if event_type == "response.created":
            response = payload.get("response")
            if isinstance(response, dict):
                self.apply_response_object(response)
            return

        if event_type == "response.in_progress":
            response = payload.get("response")
            if isinstance(response, dict):
                self.response_id = response.get("id") or self.response_id
                self.created_at = response.get("created_at") or response.get("created") or self.created_at
            return

        if event_type.endswith("output_text.delta"):
            delta = payload.get("delta") or ""
            if delta:
                self.output_text_parts.append(str(delta))
            return

        if event_type.endswith("output_text.done"):
            text = payload.get("text")
            if isinstance(text, str) and text:
                self.final_output_text = text
            annotations = payload.get("annotations") or []
            for annotation in annotations:
                self._append_annotation(annotation)
                self._extract_sources_from_annotation(annotation)
            return

        if event_type.endswith("output_text.annotation.added"):
            annotation = payload.get("annotation")
            if isinstance(annotation, dict):
                self._append_annotation(annotation)
                self._extract_sources_from_annotation(annotation)
            return

        if "reasoning" in event_type and event_type.endswith(".delta"):
            delta = payload.get("delta") or payload.get("text") or ""
            if delta:
                self.reasoning_parts.append(str(delta))
            return

        if "reasoning" in event_type and event_type.endswith(".done"):
            text = payload.get("text") or payload.get("summary")
            if isinstance(text, str) and text:
                self.final_reasoning_text = text
            return

        if event_type.endswith("function_call_arguments.delta"):
            self.record_tool_call(
                call_id=payload.get("call_id") or payload.get("item_id"),
                name=payload.get("name"),
                arguments_delta=str(payload.get("delta") or ""),
            )
            return

        if event_type.endswith("function_call_arguments.done"):
            self.record_tool_call(
                call_id=payload.get("call_id") or payload.get("item_id"),
                name=payload.get("name"),
                arguments_full=_json_dumps(payload.get("arguments") or ""),
            )
            return

        if event_type.endswith("output_item.added") or event_type.endswith("output_item.done"):
            item = payload.get("item")
            if isinstance(item, dict):
                self._record_output_item(item)
            return

        if "web_search" in event_type:
            before = len(self.search_sources)
            self._extract_sources_from_value(payload)
            if len(self.search_sources) > before:
                self._has_web_search_sources = True
            return

        if event_type == "response.completed":
            response = payload.get("response")
            if isinstance(response, dict):
                self.apply_response_object(response)
            return

        response = payload.get("response")
        if isinstance(response, dict):
            self.apply_response_object(response)

    def output_text(self) -> str:
        text = self.final_output_text
        if text is None:
            text = "".join(self.output_text_parts)
        return text or ""

    def reasoning_text(self) -> str:
        text = self.final_reasoning_text
        if text is None:
            text = "".join(self.reasoning_parts)
        return text or ""

    def ordered_tool_calls(self) -> list[dict[str, Any]]:
        ordered: list[dict[str, Any]] = []
        for call_id in self.tool_order:
            call = self.tool_calls.get(call_id)
            if call:
                ordered.append(deepcopy(call))
        for call_id, call in self.tool_calls.items():
            if call_id not in self.tool_order:
                ordered.append(deepcopy(call))
        return ordered

    def chat_usage(self) -> dict[str, Any]:
        if not self.usage:
            return _default_chat_usage()

        usage = self.usage
        prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        prompt_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("output_tokens_details") or usage.get("completion_tokens_details") or {}

        normalized = _default_chat_usage()
        normalized["prompt_tokens"] = prompt_tokens
        normalized["completion_tokens"] = completion_tokens
        normalized["total_tokens"] = total_tokens
        normalized["prompt_tokens_details"]["cached_tokens"] = int(prompt_details.get("cached_tokens") or 0)
        normalized["prompt_tokens_details"]["text_tokens"] = int(prompt_details.get("text_tokens") or prompt_tokens or 0)
        normalized["prompt_tokens_details"]["audio_tokens"] = int(prompt_details.get("audio_tokens") or 0)
        normalized["prompt_tokens_details"]["image_tokens"] = int(prompt_details.get("image_tokens") or 0)
        normalized["completion_tokens_details"]["text_tokens"] = int(completion_details.get("text_tokens") or completion_tokens or 0)
        normalized["completion_tokens_details"]["audio_tokens"] = int(completion_details.get("audio_tokens") or 0)
        normalized["completion_tokens_details"]["reasoning_tokens"] = int(
            completion_details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0
        )
        return normalized

    def rendered_text(self) -> str:
        text = self.output_text()
        if self.images:
            image_lines = [f"![image]({url})" for url in self.images if isinstance(url, str) and url]
            if image_lines:
                text = "\n".join(part for part in [text, *image_lines] if part)
        return text

    def to_console_response_object(self) -> dict[str, Any]:
        if self.raw_response:
            response = deepcopy(self.raw_response)
        else:
            response = {
                "id": self.response_id,
                "object": "response",
                "created_at": self.created_at,
                "status": self.status,
                "model": self.public_model,
                "output": [],
                "usage": self.usage or None,
            }
        response["model"] = self.public_model
        if self.search_sources:
            response["search_sources"] = deepcopy(self.search_sources)
        return response


async def iter_sse_events(
    raw_stream: AsyncIterable[Any],
) -> AsyncGenerator[tuple[str | None, str | None], None]:
    event_name: str | None = None
    data_parts: list[str] = []

    async for raw_line in raw_stream:
        if raw_line is None:
            continue
        if isinstance(raw_line, (bytes, bytearray)):
            line = raw_line.decode("utf-8", errors="ignore")
        else:
            line = str(raw_line)
        line = line.rstrip("\r\n")

        if line == "":
            if event_name is not None or data_parts:
                yield event_name, "\n".join(data_parts)
            event_name = None
            data_parts = []
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip() or None
            continue
        if line.startswith("data:"):
            data_parts.append(line[5:].lstrip())
            continue

    if event_name is not None or data_parts:
        yield event_name, "\n".join(data_parts)


def format_sse(event_name: str | None, payload: Any) -> str:
    data = payload if isinstance(payload, str) else orjson.dumps(payload).decode()
    if event_name:
        return f"event: {event_name}\ndata: {data}\n\n"
    return f"data: {data}\n\n"


def maybe_json_loads(data: str | None) -> Any:
    if data is None:
        return None
    stripped = data.strip()
    if not stripped or stripped == "[DONE]":
        return stripped
    try:
        return orjson.loads(stripped)
    except orjson.JSONDecodeError:
        return stripped


__all__ = [
    "ConsoleResponseState",
    "build_console_chat_payload",
    "build_console_responses_payload",
    "ensure_console_web_search",
    "format_sse",
    "iter_sse_events",
    "maybe_json_loads",
    "normalize_console_input",
    "normalize_console_tool_choice",
    "openai_messages_to_console_input",
]
