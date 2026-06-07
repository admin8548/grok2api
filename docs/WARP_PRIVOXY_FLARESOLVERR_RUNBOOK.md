# WARP + Privoxy + FlareSolverr 操作步骤文档（v2 / accounts.db）

> 适用分支：`integration/accountsdb-free-console-warp-202606`
>
> 适用账号源：`data/accounts.db`
>
> 本文档用于后续本地运维备用。不要在文档、日志、Git 提交或聊天中输出真实 `api_key`、`app_key`、SSO token、`cf_clearance`、`cf_cookies`。

## 0. 当前功能说明

新增功能分为两部分：

```text
1. free-console 模型能力
2. WARP + Privoxy + FlareSolverr 可选代理/clearance 栈
```

当前推荐保留两个分支：

```text
main
integration/accountsdb-free-console-warp-202606
```

其中：

```text
main = 主线/上游同步基线
integration/accountsdb-free-console-warp-202606 = 当前真正运行和维护的增强分支
```

## 1. 启动前检查

进入项目目录：

```bash
cd /home/ubuntu/grok2api
```

确认分支：

```bash
git status --short --branch
git branch --format='%(refname:short)'
```

预期当前分支：

```text
integration/accountsdb-free-console-warp-202606
```

确认账号库：

```bash
sqlite3 data/accounts.db \
  "SELECT pool,status,COUNT(*) FROM accounts WHERE deleted_at IS NULL GROUP BY pool,status;"
```

当前预期：

```text
basic|active|2586
```

## 2. 备份配置

每次切换代理/clearance 模式前，先备份配置：

```bash
cd /home/ubuntu/grok2api
mkdir -p data/backups
cp data/config.toml data/backups/config.toml.pre-warp.$(date +%Y%m%d-%H%M%S).bak
```

不要备份到 Git 追踪目录之外的公开位置。

## 3. 启动方式 A：只启用增强镜像，不启用 WARP

适合场景：

```text
只需要 free-console 模型能力
不需要 WARP 出口
不需要 FlareSolverr 自动刷新 clearance
```

构建本地镜像：

```bash
cd /home/ubuntu/grok2api

docker build -t grok2api-local:accountsdb-free-console-warp .
```

创建临时 override：

```bash
cat > /tmp/grok2api.accountsdb-local.override.yml <<'YAML'
services:
  grok2api:
    image: grok2api-local:accountsdb-free-console-warp
YAML
```

启动：

```bash
docker compose -f docker-compose.yml \
  -f /tmp/grok2api.accountsdb-local.override.yml \
  up -d --force-recreate grok2api
```

验证：

```bash
docker ps --filter name=grok2api --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

预期镜像：

```text
grok2api-local:accountsdb-free-console-warp
```

## 4. 启动方式 B：启用 WARP + Privoxy + FlareSolverr

适合场景：

```text
需要 WARP 出口
需要 HTTP proxy 统一接入
需要 FlareSolverr 自动获取/刷新 Cloudflare clearance
```

启动完整栈：

```bash
cd /home/ubuntu/grok2api

docker compose -f docker-compose.warp.yml config
docker compose -f docker-compose.warp.yml up -d --build
```

会启动以下服务：

```text
grok2api
warp-proxy
privoxy
flaresolverr
init-config
```

查看状态：

```bash
docker compose -f docker-compose.warp.yml ps
```

## 5. WARP compose 会写入的配置

`init-config` 会自动更新 `data/config.toml` 的代理相关部分：

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

注意：

```text
http://privoxy:8118       = 容器内部地址
http://flaresolverr:8191  = 容器内部地址
http://127.0.0.1:40080    = 宿主机测试 Privoxy 用
http://127.0.0.1:8191     = 宿主机测试 FlareSolverr 用
```

不要把容器内部配置改成 `127.0.0.1`，否则 `grok2api` 容器内无法访问。

## 6. 自动 clearance 工作方式

运行链路：

```text
grok2api
  -> ProxyDirectory acquire lease
  -> FlareSolverr 使用同一个 proxy 出口访问目标站点
  -> 返回 cookies + userAgent
  -> grok2api 把 sso/sso-rw + cf_clearance 自动拼进 Cookie
  -> 请求成功后复用运行时 ClearanceBundle
```

关键点：

```text
1. cf_clearance 默认不会写回 data/config.toml
2. clearance 会缓存在运行时 ClearanceBundle 中
3. 请求头由 app/dataplane/proxy/adapters/headers.py 自动构造
4. 如果遇到 403/challenge，代码会标记 bundle 失效，后续重新获取
5. 普通 grok.com 默认启动时 warm-up
6. console.x.ai 会在第一次 console-free 请求时按需获取对应域名 clearance
```

相关代码：

```text
app/control/proxy/providers/flaresolverr.py
app/control/proxy/__init__.py
app/control/proxy/scheduler.py
app/dataplane/proxy/adapters/headers.py
app/products/openai/console_free.py
```

## 7. 验证 Privoxy/WARP 出口

宿主机测试：

```bash
curl -x http://127.0.0.1:40080 https://ifconfig.me
```

如果能返回 IP，说明：

```text
宿主机 -> Privoxy -> WARP
```

链路可用。

查看日志：

```bash
docker logs warp-proxy --since=10m
docker logs privoxy --since=10m
```

## 8. 验证 FlareSolverr，不输出敏感 Cookie

宿主机执行：

```bash
cd /home/ubuntu/grok2api

python3 - <<'PY'
import json
import urllib.request

payload = {
    "cmd": "request.get",
    "url": "https://grok.com",
    "maxTimeout": 60000,
    "proxy": {"url": "http://privoxy:8118"},
}

req = urllib.request.Request(
    "http://127.0.0.1:8191/v1",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(req, timeout=90) as r:
    data = json.loads(r.read().decode())

solution = data.get("solution", {})
cookies = solution.get("cookies", [])
names = [c.get("name") for c in cookies]

print("status:", data.get("status"))
print("cookie_names:", names)
print("has_cf_clearance:", "cf_clearance" in names)
print("has_user_agent:", bool(solution.get("userAgent")))
PY
```

预期重点：

```text
status: ok
has_cf_clearance: True
has_user_agent: True
```

不要打印完整 Cookie 值。

## 9. 验证 grok2api 读取自动 clearance 模式

查看日志：

```bash
docker logs grok2api --since=10m | grep -Ei "proxy directory|clearance|flaresolverr"
```

预期能看到类似：

```text
proxy directory loaded: egress_mode=single_proxy clearance_mode=flaresolverr
proxy clearance scheduler started
proxy clearance warm-up completed
```

如果没有看到，可以重启：

```bash
docker restart grok2api
sleep 5
docker logs grok2api --since=2m | grep -Ei "proxy directory|clearance|flaresolverr"
```

## 10. 验证 API 模型列表

不要在命令输出中打印完整 key。使用 Python 直接读取配置并请求本机接口：

```bash
cd /home/ubuntu/grok2api

python3 - <<'PY'
import json
import re
import urllib.request
from pathlib import Path

text = Path("data/config.toml").read_text()
m = re.search(r'(?m)^api_key\s*=\s*["\']([^"\']+)["\']', text)
if not m:
    print("api_key_not_found")
    raise SystemExit(1)

req = urllib.request.Request(
    "http://127.0.0.1:8000/v1/models",
    headers={"Authorization": "Bearer " + m.group(1)},
)

with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read().decode())

models = [item.get("id") for item in data.get("data", [])]
console = [
    model for model in models
    if model and (
        "console" in model
        or model in {
            "grok-4.3-low",
            "grok-4.3-medium",
            "grok-4.3-high",
            "grok-4.20-multi-agent-low",
            "grok-4.20-multi-agent-medium",
            "grok-4.20-multi-agent-high",
            "grok-4.20-multi-agent-xhigh",
        }
    )
]

print("models_count:", len(models))
print("console_count:", len(console))
print("has_grok_4_3_console:", "grok-4.3-console" in models)
PY
```

当前预期：

```text
models_count: 16
console_count: 13
has_grok_4_3_console: True
```

## 11. Cherry Studio 配置

如果从本机访问：

```text
Base URL: http://127.0.0.1:8000/v1
API Key: data/config.toml 中 [app].api_key
```

如果从其它设备访问服务器：

```text
Base URL: http://服务器IP:8000/v1
API Key: data/config.toml 中 [app].api_key
```

Admin/Web 登录使用：

```text
data/config.toml 中 [app].app_key
```

本机查看 key，注意不要复制到公开位置：

```bash
cd /home/ubuntu/grok2api
grep -nE '^(app_key|api_key)' data/config.toml
```

## 12. 常用环境变量

`docker-compose.warp.yml` 支持以下变量：

```bash
HOST_PORT=8000
SERVER_PORT=8000
LOG_LEVEL=INFO
GROK_WARP_PROXY_URL=http://privoxy:8118
GROK_FLARESOLVERR_URL=http://flaresolverr:8191
GROK_CLEARANCE_MODE=flaresolverr
GROK_CF_REFRESH_INTERVAL=3600
GROK_CF_TIMEOUT_SEC=60
```

例如修改宿主机端口：

```bash
HOST_PORT=18000 docker compose -f docker-compose.warp.yml up -d --build
```

## 13. 切回手动 clearance 模式

如果 FlareSolverr 暂时不可用，可以切回 manual：

```toml
[proxy.clearance]
mode = "manual"
cf_cookies = "<手动获取的 cookie 串>"
user_agent = "<对应浏览器 UA>"
```

然后重启：

```bash
docker restart grok2api
```

注意：不要把真实 `cf_cookies`、`cf_clearance` 提交到 Git。

## 14. 恢复到非 WARP 模式

如果不需要 WARP/FlareSolverr：

```bash
docker compose -f docker-compose.warp.yml down

cat > /tmp/grok2api.accountsdb-local.override.yml <<'YAML'
services:
  grok2api:
    image: grok2api-local:accountsdb-free-console-warp
YAML

docker compose -f docker-compose.yml \
  -f /tmp/grok2api.accountsdb-local.override.yml \
  up -d --force-recreate grok2api
```

如需恢复配置：

```bash
ls -lt data/backups/config.toml.pre-warp.*.bak | head
cp data/backups/config.toml.pre-warp.<时间>.bak data/config.toml
docker restart grok2api
```

## 15. 故障排查

### 15.1 Privoxy 测试失败

```bash
curl -x http://127.0.0.1:40080 https://ifconfig.me
```

如果失败，检查：

```bash
docker logs warp-proxy --since=10m
docker logs privoxy --since=10m
docker compose -f docker-compose.warp.yml ps
```

### 15.2 FlareSolverr 连接失败

检查服务：

```bash
docker logs flaresolverr --since=10m
docker compose -f docker-compose.warp.yml ps flaresolverr
```

配置里容器内部地址必须是：

```text
http://flaresolverr:8191
```

### 15.3 grok2api 没有进入 flaresolverr 模式

检查配置：

```bash
grep -nA12 -E '^\[proxy\.egress\]|^\[proxy\.clearance\]' data/config.toml
```

重启：

```bash
docker restart grok2api
```

### 15.4 反复 403 / challenge

查看日志：

```bash
docker logs grok2api --since=10m | grep -Ei "403|challenge|clearance|flaresolverr"
```

处理顺序：

```text
1. 确认 Privoxy/WARP 出口可用
2. 确认 FlareSolverr 返回 status=ok
3. 确认 proxy.clearance.mode=flaresolverr
4. 重启 grok2api 触发 warm-up
5. 如仍异常，临时切回 manual clearance
```

### 15.5 容器仍然使用默认镜像

检查：

```bash
docker ps --filter name=grok2api --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

正确增强镜像应为：

```text
grok2api-local:accountsdb-free-console-warp
```

如果不是，重新用本地增强镜像或 WARP compose 启动。

## 16. 最小日常验收清单

```bash
cd /home/ubuntu/grok2api

git status --short --branch

docker ps --filter name=grok2api --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'

sqlite3 data/accounts.db \
  "SELECT pool,status,COUNT(*) FROM accounts WHERE deleted_at IS NULL GROUP BY pool,status;"

docker logs grok2api --since=5m | grep -Ei "proxy directory|clearance|flaresolverr|error|warning" || true
```

预期：

```text
分支：integration/accountsdb-free-console-warp-202606
容器：healthy
镜像：grok2api-local:accountsdb-free-console-warp
账号：basic|active|2586 或更高
模型：/v1/models 200，且包含 grok-4.3-console
```

## 17. 安全规则

不要提交以下内容：

```text
data/
logs/
.env
api_key
app_key
sso / sso-rw
cf_clearance
cf_cookies
Authorization header
```

检查 Git 状态：

```bash
git status --short --branch
```

如果误加入敏感文件，立刻取消暂存：

```bash
git restore --staged <file>
```
