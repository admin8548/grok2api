#!/usr/bin/env python3
"""Initialize optional v2 WARP/Privoxy/FlareSolverr proxy settings.

The script only upserts proxy-related TOML sections in data/config.toml.
It preserves app keys, account storage, SSO tokens, Cloudflare cookies, and
all unrelated settings.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
CONFIG_PATH = DATA_DIR / "config.toml"

PROXY_URL = os.getenv("GROK_WARP_PROXY_URL", "http://privoxy:8118")
FLARESOLVERR_URL = os.getenv("GROK_FLARESOLVERR_URL", "http://flaresolverr:8191")
CLEARANCE_MODE = os.getenv("GROK_CLEARANCE_MODE", "flaresolverr")
REFRESH_INTERVAL = int(os.getenv("GROK_CF_REFRESH_INTERVAL", "3600"))
TIMEOUT_SEC = int(os.getenv("GROK_CF_TIMEOUT_SEC", "60"))

PROXY_EGRESS_SECTION: dict[str, Any] = {
    "mode": "single_proxy",
    "proxy_url": PROXY_URL,
    "resource_proxy_url": PROXY_URL,
    "skip_ssl_verify": False,
}

PROXY_CLEARANCE_SECTION: dict[str, Any] = {
    "mode": CLEARANCE_MODE,
    "user_agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "browser": "chrome136",
    "flaresolverr_url": FLARESOLVERR_URL,
    "timeout_sec": TIMEOUT_SEC,
    "refresh_interval": REFRESH_INTERVAL,
}


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{escaped}"'


def _section_pattern(section: str) -> re.Pattern[str]:
    return re.compile(
        rf"(^\[{re.escape(section)}\]\s*\n)(.*?)(?=^\[[^\]]+\]\s*$|\Z)",
        flags=re.M | re.S,
    )


def _upsert_section(content: str, section: str, values: dict[str, Any]) -> str:
    pattern = _section_pattern(section)
    match = pattern.search(content)
    if not match:
        block = "\n" if content.rstrip() else ""
        block += f"[{section}]\n"
        block += "".join(f"{key} = {_toml_value(value)}\n" for key, value in values.items())
        return content.rstrip() + block

    header, body = match.group(1), match.group(2)
    for key, value in values.items():
        key_pattern = re.compile(rf"(^\s*{re.escape(key)}\s*=\s*).*$", flags=re.M)
        replacement = rf"\g<1>{_toml_value(value)}"
        if key_pattern.search(body):
            body = key_pattern.sub(replacement, body, count=1)
        else:
            if body and not body.endswith("\n"):
                body += "\n"
            body += f"{key} = {_toml_value(value)}\n"
    return content[: match.start()] + header + body + content[match.end() :]


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        content = CONFIG_PATH.read_text(encoding="utf-8")
        action = "Updated"
    else:
        content = ""
        action = "Created"

    content = _upsert_section(content, "proxy.egress", PROXY_EGRESS_SECTION)
    content = _upsert_section(content, "proxy.clearance", PROXY_CLEARANCE_SECTION)
    CONFIG_PATH.write_text(content.rstrip() + "\n", encoding="utf-8")
    print(f"[init-config] {action} proxy settings in {CONFIG_PATH}")


if __name__ == "__main__":
    main()
