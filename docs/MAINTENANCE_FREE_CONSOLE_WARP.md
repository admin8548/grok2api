# Free Console + WARP/FlareSolverr 后期维护文档

> 维护范围：当前项目以 `chenyme/grok2api` 代码体系为主干，只吸收 `jiujiu532/grok2api` 中与 **console.x.ai 免费账号模型**、**console payload/header**、**WARP + FlareSolverr 稳定访问** 有关的能力。不要整体迁移参考项目的收费账号体系。

---

## 1. 当前维护定位

当前仓库应按“三层来源”维护：

```text
主上游：chenyme/grok2api
参考上游：jiujiu532/grok2api
本项目：以当前分支为基础的下游功能增强分支
```

### 主上游职责

`chenyme/grok2api` 负责主框架：

```text
FastAPI 路由
现有 app/services/grok 业务结构
现有 token manager
现有 image/video/cache/admin/webui 功能
基础 Dockerfile / pyproject / 依赖
```

### 参考上游职责

`jiujiu532/grok2api` 只作为这些功能的参考来源：

```text
免费 console.x.ai 模型列表
public model -> upstream console model 映射
fixed reasoning effort 映射
console.x.ai payload 字段
console 专用 headers
WARP + Privoxy + FlareSolverr compose
console 独立配额/冷却思路
```

### 本项目职责

本项目需要保持：

```text
当前已有 API 兼容性
当前已有模型兼容性
当前已有数据目录和配置格式兼容性
新增免费 console 模型能力
新增 WARP/FlareSolverr 稳定访问能力
清晰可回滚、可审计的功能模块
```

---

## 2. 分支规划

当前已有基础分支：

```text
fix/codex-grok420-reasoning-20260603
```

该分支定位为：

```text
Codex / console responses / grok-4.20 reasoning 兼容修复基础分支
```

不建议继续把所有新功能堆到该分支。建议从该分支切出新的集成分支：

```bash
git switch fix/codex-grok420-reasoning-20260603
git tag safety/fix-codex-grok420-reasoning-20260603-b056f78 b056f78
git switch -c integration/free-console-warp-202606
```

后续功能分支建议：

```text
fix/console-payload-stabilize
feature/free-console-models
feature/console-headers-clearance
feature/warp-flaresolverr
feature/console-rate-limit
feature/free-console-docs-webui
```

推荐合并顺序：

```text
fix/console-payload-stabilize
  -> integration/free-console-warp-202606
feature/free-console-models
  -> integration/free-console-warp-202606
feature/console-headers-clearance
  -> integration/free-console-warp-202606
feature/warp-flaresolverr
  -> integration/free-console-warp-202606
feature/console-rate-limit
  -> integration/free-console-warp-202606
feature/free-console-docs-webui
  -> integration/free-console-warp-202606
```

合并稳定后再进入 `main`。

---

## 3. Remote 规划

建议添加两个上游 remote：

```bash
git remote add upstream-chenyme https://github.com/chenyme/grok2api.git
git remote add upstream-jiujiu https://github.com/jiujiu532/grok2api.git
git fetch --all --prune
```

如果 remote 已存在，则只需：

```bash
git fetch --all --prune
```

查看 remote：

```bash
git remote -v
```

---

## 4. 上游同步策略

### 4.1 同步主上游 chenyme

主上游可以正常 merge/rebase，但必须先在测试分支验证。

推荐流程：

```bash
git fetch upstream-chenyme

git switch main
git merge upstream-chenyme/main

python3 -m compileall app
```

然后将 integration 分支更新到最新 main：

```bash
git switch integration/free-console-warp-202606
git rebase main
# 或者 git merge main
```

重点检查这些文件是否发生冲突：

```text
app/services/grok/services/model.py
app/services/grok/services/console.py
app/services/reverse/console_responses.py
app/services/reverse/protocol/xai_console.py
app/services/reverse/utils/headers.py
config.defaults.toml
docker-compose.yml
Dockerfile
```

### 4.2 同步参考上游 jiujiu

不要直接 merge `jiujiu532/grok2api`。只做差异审计。

推荐审计命令：

```bash
git fetch upstream-jiujiu

git diff <last_jiujiu_commit>..upstream-jiujiu/main \
  -- app/control/model/registry.py \
     app/dataplane/reverse/protocol/xai_console_chat.py \
     app/dataplane/proxy/adapters/headers.py \
     docker-compose.warp.yml \
     scripts/init_proxy_config.py \
     README.md \
     docs/README.en.md
```

只同步这些语义：

```text
新增/删除免费 console 模型
public model -> upstream console model 映射变化
fixed reasoning effort 变化
max_output_tokens 变化
console tools 字段变化
console headers 字段变化
WARP/FlareSolverr compose 变化
clearance 刷新配置变化
console 限流建议变化
```

不要同步这些内容：

```text
收费账号模型体系
basic/super/heavy 完整账号池重构
付费模型 quota 同步
整套 app/control + app/products + app/dataplane 架构
无关 UI 重构
无关管理员后台重构
```

---

## 5. 建议模块边界

为了减少未来冲突，建议新增独立模块承载免费 console 逻辑：

```text
app/services/console_free/
├── __init__.py
├── catalog.py      # 免费 console 模型、映射、fixed effort、max token
├── payload.py      # console.x.ai payload 构造
├── headers.py      # console 专用 headers / cookie / clearance
├── limits.py       # console 独立限流/冷却
├── retry.py        # 401/403/429/5xx/timeout 分类
└── README.md       # 模块维护说明
```

原项目只做少量接线：

```text
app/services/grok/services/model.py
  -> 从 console_free.catalog 导入并注册模型，或调用 catalog 扩展模型列表

app/services/grok/services/console.py
  -> 调用 console_free.payload 构造 payload

app/services/reverse/console_responses.py
  -> 调用 console_free.headers 构造 console headers

app/services/reverse/protocol/xai_console.py
  -> 保留响应解析和 OpenAI 兼容转换，修复 bug

config.defaults.toml
  -> 新增 [console] / [proxy.egress] / [proxy.clearance] 配置

docker-compose.warp.yml
  -> 新增 WARP + Privoxy + FlareSolverr 部署
```

---

## 6. 免费 console 模型维护表

必须维护这张映射表。

| Public model | Upstream console model | Fixed effort | max_output_tokens | tools |
|---|---|---:|---:|---|
| `grok-4.3-console` | `grok-4.3` | default medium | 1000000 | optional |
| `grok-4.3-low` | `grok-4.3` | low | 1000000 | optional |
| `grok-4.3-medium` | `grok-4.3` | medium | 1000000 | optional |
| `grok-4.3-high` | `grok-4.3` | high | 1000000 | optional |
| `grok-4.20-0309-console` | `grok-4.20-0309` | default | 1000000 | search |
| `grok-4.20-0309-reasoning-console` | `grok-4.20-0309-reasoning` | default | 1000000 | search |
| `grok-4.20-0309-non-reasoning-console` | `grok-4.20-0309-non-reasoning` | none | 1000000 | search |
| `grok-4.20-multi-agent-console` | `grok-4.20-multi-agent-0309` | default medium | 2000000 | search + x_search |
| `grok-4.20-multi-agent-low` | `grok-4.20-multi-agent-0309` | low | 2000000 | search + x_search |
| `grok-4.20-multi-agent-medium` | `grok-4.20-multi-agent-0309` | medium | 2000000 | search + x_search |
| `grok-4.20-multi-agent-high` | `grok-4.20-multi-agent-0309` | high | 2000000 | search + x_search |
| `grok-4.20-multi-agent-xhigh` | `grok-4.20-multi-agent-0309` | xhigh | 2000000 | search + x_search |
| `grok-build-console` | `grok-build-0.1` | default medium | 256000 | search + x_search |

注意：

```text
fixed effort 优先于用户传入 reasoning_effort
```

即：

```text
grok-4.20-multi-agent-xhigh + reasoning_effort=low
仍然应使用 xhigh
```

---

## 7. Payload 维护规则

console.x.ai 免费模型应走：

```text
POST https://console.x.ai/v1/responses
```

推荐 payload 基础字段：

```json
{
  "model": "<upstream_console_model>",
  "input": [],
  "stream": true,
  "store": false,
  "include": ["reasoning.encrypted_content"],
  "max_output_tokens": 1000000,
  "temperature": 0.7,
  "top_p": 0.95
}
```

reasoning 字段规则：

```text
只对这些 upstream model 传 reasoning：
- grok-4.3
- grok-4.20-multi-agent-0309
```

不要对所有 grok-4.20 console 模型盲传 reasoning，否则可能触发 400。

multi-agent / build / search 模型建议启用：

```json
"tools": [
  {"type": "web_search", "enable_image_understanding": true},
  {"type": "x_search", "enable_video_understanding": true}
],
"tool_choice": "auto"
```

如果当前上游不接受扩展字段，则降级为：

```json
"tools": [
  {"type": "web_search"},
  {"type": "x_search"}
]
```

---

## 8. Header / Cookie 维护规则

console.x.ai 请求必须使用专用 header，不要完全复用 grok.com app-chat header。

推荐 header：

```text
Authorization: Bearer anonymous
Cookie: sso=<token>; sso-rw=<token>; cf_clearance=<clearance>
Origin: https://console.x.ai
Referer: https://console.x.ai/
Accept: text/event-stream 或 */*
Content-Type: application/json
User-Agent: 与 cf_clearance 获取时一致
x-cluster: https://us-east-1.api.x.ai
```

敏感信息禁止进入日志：

```text
sso
sso-rw
cf_clearance
api_key
Authorization
完整 Cookie
```

允许记录：

```text
token hash 前 12 位
payload sha
状态码
脱敏后的 payload summary
```

---

## 9. WARP + FlareSolverr 维护规则

推荐链路：

```text
grok2api -> privoxy -> warp-proxy -> console.x.ai / grok.com
```

推荐 compose 服务：

```text
init-config
warp-proxy
privoxy
flaresolverr
grok2api
```

配置建议：

```toml
[proxy.egress]
mode = "single_proxy"
proxy_url = "http://privoxy:8118"
resource_proxy_url = "http://privoxy:8118"

[proxy.clearance]
mode = "flaresolverr"
flaresolverr_url = "http://flaresolverr:8191"
timeout_sec = 60
refresh_interval = 3600
browser = "chrome136"
user_agent = "Mozilla/5.0 ... Chrome/136.0.0.0 Safari/537.36"
```

兼容当前旧配置：

```toml
[proxy]
base_proxy_url = "http://privoxy:8118"
asset_proxy_url = "http://privoxy:8118"
enabled = true
flaresolverr_url = "http://flaresolverr:8191"
refresh_interval = 3600
timeout = 60
```

如果引入嵌套配置，必须确认 `get_config()` 支持深层 dotted path，例如：

```text
proxy.egress.mode
proxy.clearance.flaresolverr_url
```

当前项目的 `get_config()` 只可靠支持一层 dotted path：

```text
section.key
```

所以如果要读取 `proxy.clearance.mode`，需要增强 `app/core/config.py`。

---

## 10. 限流和错误反馈维护规则

建议新增配置：

```toml
[console]
enabled = true
default_effort = "medium"
rate_limit_count = 30
rate_limit_window_sec = 900
max_retry_tokens = 20
```

错误处理策略：

| 状态 | 含义 | 建议处理 |
|---|---|---|
| 401 | SSO token 失效 | record_fail / 标记账号失效 |
| 403 | clearance / CF / 代理问题 | 刷新 clearance / 标记代理异常 |
| 429 | console 配额或上游限流 | token 冷却 15 分钟 |
| blank 400 | 会话或 payload 边界问题 | 换 token 重试 |
| 500/502/503/504 | 上游或网络问题 | 换 token / proxy 重试 |
| timeout | 网络或上游超时 | 换 token / proxy 重试 |

---

## 11. 必须验证的命令

每次功能改动后至少运行：

```bash
python3 -m compileall app
```

如果有 Docker 改动：

```bash
docker compose config
docker compose build
```

如果有 WARP compose：

```bash
docker compose -f docker-compose.warp.yml config
docker compose -f docker-compose.warp.yml build
```

服务启动后：

```bash
curl http://127.0.0.1:8000/v1/models \
  -H "Authorization: Bearer $API_KEY"
```

必须能看到：

```text
grok-4.20-multi-agent-xhigh
grok-4.20-multi-agent-high
grok-4.3-high
grok-build-console
```

smoke test：

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "grok-4.20-multi-agent-xhigh",
    "stream": false,
    "messages": [{"role":"user","content":"只回复 OK"}]
  }'
```

Responses API：

```bash
curl http://127.0.0.1:8000/v1/responses \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "grok-4.20-multi-agent-xhigh",
    "stream": false,
    "input": "只回复 OK"
  }'
```

日志检查：

```bash
grep -RInE "sso=|sso-rw=|cf_clearance=|Authorization: Bearer|sk-" logs app 2>/dev/null
```

日志不得泄露敏感值。

---

## 12. 上游锁文件建议

建议新增：

```text
.upstream-lock.toml
```

示例：

```toml
[upstream]
chenyme = "d76b5e4"
jiujiu = "27a16163c11f4a5d16ca00388ae1edb0c5ee7d45"

[branches]
base = "fix/codex-grok420-reasoning-20260603"
integration = "integration/free-console-warp-202606"

[imported]
free_console_models = false
console_payload = false
console_headers = false
warp_flaresolverr = false
console_rate_limit = false
paid_models = false
```

每次同步参考项目后更新 `jiujiu` commit。
每次同步主上游后更新 `chenyme` commit。

---

## 13. 发布流程

### 发布前检查

```bash
git status --short
python3 -m compileall app
docker compose config
docker compose build
```

如果是 WARP 版：

```bash
docker compose -f docker-compose.warp.yml config
docker compose -f docker-compose.warp.yml build
```

### 发布分支

```bash
git switch integration/free-console-warp-202606
# 完整验证通过后
git switch main
git merge --no-ff integration/free-console-warp-202606
```

### 回滚

保留安全 tag：

```bash
git tag safety/<name> <commit>
```

回滚代码：

```bash
git switch main
git reset --hard <safe_commit>
```

回滚容器：

```bash
docker compose down
docker compose up -d --build
```

---

## 14. 维护节奏

建议：

```text
每周：fetch 两个 upstream，审计 jiujiu console 相关 diff
每月：完整 rebase/merge 主上游，跑 Docker build
每次发布前：/v1/models、chat、responses、日志脱敏 smoke test
```

---

## 15. 维护原则

最终原则：

```text
当前项目稳定运行 > 免费 console 功能正确 > 参考项目新实现 > 收费账号相关变化
```

不要让项目变成两个仓库的混合大杂烩。
应保持：

```text
chenyme 主体 + 独立 console_free 模块 + 可审计的 jiujiu 功能补丁
```
