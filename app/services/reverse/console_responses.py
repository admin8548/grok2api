"""
Reverse interface: console.x.ai /v1/responses.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import orjson
from curl_cffi.requests import AsyncSession

from app.core.config import get_config
from app.core.exceptions import UpstreamException
from app.core.logger import logger
from app.services.reverse.utils.headers import build_headers
from app.services.reverse.utils.retry import retry_on_status
from app.services.token.service import TokenService


CONSOLE_RESPONSES_API = "https://console.x.ai/v1/responses"


def _normalize_proxy(proxy_url: str) -> str:
    if not proxy_url:
        return proxy_url
    parsed = urlparse(proxy_url)
    scheme = parsed.scheme.lower()
    if scheme == "socks5":
        return proxy_url.replace("socks5://", "socks5h://", 1)
    if scheme == "socks4":
        return proxy_url.replace("socks4://", "socks4a://", 1)
    return proxy_url


def _token_hash_for_debug(token: str | None) -> str:
    if not token:
        return "-"
    return hashlib.sha256(token.encode("utf-8", "ignore")).hexdigest()[:12]


def _payload_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a sanitized request summary for debugging blank console 400s."""

    def _content_summary(content: Any) -> list[Dict[str, Any]]:
        if not isinstance(content, list):
            return [
                {
                    "kind": type(content).__name__,
                    "len": len(str(content)) if content is not None else 0,
                }
            ]
        out: list[Dict[str, Any]] = []
        for part in content[:8]:
            if isinstance(part, dict):
                text = part.get("text") or part.get("content") or ""
                text_s = str(text)
                preview = text_s[:160].replace("\n", " ").replace("\r", " ")
                out.append(
                    {
                        "type": part.get("type"),
                        "text_len": len(text_s),
                        "preview": preview,
                    }
                )
            else:
                out.append({"kind": type(part).__name__, "len": len(str(part))})
        return out

    input_summary: list[Dict[str, Any]] = []
    for item in (payload.get("input") or [])[:20]:
        if isinstance(item, dict):
            input_summary.append(
                {
                    "type": item.get("type"),
                    "role": item.get("role"),
                    "call_id": bool(item.get("call_id")),
                    "name": item.get("name"),
                    "content": _content_summary(item.get("content")),
                    "output_len": len(str(item.get("output") or "")),
                    "arguments_len": len(str(item.get("arguments") or "")),
                }
            )
        else:
            input_summary.append({"kind": type(item).__name__, "len": len(str(item))})

    tool_summary: list[Dict[str, Any]] = []
    for tool in (payload.get("tools") or [])[:50]:
        if not isinstance(tool, dict):
            tool_summary.append({"kind": type(tool).__name__})
            continue
        params = tool.get("parameters") if isinstance(tool.get("parameters"), dict) else {}
        props = params.get("properties") if isinstance(params.get("properties"), dict) else {}
        tool_summary.append(
            {
                "type": tool.get("type"),
                "name": tool.get("name"),
                "strict": tool.get("strict"),
                "param_type": params.get("type"),
                "prop_keys": list(props.keys())[:20],
                "required_count": len(params.get("required") or []),
            }
        )

    return {
        "keys": sorted(payload.keys()),
        "model": payload.get("model"),
        "stream": payload.get("stream"),
        "instructions_len": len(str(payload.get("instructions") or "")),
        "input_count": len(payload.get("input") or []),
        "input": input_summary,
        "tools_count": len(payload.get("tools") or []),
        "tools": tool_summary,
        "reasoning": payload.get("reasoning"),
        "max_output_tokens": payload.get("max_output_tokens"),
        "parallel_tool_calls": payload.get("parallel_tool_calls"),
        "temperature": payload.get("temperature"),
        "top_p": payload.get("top_p"),
    }


class ConsoleResponsesReverse:
    @staticmethod
    async def request(
        session: AsyncSession,
        token: str,
        payload: Dict[str, Any],
        *,
        stream: bool,
    ) -> Any:
        try:
            base_proxy = get_config("proxy.base_proxy_url")
            proxy = None
            proxies = None
            if base_proxy:
                normalized_proxy = _normalize_proxy(base_proxy)
                scheme = urlparse(normalized_proxy).scheme.lower()
                if scheme.startswith("socks"):
                    proxy = normalized_proxy
                else:
                    proxies = {"http": normalized_proxy, "https": normalized_proxy}
                logger.info(
                    "ConsoleResponsesReverse proxy enabled: scheme=%s, target=%s",
                    scheme,
                    normalized_proxy,
                )

            headers = build_headers(
                cookie_token=token,
                content_type="application/json",
                origin="https://console.x.ai",
                referer="https://console.x.ai/",
            )
            headers["Accept"] = "text/event-stream" if stream else "application/json"

            timeout = float(get_config("chat.timeout") or 0)
            if timeout <= 0:
                timeout = 300
            browser = get_config("proxy.browser")
            payload_bytes = orjson.dumps(payload)
            payload_sha = hashlib.sha256(payload_bytes).hexdigest()[:16]

            async def _do_request():
                response = await session.post(
                    CONSOLE_RESPONSES_API,
                    headers=headers,
                    data=payload_bytes,
                    timeout=timeout,
                    stream=stream,
                    proxy=proxy,
                    proxies=proxies,
                    impersonate=browser,
                )

                if response.status_code not in {200, 201}:
                    body = ""
                    try:
                        body = await response.text()
                    except Exception:
                        pass

                    error_code = None
                    error_type = None
                    try:
                        parsed = orjson.loads(body)
                    except Exception:
                        parsed = None
                    if isinstance(parsed, dict):
                        err = parsed.get("error") if isinstance(parsed.get("error"), dict) else parsed
                        error_code = err.get("code") or err.get("type")
                        error_type = err.get("type")

                    logger.warning(
                        "console responses rejected payload: status=%s acct=%s payload_sha=%s body=%s summary=%s",
                        response.status_code,
                        _token_hash_for_debug(token),
                        payload_sha,
                        body[:400] if body else "-",
                        _payload_summary(payload),
                    )

                    raise UpstreamException(
                        message=f"ConsoleResponsesReverse failed, {response.status_code}",
                        details={
                            "status": response.status_code,
                            "body": body,
                            "headers": dict(response.headers or {}),
                            "error_code": error_code,
                            "error_type": error_type,
                        },
                    )

                return response

            def extract_status(exc: Exception) -> Optional[int]:
                if not isinstance(exc, UpstreamException):
                    return None
                if exc.details and "status" in exc.details:
                    status = exc.details["status"]
                else:
                    status = getattr(exc, "status_code", None)
                if status in {402, 429}:
                    return None
                return status

            response = await retry_on_status(_do_request, extract_status=extract_status)

            if not stream:
                try:
                    body = await response.text()
                finally:
                    await session.close()
                try:
                    return orjson.loads(body)
                except orjson.JSONDecodeError as exc:
                    raise UpstreamException(
                        message="ConsoleResponsesReverse returned invalid JSON",
                        details={"status": 502, "error": str(exc), "body": body},
                    ) from exc

            async def _stream():
                try:
                    async for line in response.aiter_lines():
                        yield line
                finally:
                    await session.close()

            return _stream()

        except Exception as exc:
            if isinstance(exc, UpstreamException):
                if exc.details and "status" in exc.details:
                    status = exc.details["status"]
                else:
                    status = getattr(exc, "status_code", None)
                if status == 401:
                    try:
                        await TokenService.record_fail(
                            token, status, "console_responses_auth_failed"
                        )
                    except Exception:
                        pass
                raise

            logger.error(
                "ConsoleResponsesReverse request failed: %s",
                exc,
                extra={"error_type": type(exc).__name__},
            )
            raise UpstreamException(
                message=f"ConsoleResponsesReverse failed: {exc}",
                details={"status": 502, "error": str(exc)},
            ) from exc


__all__ = ["ConsoleResponsesReverse"]
