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

本机 PostgreSQL 已导入原 SQLite 测试数据，因此测试账户会看到原有公司和凭证；不要再对本机库重复执行正式迁移，除非先恢复备份或重建专用测试数据库。

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
