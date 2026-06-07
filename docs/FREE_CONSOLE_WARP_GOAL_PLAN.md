# Free Console + WARP/FlareSolverr 后续改进计划（v2 / accounts.db）

> 本计划用于后续继续迭代。当前功能已集成到
> `integration/accountsdb-free-console-warp-202606`，不再使用旧版
> `integration/free-console-warp-202606` 或旧 `feature/*` 分支。

## 0. 当前已完成

```text
[完成] v2 accounts.db 基线确认
[完成] 旧 token.json 账号合并到 accounts.db
[完成] free-console 模型目录
[完成] console.x.ai /v1/responses 分流
[完成] console headers
[完成] WARP + Privoxy + FlareSolverr 可选 compose
[完成] Git 分支整理，只保留 main + 正确 integration 分支
```

当前验证状态：

```text
运行镜像: grok2api-local:accountsdb-free-console-warp
账号来源: data/accounts.db
active账号: 2586
/v1/models: 200
console模型: 13
```

## 1. 后续维护原则

1. 以 v2 架构为主线，不回退到旧 `token.json` 架构。
2. 账号只看 `data/accounts.db`。
3. `data/token.json` 已归档，不能再作为运行数据源。
4. Git 只保留一个正确集成分支：

```text
integration/accountsdb-free-console-warp-202606
```

5. 标准 `docker-compose.yml` 不加入 WARP 依赖；WARP 只通过
   `docker-compose.warp.yml` 可选启用。

## 2. 建议后续 Goal

### Goal A：console-free 实际请求矩阵验证

目标：验证每类 console-free 模型的真实请求效果。

模型矩阵：

```text
grok-4.3-console
grok-4.3-low
grok-4.3-medium
grok-4.3-high
grok-4.20-multi-agent-xhigh
grok-build-console
```

验证接口：

```text
/v1/chat/completions
/v1/responses
```

验证项：

```text
非流式返回
流式返回
reasoning_effort 映射
工具调用字段
搜索来源字段
401/403/429/5xx 重试
账号 release 是否正常
```

完成后记录：

```text
docs/CONSOLE_FREE_VALIDATION.md
```

### Goal B：console-free 错误分类优化

目标：进一步细化 console 上游错误分类。

当前已有基础：

```text
app/products/openai/console_free.py
```

后续可拆分：

```text
app/products/openai/console_free.py        # 入口/编排
app/dataplane/reverse/protocol/xai_console.py
app/dataplane/reverse/transport/console.py # 可选拆出 transport
```

重点分类：

```text
401 -> UNAUTHORIZED / expire
402/429 -> RATE_LIMITED / local cooldown
403 -> FORBIDDEN / clearance or account issue
5xx/timeout -> SERVER_ERROR / retry another account
```

### Goal C：WARP 栈实测

目标：验证完整 WARP/Privoxy/FlareSolverr 栈。

命令：

```bash
docker compose -f docker-compose.warp.yml up -d --build
curl -x http://127.0.0.1:40080 https://ifconfig.me
```

验证项：

```text
privoxy 可用
warp-proxy 出口正常
flaresolverr 可访问
init-config 未覆盖敏感配置
proxy.egress.mode=single_proxy
proxy.clearance.mode=flaresolverr
```

### Goal D：上游同步检查

目标：当 `origin/main` 更新时，检查 v2 热点文件是否冲突。

命令：

```bash
git fetch origin
git switch integration/accountsdb-free-console-warp-202606
git merge origin/main
python3 -m compileall app
docker build -t grok2api-local:accountsdb-free-console-warp .
```

重点文件：

```text
app/control/model/registry.py
app/control/model/console_free.py
app/products/openai/console_free.py
app/dataplane/reverse/protocol/xai_console.py
app/dataplane/proxy/adapters/headers.py
config.defaults.toml
```

## 3. 不再执行的旧计划

以下旧计划已废弃：

```text
旧 integration/free-console-warp-202606
旧 feature/free-console-models
旧 feature/warp-flaresolverr
旧 feature/console-rate-limit
旧 fix/console-payload-stabilize
旧 token.json 账号体系
旧 app/services/* 代码体系
```

如果需要查看旧实现，只能通过历史 commit 查阅，不要恢复为主线。

## 4. 最小验收清单

每次修改后至少执行：

```bash
git status --short --branch
python3 -m compileall app
docker build -t grok2api-local:accountsdb-free-console-warp .
docker compose -f docker-compose.yml -f /tmp/grok2api.accountsdb-local.override.yml up -d --force-recreate grok2api
```

然后确认：

```text
容器 healthy
account_count=2586 或更高
/v1/models 200
grok-4.3-console 可见
```
