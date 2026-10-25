# SoloBooks 可靠财务核心运行手册

## 上线阻断条件

以下任一项未满足时不得开放写入：

- 微信支付私钥已在微信商户平台完成轮换，旧私钥已吊销。
- Git 托管平台确认历史中的 `private.*.key` 已清除，并强制所有使用者重新拉取仓库。
- 真实迁移报告 `passed=true`，`exceptions=[]`，`accountDifferences=[]`。
- PostgreSQL 集成测试、Web 回归、微信小程序回归和恢复演练全部通过。
- 缺少分录的旧记录 `bank-bs-c_41e4385e891e-1783656499924120-2`（0.15 元）已由负责人书面决定科目，或明确批准为非账务归档记录。

## 已泄露微信私钥处置

仓库已忽略并移除工作树中的 `*.key`，但这不能替代平台轮换和历史清理。

1. 在微信商户平台生成新私钥/证书，更新部署密钥，验证支付与回调。
2. 吊销旧私钥。不要把新私钥复制到仓库；只放入部署平台的 Secret 管理。
3. 在隔离维护分支使用 `git-filter-repo` 清理全部历史，然后通知所有协作者重新克隆：

   ```sh
   git filter-repo --path-glob 'private.*.key' --invert-paths
   git push --force --all
   git push --force --tags
   ```

4. 在 Git 托管平台执行 Secret 扫描，确认旧内容不可检索。历史重写是团队级破坏性操作，必须由仓库管理员协调执行。

## 环境和角色

部署必须设置：

- `POSTGRES_PASSWORD`：迁移所有者密码，仅 Alembic 使用。
- `POSTGRES_APP_PASSWORD`：应用角色密码，后端日常运行使用。
- `BACKUP_ENCRYPTION_RECIPIENT`：`age` 公钥，用于每日备份。

应用使用 `solobooks_app`，不能绕开数据库不可变触发器。Alembic 使用 `solobooks_owner`。两类密码不得相同。

## 迁移演练

先启动 PostgreSQL，再只读演练：

```sh
docker compose -f deploy/docker-compose.yml up -d postgres
docker compose -f deploy/docker-compose.yml run --rm backend sh -c \
  'DATABASE_URL="$MIGRATION_DATABASE_URL" alembic upgrade head'
docker compose -f deploy/docker-compose.yml run --rm backend python \
  scripts/migrate_sqlite_to_postgres.py \
  --source /migration-source/app.db \
  --database-url "$MIGRATION_DATABASE_URL" \
  --report /migration-reports/rehearsal.json \
  --dry-run
```

迁移源必须以只读方式挂载。解决文件只允许两种显式决定：提供完整平衡的 `voucherLines`，或带书面原因的 `{"action":"archive"}`。不得由脚本猜测科目。

## 维护窗口切换

1. Web、微信小程序和后台任务进入维护模式，确认没有写请求。
2. 复制 SQLite，记录哈希并保存到只读介质：

   ```sh
   cp backend/data/app.db backups/app-cutover.db
   sha256sum backups/app-cutover.db > backups/app-cutover.db.sha256
   ```

3. 部署 PostgreSQL 16，执行 Alembic。
4. 使用已审批的解决文件执行正式迁移（不要加 `--dry-run`）。
5. 检查报告的源 SHA-256 与第 2 步一致；检查所有差异为 `0.00`。
6. 运行后端测试、API 冒烟、Web 和微信小程序关键流程。
7. 同一发布批次切换后端、Web 和小程序，最后开放写入。

开放写入前任何一步失败：停止新容器，恢复旧后端镜像和第 2 步 SQLite 副本。开放写入后不得回写 SQLite；只能在 PostgreSQL 上修复或从 PostgreSQL 备份恢复。

## 备份与恢复

每日计划任务执行：

```sh
cd deploy
BACKUP_ENCRYPTION_RECIPIENT='age1...' ./backup-postgres.sh
```

WAL 归档超时为 300 秒，目标 RPO 为 5 分钟。备份文件和 WAL 归档必须复制到异机对象存储，并设置不可变保留策略。

每月在隔离主机执行恢复演练：

```sh
cd deploy
AGE_IDENTITY_FILE=/secure/age-key.txt ./restore-drill.sh backups/solobooks-....dump.age
```

记录开始/结束时间、恢复行数、校验结果和负责人。目标 RTO 不超过 60 分钟。脚本会恢复到临时数据库并在结束后删除，不得把演练目标指向生产库。

## 日常异常处理

- `PERIOD_CLOSED`：先判断该月是否已对外报送。
  - 未报送 → 由 owner 短信二次认证后反结账，完成调整后重新关账；
  - 已报送 → **不要改原月**，改走「补记」：录入时确认 `allowBackfill` 并填写原因，凭证记入最早的未关账期间，摘要带「补记YYYY-MM｜」前缀，台账 `period_backfills` 留痕（依据《小企业会计准则》第 88 条未来适用法）。
  - 跨年一律不做补记：由财税人员按以前年度应扣未扣支出专项申报（2012 年 15 号公告，追补期 5 年），系统只导出清单。
  - 任何情况下不得直接改库，也不得修改原始单据日期来规避期间校验。
- `ENTRY_POSTED_IMMUTABLE`：生成等额反向凭证，再创建正确凭证。
- `UNBALANCED_ENTRY`：检查每条分录均为正数金额，借贷合计必须精确相等。
- `DUPLICATE_SOURCE`：核对来源外部键和幂等键，不得删除已有凭证后重试。
- `REVIEW_REQUIRED`：maker-checker 模式由不同人员复核。
- `STEP_UP_REQUIRED`：重新发送短信验证码；token 单次使用、限定公司和动作、5 分钟有效。

所有人工调整必须写明原因和请求 ID，审计事件不得更新或删除。
