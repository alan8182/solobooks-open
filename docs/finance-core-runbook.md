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

**当前部署实际状态（2026-11-19 核对）**：服务器 cron `30 3 * * *` 正常产出
`deploy/backups/solobooks-*.dump.age`（age 加密 + sha256 校验，本地保留 35 天），
`deploy.sh --verify` 会在产物超过 26 小时时告警。加密文件随后上传到腾讯云 COS
（`COS_BACKUP_BUCKET`，前缀 `backups/`），并下载回读比对 sha256；上传失败会让本次
备份以非零退出，不会静默丢异地副本。子账号只有 Put/Get/Head 权限，没有删除权限，
对象过期由桶的生命周期规则处理。

WAL 归档已于 2026-11-19 启用，与逻辑备份并行：

- `docker-compose.yml` 的 `archive_command` 把 WAL 段写入命名卷
  `postgres_wal_archive`，先写 `.partial` 再原子改名，避免读到半个文件；
- root cron 每分钟运行 `wal_archive_ship.py`，把归档段上传到 `backups/wal/`，
  HEAD 比对大小 + MD5 一致后才删除本地副本；上传失败会保留本地文件并告警；
- 每天 04:10 运行 `base-backup-postgres.sh`：`pg_basebackup` 在线导出 →
  zstd → age → `backups/base/`，首份备份已用 `pg_verifybackup` 验证清单；
- 本地归档卷只作中转，正常情况下应接近空；磁盘剩余不足 2GiB 时上传脚本会报错。

实际 RPO 约 **5–6 分钟**（`archive_timeout=300` + 每分钟上传）。这是在 cron 正常
运行前提下的部署状态；完整 PITR 恢复演练通过前，对外仍按更保守的口径说明。

**COS 桶配置（2026-11-19 控制台确认）**：

- 版本控制：开启（开启后只能暂停，不能回到“未启用”）；
- 生命周期规则 `solobooks`：开启，应用范围 `backups/`；
  - 当前版本：文件修改后 35 天删除（与本地保留期一致）；
  - 历史版本：文件修改后 7 天删除（只用于误覆盖/误删后的短窗口恢复）；
  - 未完成分片：创建后 7 天删除。
- 该规则覆盖整个 `backups/`，因此同时管理 `backups/wal/` 与 `backups/base/`；
  WAL 保留期与基础备份一致，恢复窗口覆盖最近一份基础备份。
- 生命周期删除由 COS 服务执行，不受子账号没有 `DeleteObject` 权限的影响。

WAL 归档参数：超时 300 秒、每分钟上传，目标 RPO 5 分钟。备份文件、基础备份和 WAL
归档均复制到异机对象存储，并由生命周期规则控制保留期。

异地副本自检（只输出对象键与校验结果，不打印密钥）：

```sh
cd deploy
/opt/solobooks/.cos-venv/bin/python cos_backup.py head backups/<文件名>.dump.age
/opt/solobooks/.cos-venv/bin/python cos_backup.py verify backups/<文件名>.dump.age backups/<文件名>.dump.age
/opt/solobooks/.cos-venv/bin/python cos_backup.py delete-denied backups/<文件名>.dump.age
```

最后一条必须返回 `"delete": "denied"`；若返回 `ALLOWED`，说明子账号策略过宽，立即收权。
子账号策略模板见 `deploy/cos-cam-policy.json`（仅 Put/Get/Head + 列桶，无删除）。

WAL 上传自检（状态文件里的 `errors` 必须为空，`checkedAt` 应在一分钟内）：

```sh
cd deploy
/opt/solobooks/.cos-venv/bin/python wal_archive_ship.py
cat backups/wal-ship-status.json
/opt/solobooks/.cos-venv/bin/python cos_backup.py ls backups/wal/
```

基础备份自检：

```sh
cd deploy
./base-backup-postgres.sh
/opt/solobooks/.cos-venv/bin/python cos_backup.py ls backups/base/
```

### 时间点恢复（PITR）

基础备份 + WAL 才能恢复到任意时间点。恢复必须在隔离主机进行：

1. 取最近的 `backups/base/solobooks-base-*.tar.zst.age`，用离线 age 私钥解密；
2. 解压到空的 PGDATA 目录，并用 `pg_verifybackup` 校验清单；
3. 从 COS `backups/wal/` 下载该基础备份起点之后的 WAL 段和 `.history`；
4. 创建 `recovery.signal`，设置 `restore_command` 指向本地 WAL 目录；精确演练优先
   在生产先建 `pg_create_restore_point('drill-...')`，恢复时用 `recovery_target_name`，
   按时间恢复则用 `recovery_target_time`；
5. 启动临时 PostgreSQL 实例，确认恢复到目标时间且数据一致，再决定是否切换。

**2026-11-19 首次 PITR 演练记录**：基础备份
`solobooks-base-20261119T124127Z.tar.zst.age`（起点 `4/F5`，12:41:28 UTC），
WAL 回放至 `4/FD`，恢复到 restore point `drill-20261119T1250Z`（12:50:50 UTC）；
隔离容器启动后约 1 秒完成回放并 promote（timeline 2），`pg_is_in_recovery()=f`，
`journal_entries=583`、`invoices=189`，迁移版本 `b4e1a7c9d2f3`，与生产一致。
演练使用同主机独立容器，未暴露端口；age 私钥经 stdin 注入 `/dev/shm`，结束后立即
删除。下一次季度演练应在独立主机重复。

不要在生产 PGDATA 上演练，恢复目标必须与生产隔离。

每月在隔离主机执行恢复演练：

```sh
cd deploy
AGE_IDENTITY_FILE=/secure/age-key.txt ./restore-drill.sh backups/solobooks-....dump.age
```

记录开始/结束时间、恢复行数、校验结果和负责人。目标 RTO 不超过 60 分钟。脚本会恢复到临时数据库并在结束后删除，不得把演练目标指向生产库。逻辑恢复演练之外，每季度至少做一次「基础备份 + WAL」的 PITR 演练。

### 主体永久删除（不可变触发器豁免）

已过账凭证与审计事件的数据库触发器只允许**数据库属主**在会话级
`solobooks.allow_tenant_purge=on` 时删除（迁移 `e7c1a4d9f2b8`）。应用角色
即使自己设置该 GUC 也会被拒绝，因此删除主体必须通过专用属主连接：

- 环境变量 `TENANT_PURGE_DATABASE_URL`（未配置时回落到 `MIGRATION_DATABASE_URL`）；
- 未配置时接口失败关闭并返回明确错误，绝不回落到应用角色；
- 删除入口还要求 owner 身份 + 精确主体名称 + `delete_company` 短信二次认证。

### 电子账簿与归档件

签发正式账簿时，系统会在同一个签发动作里把带封面/目录/书签的 PDF 落盘到
`backend/data/archives/`，并把 `pdf_path`、`pdf_sha256`、`pdf_size_bytes` 写进
`ledger_book_snapshots`。生产 compose 用命名卷 `backend_archives` 挂载该目录：

- **不要删除或重建该卷**：卷没了，`ledger_book_snapshots`/`backup_records` 记录还在，
  但历史归档下载会变成 410。
- 卷需要与 PostgreSQL 备份一起异地复制；恢复演练时同时核对：
  1. `scripts/diagnose_ledger_book_pdf.py` 输出 0 条问题；
  2. 随机抽一份已签发账簿下载，`X-Book-Pdf-Sha256` 与库内 `pdf_sha256` 一致。
- 归档下载会在服务端重算文件 sha256，并通过 `X-Backup-Sha256` 回显；签发件与
  `pdf_sha256` 不一致时直接返回 409，不把损坏文件交给用户。
- 归档目录只增不减；确需清理时先排除 `ledger_book_snapshots.pdf_path` 引用的文件
  （签发件长期保留），只清理 `backup_records` 中已过保留期且未被引用的导出件。
- 本功能上线前的历史册没有 PDF 归档件：下载时只读现渲染，**不会**自动写库。
  补齐必须显式执行：

```sh
cd backend
venv/bin/python scripts/diagnose_ledger_book_pdf.py --company c_xxx
venv/bin/python scripts/repair_ledger_book_pdf.py --company c_xxx            # dry-run
venv/bin/python scripts/repair_ledger_book_pdf.py --company c_xxx --apply    # 确认后
```

修复脚本幂等、打印每册「改前 → 改后」并写 `repair_ledger_book_pdf` 审计事件。
回滚：删除本次新生成的文件，把对应四列置空，或恢复执行前的数据库与归档目录备份。

### 发票电子原件（attachments）

发票原件（数电票 XML/PDF/OFD）上传后落盘到 `backend/data/attachments/`，生产 compose
用命名卷 `backend_attachments` 挂载该目录（`SOLOBOOKS_ATTACHMENTS_DIR` 可覆盖）：

- **原件不可变**：入库后不更新内容；作废只置 `status='void'` + 写审计事件，
  文件保留不删除——目录只增不减，与归档卷同规则。
- **不要删除或重建该卷**：卷没了，附件行还在但下载会 410（文件缺失）。
- 下载在服务端重算文件 sha256 并通过 `X-Attachment-Sha256` 回显；与库内
  `content_sha256` 不一致直接 409（对应「原件被篡改/损坏」场景，从备份恢复）。
- 卷需要与 PostgreSQL 备份一起异地复制；附件表（`attachments`）随库备份。
- 回滚：`alembic downgrade` 到 `b8e2d4c6a1f3` 之前会 drop 表与列，磁盘文件保留；
  附件功能对凭证零写入，回滚不影响账务。

### 凭证册装订（book_kind='voucher'，Phase 2）

凭证册复用 `ledger_book_snapshots` 表（`book_kind='voucher'`，迁移
`e9b3d7f2c5a8` 放宽 CHECK）：签发即定版、册号按 (公司, kind, 期间) 独立递增、
作废留痕不删册、PDF 落盘 `backend/data/archives/`（与账簿册同卷同规则）。

- 签发前提：期间已关账（`PERIOD_NOT_CLOSED` 422）；只装订已过账凭证
  （posted/reversed），草稿不入册；单册上限 200 张（超限 413）。
- 「附单据 X 张」来自真实附件聚合（`invoices` → active `attachments`），
  不再由 billingStatus 推导——修数请补附件，不要改展示。
- 重签发语义与账簿一致：反结账补记后重新签发产生新册新号，旧册照查；
  同内容预览下载优先复用已签发归档件。
- 回滚：`alembic downgrade` 到 `e9b3d7f2c5a8` 之前，若存在 voucher 册
  会**直接拒绝**（防止静默删用户定版册，A2）——先逐册作废并导出 PDF 后再回滚。
- 冻结 payload 重渲染按 kind 分发（voucher 册用凭证册渲染器），改
  `voucher_book_pdf_service.py` 的版式不影响已签发册的逐字节复现
  （payload 驱动 + invariant=1）。

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

## 发生额与报表取数口径

余额与发生额是两套口径，消费方必须对齐，不得各写一套：

- **余额**：全口径净额。已过账（posted）与已冲销（reversed）凭证全部参与，冲销对借贷自相抵，净额恒正确。
- **聚合发生额**（月度汇总 `get_balance_summary`、账簿册 `build_ledger_book` 余额表/明细账合计/总账、结账检查「账账核对」）：**剔除同期间冲销对**——被冲销原凭证与其冲销凭证落在同一 `period_start` 时两张都不计入；跨期冲销保留（是更正当期的真实动作）。共享实现在 `services/reversal_pairs.py::same_period_reversal_exclusions`，两边由 parity 测试锁定一致。
- **行级账簿**（明细账/序时账/日记账的逐凭证行）：完整留痕，不剔除。因此冲销重记当月，明细行合计会大于聚合发生额，这是口径设计而不是错账。
- **报表发生额**（申报财务报表/利润表，`tax_filing_export_service`）：期初凭证（`entryType=opening`）与其冲销凭证都不进发生额，期初数由「期初余额映射」代替；YTD 过渡凭证只计「已过账」的，且利润表本年累计按**方向毛额**读取（收入取贷方累计、费用取借方累计——原账本每月结转损益时损益科目净额为 0）。资产负债表权益（`profit_ytd_net`）保持净额口径，避免与用户期初挂账的 3103/3104 重复计入。
- 冲销凭证的识别：metadata `reversalOf`（dict）/ `reversal_of_id` 列（ORM）；期初凭证 id 前缀 `opening-{company_id}-{year}[-gN][-ytd]`。

所有人工调整必须写明原因和请求 ID，审计事件不得更新或删除。
