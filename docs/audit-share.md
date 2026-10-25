# Solo Audit Share 协审链接 · 运行手册

> 对应规划：《Solo Audit Share 协审链接落地计划.md》（商业计划书 §3.4/§5.1/§7.3）。
> 一句话：老板一键生成免注册只读链接，外部 CPA 打开即可查看当月账套体检快照并批注，批注回流老板待办处理。

## 能力边界（先读这个）

- **非代理记账**：CPA 视图全程展示边界声明；批注是咨询参考，不构成记账指令；"查看凭证"只做跳转，采纳建议后的调整仍走草稿-确认流程。
- **只读是结构性的**：CPA 侧代码 `backend/services/audit_share_public.py` 只触碰 `audit_shares` / `share_annotations` 两张表，禁止 import 记账写路径——`test_audit_share.py::test_public_module_imports_no_write_paths` 用 import 静态断言守住这条线，改动该文件前先看测试。
- **快照式**：链接内容是生成时刻的冻结脱敏数据集。老板改账后旧链接内容不变，需重新生成。
- **明文 token 只出现一次**：库里只存 sha256；管理页再次进入拿不到旧链接，丢失只能重新生成（与 MCP 令牌同款约定）。

## 数据流

```
结账向导/我的页面 ──生成──▶ audit_shares(snapshot=冻结脱敏JSON, token_hash)
     │                              │
     │ 复制 /share/<token> 私发 CPA  │（免注册，凭 token 哈希定位）
     ▼                              ▼
  CPA 浏览器打开 ────── GET /api/share/{token} ──▶ 只读快照 + 计数
     │ 批注（唯一写操作，仅写 share_annotations）
     ▼
  老板端「我的 → 专家协审」批注收件箱：已解决 / 已知悉 / 忽略（留痕）
```

## 关键文件

| 层 | 文件 | 说明 |
|---|---|---|
| 表 | `backend/db/models.py` `AuditShare` / `ShareAnnotation` | 启动时 create_all 自动建表；带 company_id，公司删除自动连带清理 |
| 老板侧服务 | `backend/services/audit_share_service.py` | 快照构建（复用 period_close_checks / todo_service / get_difference_report）、脱敏器、令牌签发、entitlement 挂点 |
| CPA 侧服务 | `backend/services/audit_share_public.py` | token 校验、只读视图、批注（含每日 50 条上限与每分钟限流） |
| 路由 | `backend/routers/audit_share.py` | 老板侧 `/api/audit-shares*`（require_auth）；CPA 侧 `/api/share/{token}`（免注册） |
| 前端 | `web/src/pages/share/index.vue` | CPA 体检视图（总览→异常→勾稽→余额表→总体意见） |
| 前端 | `web/src/pages/mine/audit-share.vue` | 老板管理页（生成/预览/撤销/批注处理） |
| API 封装 | `web/src/api/auditShare.ts` | 含类型定义 |
| 测试 | `backend/tests/test_audit_share.py` + `web/src/__tests__/auditShare.test.ts` | 全链路 + 只读边界静态断言 |

## 运维要点

- **SQLite 锁纪律**（本功能踩过的坑，写新代码必须遵守）：`log_action` / 任何裸连接写操作**不得在 `session_scope` 事务内调用**，否则忙等 30 秒。留痕一律放事务提交之后。裸层表（bank_statements 等）的 ensure 也要在事务前跑（见 `_prepare_raw_schemas`）。
- **失效语义**：404=链接不存在；410=已撤销/已过期（body 带 reason）；429=限流。撤销即时生效。
- **限流**：每 token 每分钟 60 次（进程内存桶，多进程部署需换 Redis）；批注每日 50 条、单条 ≤500 字。
- **脱敏口径**：往来单位/人名→首字+`**`；摘要中的手机号/银行卡/身份证→正则打码；金额与公司主体名不脱敏（体检需要）。生成前有预览。
- **商业化挂点**：`audit_share_service.require_entitlement()` 目前恒真；Solo Pro 上线后在此接 license 判定即可，数据形状不动。

## 与商业计划书承诺的对应

| BP 承诺 | 落点 |
|---|---|
| 免注册只读批注链接 | `/share/:token` 公开路由 + token 哈希定位 |
| 一键脱敏 | 生成开关 + sanitize_snapshot + 预览 |
| 五项检查报告与异常分录 | 快照 checks（含试算平衡）+ anomalies（复用待办推导规则） |
| CPA 单户体检 ≤15 分钟（待实测） | 五段预消化动线 + view_count/last_viewed_at 留痕；第一户实测数据回来后迭代 |
