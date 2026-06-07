# Free Console + WARP/FlareSolverr `/goal` 后续改进计划

> 目标：通过 Codex `/goal` 分阶段实现免费 console.x.ai 模型与 WARP/FlareSolverr 稳定访问能力。本文档中的每个 Goal 都可以独立复制给 Codex 执行。

---

## 0. 总目标

在当前项目中实现：

```text
免费 console.x.ai 模型
public model -> upstream console model 映射
fixed reasoning effort
console 专用 payload/header
console 独立限流/冷却
WARP + Privoxy + FlareSolverr 部署
文档与验证脚本
```

不实现：

```text
收费账号模型体系
完整 basic/super/heavy 重构
付费模型 quota 同步
grok-4.20-auto/expert/heavy 收费模型
grok-4.3-beta 收费模型
整仓迁移 jiujiu532/grok2api 架构
```

参考仓库：

```text
https://github.com/jiujiu532/grok2api
```

重点参考文件：

```text
app/control/model/registry.py
app/dataplane/reverse/protocol/xai_console_chat.py
app/dataplane/proxy/adapters/headers.py
docker-compose.warp.yml
scripts/init_proxy_config.py
README.md
```

---

## 1. 建议执行顺序

```text
Goal 0: 建立分支和同步锁
Goal 1: 修复 console payload 稳定性
Goal 2: 新增免费 console 模型与映射
Goal 3: 增强 console 专用 headers 和 clearance
Goal 4: 新增 WARP + FlareSolverr 部署
Goal 5: 新增 console 限流、错误分类与重试
Goal 6: 增加测试、验证脚本、文档和 WebUI 展示优化
```

每个 Goal 完成后必须：

```bash
python3 -m compileall app
```

有 Docker 改动时必须：

```bash
docker compose config
docker compose build
```

有 WARP compose 改动时必须：

```bash
docker compose -f docker-compose.warp.yml config
docker compose -f docker-compose.warp.yml build
```

---

# Goal 0：建立分支和同步锁

## 目标

建立后续开发基础分支、上游 remote 和同步锁文件。

## 给 Codex 的 `/goal` 提示词

```text
/goal

目标：为免费 console.x.ai + WARP/FlareSolverr 后续开发建立维护基础，不实现功能代码。

当前工作目录：/home/ubuntu/grok2api
当前基础分支：fix/codex-grok420-reasoning-20260603

任务：
1. 检查 git status，确认工作区干净；如果不干净，先报告不要覆盖。
2. 添加 remote：
   - upstream-chenyme = https://github.com/chenyme/grok2api.git
   - upstream-jiujiu = https://github.com/jiujiu532/grok2api.git
   如果 remote 已存在则跳过。
3. fetch --all --prune。
4. 从 fix/codex-grok420-reasoning-20260603 创建 integration/free-console-warp-202606。
5. 创建安全 tag：safety/fix-codex-grok420-reasoning-20260603-b056f78，如果已存在则跳过。
6. 新增 .upstream-lock.toml，记录：
   - chenyme 当前 origin/main commit
   - jiujiu 当前 upstream-jiujiu/main commit
   - base 分支
   - integration 分支
   - imported 标记全部 false
7. 不修改业务代码。
8. 输出最终分支、remote、lock 文件内容。

验证：
- git remote -v
- git branch --show-current
- cat .upstream-lock.toml
```

## 预期输出

```text
.upstream-lock.toml
```

---

# Goal 1：修复 console payload 稳定性

## 目标

修复当前 console chat payload 构造中的已知 bug，并让 chat/responses payload 构造更稳定。

当前已知 bug：

```text
NameError: name 'normalized_input' is not defined
```

位置：

```text
app/services/reverse/protocol/xai_console.py
```

## 给 Codex 的 `/goal` 提示词

```text
/goal

目标：修复当前 console.x.ai payload 构造稳定性问题，不新增模型，不改 Docker。

工作目录：/home/ubuntu/grok2api
建议分支：fix/console-payload-stabilize，基于 integration/free-console-warp-202606 创建。

任务：
1. 修复 app/services/reverse/protocol/xai_console.py 中 build_console_chat_payload 的 normalized_input / normalized_tools 未定义问题。
2. normalized_input 应使用 openai_messages_to_console_input(messages) 结果转换后的 input_items。
3. normalized_tools 应使用 ensure_console_web_search(tools) 的结果。
4. _force_exec_for_codex_edit_intent 只在存在 exec_command function tool 时生效；没有本地 function tool 时不得强制 tool_choice。
5. 避免对 multi-agent 盲目透传本地 function tools，保留 search tools 优先。
6. 确保 build_console_responses_payload 原有 Codex context compaction 行为不被破坏。
7. 不改变模型列表。
8. 不泄露敏感日志。

验证：
1. python3 -m compileall app
2. 写一个小脚本直接调用 build_console_chat_payload，输入：
   - model: grok-4.3
   - messages: [{role:user, content:"hello"}]
   - tools: None
   确认不抛 NameError。
3. 输出 payload key summary。
4. git diff -- app/services/reverse/protocol/xai_console.py

完成后提交：
commit message: fix: stabilize console payload builder
```

## 验收标准

```text
python3 -m compileall app 通过
build_console_chat_payload 不再 NameError
不新增模型
不改变 Docker
```

---

# Goal 2：新增免费 console 模型与映射

## 目标

新增免费 console.x.ai 模型列表和映射，不迁移收费账号模型。

## 需要新增的模型

```text
grok-4.3-console
grok-4.3-low
grok-4.3-medium
grok-4.3-high
grok-4.20-0309-console
grok-4.20-0309-reasoning-console
grok-4.20-0309-non-reasoning-console
grok-4.20-multi-agent-console
grok-4.20-multi-agent-low
grok-4.20-multi-agent-medium
grok-4.20-multi-agent-high
grok-4.20-multi-agent-xhigh
grok-build-console
```

## 给 Codex 的 `/goal` 提示词

```text
/goal

目标：只新增免费 console.x.ai 模型与映射，不新增收费账号模型，不迁移 jiujiu 的完整架构。

工作目录：/home/ubuntu/grok2api
建议分支：feature/free-console-models，基于 integration/free-console-warp-202606 创建。

参考：
- /tmp/grok2api-ref/app/control/model/registry.py
- /tmp/grok2api-ref/app/dataplane/reverse/protocol/xai_console_chat.py
如果 /tmp/grok2api-ref 不存在，则 git clone --depth 1 https://github.com/jiujiu532/grok2api /tmp/grok2api-ref。

任务：
1. 优先新增独立模块 app/services/console_free/catalog.py，定义免费 console 模型 catalog。
2. catalog 至少包含：
   - public_model
   - upstream_model
   - fixed_reasoning_effort
   - include_reasoning
   - max_output_tokens
   - enable_search_tools
   - display_name
3. 在 app/services/grok/services/model.py 中接入这些模型，保留当前已有模型。
4. 不新增以下收费模型：
   - grok-4.20-auto
   - grok-4.20-expert
   - grok-4.20-heavy
   - grok-4.3-beta
   - super/heavy 0309 系列
5. 实现 fixed effort 优先级：
   fixed_reasoning_effort > request.reasoning_effort > console.default_effort > medium
6. public model 映射必须正确：
   - grok-4.20-multi-agent-xhigh -> grok-4.20-multi-agent-0309 + xhigh
   - grok-build-console -> grok-build-0.1
   - grok-4.3-* -> grok-4.3
7. 更新 /v1/models 输出，使这些模型在有可用 token 池时可见。
8. 如果当前模型可用性依赖 ssoBasic/ssoSuper，则免费 console 模型按 basic 候选池处理，不能要求 heavy/super。

验证：
1. python3 -m compileall app
2. 写脚本列出 ModelService.list()，确认新增 13 个模型。
3. 确认 grok-4.20-multi-agent-xhigh 的 upstream 是 grok-4.20-multi-agent-0309。
4. 确认 grok-build-console 的 upstream 是 grok-build-0.1。
5. git diff -- app/services/grok/services/model.py app/services/console_free/catalog.py

完成后提交：
commit message: feat: add free console model catalog
```

## 验收标准

```text
新增 13 个免费 console 模型
不新增收费账号模型
grok-4.20-multi-agent-xhigh 映射正确
```

---

# Goal 3：增强 console 专用 headers 和 clearance

## 目标

让 console.x.ai 请求使用专用 headers，不完全复用 grok.com app-chat headers。

## 给 Codex 的 `/goal` 提示词

```text
/goal

目标：新增 console.x.ai 专用 headers/cookie 构造，接入 ConsoleResponsesReverse，不改模型列表，不改 Docker。

工作目录：/home/ubuntu/grok2api
建议分支：feature/console-headers-clearance，基于 integration/free-console-warp-202606 创建。

参考：
- /tmp/grok2api-ref/app/dataplane/proxy/adapters/headers.py 的 build_console_headers

任务：
1. 在 app/services/reverse/utils/headers.py 新增 build_console_headers(token: str) -> dict。
2. build_console_headers 必须包含：
   - Authorization: Bearer anonymous
   - Cookie: sso=<token>; sso-rw=<token>; cf_clearance=<...>
   - Origin: https://console.x.ai
   - Referer: https://console.x.ai/
   - Content-Type: application/json
   - Accept: */* 或 text/event-stream，由调用方覆盖也可
   - User-Agent: get_config("proxy.user_agent")
   - x-cluster: https://us-east-1.api.x.ai
3. Cookie 中 cf_clearance 来源优先级：
   - proxy.cf_cookies 中的 cf_clearance
   - proxy.cf_clearance
   - proxy.clearance.cf_cookies / proxy.clearance.cf_clearance，如果 get_config 支持则读取
4. 不在日志输出完整 Cookie / token / cf_clearance。
5. 修改 app/services/reverse/console_responses.py，console 请求改用 build_console_headers。
6. 保持 app-chat / image / video 旧 build_headers 行为不变。
7. 如果需要读取嵌套配置，可增强 get_config 支持深层 dotted path，但必须保持旧 section.key 行为。

验证：
1. python3 -m compileall app
2. 写脚本调用 build_console_headers("test-token")，确认：
   - Authorization == Bearer anonymous
   - Cookie 包含 sso= 和 sso-rw=
   - 不打印完整 Cookie
3. git diff -- app/services/reverse/utils/headers.py app/services/reverse/console_responses.py app/core/config.py

完成后提交：
commit message: feat: add console-specific headers
```

## 验收标准

```text
console 请求使用专用 headers
日志不泄露敏感值
旧 grok.com headers 不被破坏
```

---

# Goal 4：新增 WARP + FlareSolverr 部署

## 目标

新增防封版部署，不破坏标准版 compose。

## 给 Codex 的 `/goal` 提示词

```text
/goal

目标：新增 WARP + Privoxy + FlareSolverr 防封部署配置，不改变标准版默认行为。

工作目录：/home/ubuntu/grok2api
建议分支：feature/warp-flaresolverr，基于 integration/free-console-warp-202606 创建。

参考：
- /tmp/grok2api-ref/docker-compose.warp.yml
- /tmp/grok2api-ref/scripts/init_proxy_config.py

任务：
1. 新增 docker-compose.warp.yml，包含：
   - init-config
   - warp-proxy
   - privoxy
   - flaresolverr
   - grok2api
2. grok2api 服务必须使用 build: .，不要使用远程 image，否则本地代码不生效。
3. 新增 scripts/init_proxy_config.py，用于启动前写入 data/config.toml 的代理配置。
4. init_proxy_config.py 必须：
   - 创建 data/config.toml 如果不存在
   - 如果已有配置，追加或更新 proxy 相关配置
   - 不覆盖 app.api_key/app.app_key/token 等已有配置
5. 配置写入建议兼容旧结构：
   [proxy]
   base_proxy_url = "http://privoxy:8118"
   asset_proxy_url = "http://privoxy:8118"
   enabled = true
   flaresolverr_url = "http://flaresolverr:8191"
   refresh_interval = 3600
   timeout = 60
6. 如果也写入新结构 [proxy.egress] / [proxy.clearance]，必须确认 app/core/config.py 支持读取。
7. 更新 README 或新增 docs/WARP_FLARESOLVERR.md，说明启动方式。

验证：
1. python3 -m compileall app
2. docker compose -f docker-compose.warp.yml config
3. docker compose -f docker-compose.warp.yml build
4. python3 scripts/init_proxy_config.py 在本地临时目录或受控 data 下测试，不得清空已有关键配置。

完成后提交：
commit message: feat: add warp flaresolverr compose
```

## 验收标准

```text
docker-compose.warp.yml 可 config/build
标准 docker-compose.yml 不被破坏
init_proxy_config.py 不覆盖敏感配置
```

---

# Goal 5：console 限流、错误分类与重试

## 目标

为免费 console 模型新增轻量独立限流和错误反馈，不重构整个 token manager。

## 给 Codex 的 `/goal` 提示词

```text
/goal

目标：新增免费 console 模型独立限流、冷却和错误分类，不重构付费账号体系。

工作目录：/home/ubuntu/grok2api
建议分支：feature/console-rate-limit，基于 integration/free-console-warp-202606 创建。

任务：
1. 新增配置：
   [console]
   enabled = true
   default_effort = "medium"
   rate_limit_count = 30
   rate_limit_window_sec = 900
   max_retry_tokens = 20
2. 新增 app/services/console_free/limits.py，实现轻量内存限流：
   - 按 token hash 统计 console 请求次数
   - 30 次 / 15 分钟窗口
   - 429 后 cooldown 到窗口结束
   - 进程重启后可重置，先不要求持久化
3. 新增 app/services/console_free/retry.py，分类：
   - 401 -> auth_failed
   - 403 -> clearance_or_proxy
   - 429 -> rate_limited
   - blank 400 -> rotate_token
   - 5xx -> transient
   - timeout -> transient
4. 接入 app/services/grok/services/console.py：
   - 请求前检查 console limit
   - 成功后记录一次 console 使用
   - 429 后标记 cooldown
   - blank 400/5xx/timeout 尝试换 token
5. 不破坏现有 token_mgr.consume 逻辑；如果已有 wrap_stream_with_usage 会消费 token，要避免重复计数导致不合理冷却。
6. 日志只记录 token hash。

验证：
1. python3 -m compileall app
2. 单元脚本模拟同一 token 31 次请求，第 31 次应触发 limited。
3. 模拟 429 后 token cooldown。
4. git diff -- app/services/console_free app/services/grok/services/console.py config.defaults.toml

完成后提交：
commit message: feat: add console rate limiting
```

## 验收标准

```text
console 免费模型有独立冷却
401/403/429/blank400/5xx 分类清晰
不泄露 token
```

---

# Goal 6：测试、验证脚本、文档和 WebUI

## 目标

补齐可维护性资产，让后续同步更安全。

## 给 Codex 的 `/goal` 提示词

```text
/goal

目标：为免费 console + WARP/FlareSolverr 增加测试、验证脚本、文档和基础 WebUI 模型展示优化。

工作目录：/home/ubuntu/grok2api
建议分支：feature/free-console-docs-webui，基于 integration/free-console-warp-202606 创建。

任务：
1. 新增或更新文档：
   - docs/MAINTENANCE_FREE_CONSOLE_WARP.md
   - docs/FREE_CONSOLE_WARP_GOAL_PLAN.md
   - docs/WARP_FLARESOLVERR.md
   - README.md 中增加免费 console 模型说明
2. 新增验证脚本 scripts/check_console_models.py，输出：
   - 所有免费 console 模型是否存在
   - public -> upstream 映射
   - fixed effort
   - max_output_tokens
3. 新增测试或最小脚本：
   - tests/test_console_free_catalog.py
   - tests/test_console_free_payload.py
   - tests/test_console_free_headers.py
   如果项目没有 pytest 配置，可先提供 scripts 形式。
4. WebUI 模型 fallback 列表增加免费 console 模型，并尽量分组显示：
   - Console
   - Multi-Agent
   - Build
   - Image
5. 增加日志脱敏检查说明。

验证：
1. python3 -m compileall app
2. python3 scripts/check_console_models.py
3. docker compose config
4. docker compose -f docker-compose.warp.yml config 如果文件存在
5. grep 检查 docs 中没有真实 token / api key / cf_clearance。

完成后提交：
commit message: docs: add free console maintenance plan
```

## 验收标准

```text
有维护文档
有 /goal 计划文档
有模型检查脚本
WebUI 能看到免费 console 模型
```

---

## 7. 最终集成 Goal

所有阶段完成后，运行最终集成。

```text
/goal

目标：将免费 console + WARP/FlareSolverr 各功能分支合并到 integration/free-console-warp-202606 并完成最终验证。

任务：
1. 检查以下分支是否已完成并有 commit：
   - fix/console-payload-stabilize
   - feature/free-console-models
   - feature/console-headers-clearance
   - feature/warp-flaresolverr
   - feature/console-rate-limit
   - feature/free-console-docs-webui
2. 逐个 merge --no-ff 到 integration/free-console-warp-202606。
3. 解决冲突时遵守：
   当前项目稳定运行 > 免费 console 功能正确 > 参考项目实现 > 收费账号相关变化。
4. 运行验证：
   - python3 -m compileall app
   - python3 scripts/check_console_models.py
   - docker compose config
   - docker compose build
   - docker compose -f docker-compose.warp.yml config
   - docker compose -f docker-compose.warp.yml build
5. 输出新增模型列表、修改文件列表、验证结果、已知风险。
6. 不自动合并 main，等待人工确认。
```

---

## 8. 最终验收清单

### 模型

必须存在：

```text
grok-4.20-multi-agent-xhigh
grok-4.20-multi-agent-high
grok-4.20-multi-agent-medium
grok-4.20-multi-agent-low
grok-4.3-high
grok-4.3-medium
grok-4.3-low
grok-build-console
```

### 映射

必须正确：

```text
grok-4.20-multi-agent-xhigh -> grok-4.20-multi-agent-0309 + xhigh
grok-build-console -> grok-build-0.1
grok-4.3-high -> grok-4.3 + high
```

### Payload

multi-agent xhigh 应包含：

```json
{
  "model": "grok-4.20-multi-agent-0309",
  "reasoning": {"effort": "xhigh"},
  "max_output_tokens": 2000000,
  "store": false
}
```

### Headers

console 请求应包含：

```text
Authorization: Bearer anonymous
Origin: https://console.x.ai
Referer: https://console.x.ai/
Cookie: sso=<redacted>; sso-rw=<redacted>; cf_clearance=<redacted>
```

### Docker

必须通过：

```bash
docker compose -f docker-compose.warp.yml config
```

### 日志安全

不得出现：

```text
sso=
sso-rw=
cf_clearance=
完整 Authorization
真实 API key
```

---

## 9. 风险清单

| 风险 | 说明 | 缓解 |
|---|---|---|
| 上游 console model 改名 | 例如 `grok-4.20-multi-agent-0309` 变化 | 通过 jiujiu diff 审计更新 catalog |
| reasoning 字段被部分模型拒绝 | 某些 grok-4.20 console 模型传 reasoning 会 400 | 只对白名单模型传 reasoning |
| WARP IP 被风控 | WARP 不是永久干净 | 支持 direct/manual/flaresolverr 三种模式 |
| FlareSolverr 获取的 UA 与请求 UA 不一致 | 会导致 clearance 失效 | 使用 FlareSolverr 返回 UA 覆盖配置 |
| 日志泄露 token | 安全风险 | 强制 redaction 和 grep 检查 |
| 当前容器仍使用远程 image | 本地改动不生效 | compose 改 build 或 warp compose 使用 build |
| 两个上游结构差异大 | 直接 merge 会冲突 | 只做语义移植，不整仓合并 |

---

## 10. 推荐执行方式

优先拆分执行：

```text
先 Goal 1 + Goal 2，让免费模型可见并可构造正确 payload
再 Goal 3，让 console header 正确
再 Goal 4，上 WARP + FlareSolverr
再 Goal 5，做限流和稳定性
最后 Goal 6，补文档/脚本/WebUI
```

不要一次性执行所有 Goal，避免冲突和不可控改动。
