# SoloBooks 开发更新流程

当前阶段是开发优化阶段。PostgreSQL 保持在 Docker 中运行；修改代码后只重建发生变化的服务。

## 热更新模式

开发时可停止 Docker 的 backend/frontend，仅保留 PostgreSQL：

```powershell
$env:PATH="$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin;$env:PATH"
docker compose --env-file deploy/.env.staging.local `
  -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml `
  -p solobooks-dev stop backend frontend
```

然后分别启动本机服务：

```powershell
cd backend
$env:DATABASE_URL='postgresql+asyncpg://solobooks_app:<本地应用密码>@127.0.0.1:55432/solobooks'
Remove-Item Env:ACCOUNTING_TEST_MODE -ErrorAction SilentlyContinue
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

另开一个 PowerShell：

```powershell
cd web
npm run dev -- --host 127.0.0.1 --port 5173
```

热更新访问地址为 `http://127.0.0.1:5173/`；Vite 会把 `/api` 代理到 `http://127.0.0.1:8001`。修改 Python/Vue 文件后会自动重载，不需要重新构建 Docker 镜像。

本机 PostgreSQL 已导入原 SQLite 测试数据，因此测试账户会看到原有公司和凭证。

### 本地库保持与迁移同步（必做）

应用代码假定本地库已跑齐 Alembic 迁移——运行期 `_ensure_schema` 的补列逻辑按 SQLite
写就，PostgreSQL 缺列时不会自动补齐，会直接全局 500（2026-11 曾因本地库漂移 4 个迁移
导致所有接口 500）。拉取到含新迁移的代码后，在 `backend` 目录执行：

```powershell
.\venv\Scripts\python.exe scripts\migrate_local.py          # upgrade head
.\venv\Scripts\python.exe scripts\migrate_local.py current  # 查看当前版本
```

脚本强制使用 `backend/.env` 里的 `MIGRATION_DATABASE_URL`（`solobooks_owner` 专用
连接串，占位见 `.env.example`，与 `solobooks_app` 密码不同），缺配置时直接报错，
不会静默连到 alembic.ini 的 SQLite 副本。**勿用 `python -m alembic` 裸命令跑本地库**
——不带 DATABASE_URL 时它连的是 SQLite，这正是当初漂移没被发现的原因。

已执行过的迁移不要重复执行；本地调试回退用 `migrate_local.py downgrade -1`；
生产/演练环境迁移仍走 `deploy/deploy.sh`（backend 容器启动时自动执行）与
`docs/finance-core-runbook.md` 的流程。

## 生产部署

统一用 `deploy/deploy.sh`，不要手动在服务器上 build（小内存机禁止并行构建）：

```bash
bash deploy/preflight.sh            # 部署前门禁：后端 pytest + 前端 vitest/类型检查/构建/预渲染（也可单独当全量回归）
bash deploy/deploy.sh --check       # 演练：全部检查 + rsync 预览，不改服务器
bash deploy/deploy.sh               # 正式部署：要求工作区已提交；自动做部署前快照与回滚锚点
bash deploy/deploy.sh --verify      # 线上只读核验
bash deploy/deploy.sh --rollback    # 镜像切回上一版（不回滚数据库）
```

部署遇到的问题按编号（D-xxx）记录在 solobooks-deploy skill 的 `DEPLOY-LOG.md`，脚本注释引用同一编号。

## 推荐命令

在项目根目录 PowerShell 执行：

```powershell
.\deploy\update-dev.ps1 -Service backend
```

只修改 Web：

```powershell
.\deploy\update-dev.ps1 -Service frontend
```

后端和 Web 都修改：

```powershell
.\deploy\update-dev.ps1 -Service all
```

脚本会自动：

1. 保持/启动 PostgreSQL；
2. 构建指定镜像；
3. 重启指定服务；
4. 输出 Compose 健康状态。

## 手动命令

```powershell
$env:PATH="$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin;$env:PATH"

docker compose `
  --env-file deploy/.env.staging.local `
  -f deploy/docker-compose.yml `
  -f deploy/docker-compose.local.yml `
  -p solobooks-dev build backend frontend

docker compose `
  --env-file deploy/.env.staging.local `
  -f deploy/docker-compose.yml `
  -f deploy/docker-compose.local.yml `
  -p solobooks-dev up -d backend frontend
```

## 数据库变更

Alembic 迁移由 backend 容器启动命令自动执行。只修改 Python/Web 代码不需要重建 PostgreSQL，也不会删除 PostgreSQL volume。

查看状态：

```powershell
docker compose `
  --env-file deploy/.env.staging.local `
  -f deploy/docker-compose.yml `
  -f deploy/docker-compose.local.yml `
  -p solobooks-dev ps
```

## 主体删除的 PG 验证

`journal_entries` / `journal_lines` / `audit_events` 有不可变触发器，会无条件拒绝 DELETE。
「永久删除主体」依赖迁移 `c1d3e5f7a9b2` 引入的会话级开关
`solobooks.allow_tenant_purge` 才能删干净，而 SQLite 没有这些触发器，**只能在 PG 上验证**。

部署后（或改过相关触发器 / 删除逻辑后）跑一次：

```bash
cd backend
set -a && . ../deploy/.env.staging.local && set +a
DATABASE_URL="postgresql+asyncpg://${POSTGRES_APP_USER}:${POSTGRES_APP_PASSWORD}@127.0.0.1:55432/${POSTGRES_DB}" \
  venv/Scripts/python.exe scripts/verify_tenant_purge_pg.py
```

脚本只操作自带前缀的临时主体（`c_purge_probe_*` / `u_purge_probe_*`），不触碰既有数据。
它断言两类行为同时成立：正常业务路径仍拒绝删除已过账凭证与审计事件；
清理事务能删干净、清空悬空引用、写入平台留痕，且开关不会泄漏到后续会话。
退出码 0 表示全部通过。
