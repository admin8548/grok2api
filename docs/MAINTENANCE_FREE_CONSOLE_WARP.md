# Free Console + WARP/FlareSolverr 后期维护文档（v2 / accounts.db）

> 当前维护基线：`origin/main` 的 v2 架构，账号体系使用 `data/accounts.db`。
> 旧源码 `token.json` 账号已合并进 `accounts.db`，不要再把 `token.json` 当作当前账号来源。

## 1. 当前状态

当前正确分支：

```text
integration/accountsdb-free-console-warp-202606
```

当前保留分支：

```text
本地：
- main
- integration/accountsdb-free-console-warp-202606

fork 远端：
- fork/main
- fork/integration/accountsdb-free-console-warp-202606
```

已清理的旧临时分支包括旧基线的 `feature/*`、`fix/*` 和错误集成分支
`integration/free-console-warp-202606`。后续不要再基于这些旧分支开发。

当前运行镜像：

```text
grok2api-local:accountsdb-free-console-warp
```

当前账号来源：

```text
data/accounts.db
```

账号迁移记录：

```text
迁移前 active: 1851
从旧 token.json 导入: 735
当前 active: 2586
```

旧 token 文件已归档：

```text
data/backups/token.json.migrated.20260607-195901.bak
```

## 2. 已集成模块

### Free Console 模型目录

```text
app/control/model/console_free.py
app/control/model/registry.py
```

已注册的 console-free public models：

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

### Console 请求适配

```text
app/dataplane/reverse/protocol/xai_console.py
app/products/openai/console_free.py
app/products/openai/chat.py
app/products/openai/responses.py
app/products/openai/router.py
app/dataplane/proxy/adapters/headers.py
```

Console-free 请求通过 v2 的 `accounts.db` 账号目录选择账号，使用
`reserve_any()` 选取 basic 账号，不再依赖旧版 `app/services/token`。

### WARP / Privoxy / FlareSolverr

```text
docker-compose.warp.yml
docker/privoxy-warp/Dockerfile
docker/privoxy-warp/config
scripts/init_proxy_config.py
docs/WARP_FLARESOLVERR.md
```

标准 `docker-compose.yml` 保持不变；WARP 栈只通过
`docker-compose.warp.yml` 可选启用。

## 3. 日常验证命令

### Python 编译

```bash
python3 -m compileall app
```

注意：v2 项目要求 Python 3.13。主机如果只有 Python 3.10，可用容器验证：

```bash
docker exec -i grok2api python -m compileall app
```

### 构建本地镜像

```bash
docker build -t grok2api-local:accountsdb-free-console-warp .
```

### 启动本地增强镜像

```bash
cat > /tmp/grok2api.accountsdb-local.override.yml <<'YAML'
services:
  grok2api:
    image: grok2api-local:accountsdb-free-console-warp
YAML

docker compose -f docker-compose.yml \
  -f /tmp/grok2api.accountsdb-local.override.yml \
  up -d --force-recreate grok2api
```

### 验证账号数

```bash
sqlite3 data/accounts.db \
  "SELECT pool,status,COUNT(*) FROM accounts WHERE deleted_at IS NULL GROUP BY pool,status;"
```

预期当前结果：

```text
basic|active|2586
```

### 验证模型列表

使用 `data/config.toml` 中的 API key 调用：

```bash
curl -H "Authorization: Bearer <API_KEY>" \
  http://127.0.0.1:8000/v1/models
```

当前 basic 池下预期：

```text
models_count=16
console_count=13
grok-4.3-console=true
```

## 4. WARP 启用流程

```bash
docker compose -f docker-compose.warp.yml config
docker compose -f docker-compose.warp.yml up -d --build
```

`init-config` 只更新以下配置：

```toml
[proxy.egress]
mode = "single_proxy"
proxy_url = "http://privoxy:8118"
resource_proxy_url = "http://privoxy:8118"

[proxy.clearance]
mode = "flaresolverr"
flaresolverr_url = "http://flaresolverr:8191"
```

不会覆盖：

```text
app.api_key
app.app_key
accounts.db
SSO token
cf_cookies / cf_clearance
```

## 5. Git 维护规则

后续维护只在一个正确集成分支上进行：

```text
integration/accountsdb-free-console-warp-202606
```

不再为每个小功能创建大量远端 feature 分支。只有出现长期、大范围、
高风险改动时，才临时创建本地分支；验证通过后合并回上述 integration 分支，
再删除临时分支。

推送命令：

```bash
sudo -H -u ubuntu bash -lc '
cd /home/ubuntu/grok2api
git push fork integration/accountsdb-free-console-warp-202606
'
```

不要把以下文件提交到 Git：

```text
data/
logs/
.env
任何包含 sso / sso-rw / cf_clearance / API key 的文件
```

## 6. 上游同步策略

同步主上游：

```bash
git fetch origin

git switch main
git merge origin/main

git switch integration/accountsdb-free-console-warp-202606
git merge main
```

重点检查冲突：

```text
app/control/model/registry.py
app/control/model/console_free.py
app/dataplane/reverse/protocol/xai_console.py
app/dataplane/proxy/adapters/headers.py
app/products/openai/console_free.py
app/products/openai/chat.py
app/products/openai/responses.py
app/products/openai/router.py
config.defaults.toml
docker-compose.warp.yml
scripts/init_proxy_config.py
```

同步后必须验证：

```bash
python3 -m compileall app
docker build -t grok2api-local:accountsdb-free-console-warp .
```

## 7. 回滚策略

### 仅回滚运行镜像

如果本地增强镜像异常，可切回官方 latest：

```bash
docker compose -f docker-compose.yml up -d --force-recreate grok2api
```

### 回滚账号迁移

迁移前备份：

```text
data/backups/accounts.db.pre-token-merge.20260607-195431.bak
```

如必须回滚账号库：

```bash
cp data/backups/accounts.db.pre-token-merge.20260607-195431.bak data/accounts.db
docker restart grok2api
```

正常情况下不要回滚账号库；当前 2586 账号已在 v2 runtime 中验证可加载。
