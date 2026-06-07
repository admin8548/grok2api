# Optional WARP + Privoxy + FlareSolverr deployment

The default `docker-compose.yml` stays unchanged. Use this stack only when you
want the v2 runtime to send Grok traffic through WARP and refresh Cloudflare
clearance through FlareSolverr.

## Start

```bash
docker compose -f docker-compose.warp.yml up -d --build
```

Services:

- `warp-proxy`: WARP SOCKS proxy container.
- `privoxy`: local HTTP proxy that forwards to `warp-proxy:1080`.
- `flaresolverr`: optional clearance provider.
- `init-config`: one-shot config updater for `data/config.toml`.
- `grok2api`: local build image `grok2api-local:accountsdb-free-console-warp`.

## Config written by `scripts/init_proxy_config.py`

Only these proxy sections are upserted:

```toml
[proxy.egress]
mode = "single_proxy"
proxy_url = "http://privoxy:8118"
resource_proxy_url = "http://privoxy:8118"
skip_ssl_verify = false

[proxy.clearance]
mode = "flaresolverr"
flaresolverr_url = "http://flaresolverr:8191"
timeout_sec = 60
refresh_interval = 3600
```

The script preserves app keys, account storage, SSO tokens, Cloudflare cookies,
and unrelated settings.

## Verify

```bash
docker compose -f docker-compose.warp.yml config
docker compose -f docker-compose.warp.yml build
curl -x http://127.0.0.1:40080 https://ifconfig.me
```

Use standard `docker-compose.yml` when WARP/FlareSolverr is not needed.
