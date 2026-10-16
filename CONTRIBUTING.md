# 贡献指南

感谢你愿意为 SoloBooks 贡献！请遵循以下规范。

## ⚠️ 授权提醒

本报遵循**源码可用许可**（见 [LICENSE](LICENSE)：内部记账自托管免费，对外商用需授权）。你提交的任何代码、文档，将被视为同意以相同许可证分发。提交内容中**不得包含**任何密钥、密码、Token、真实业务数据或版权材料。

## 提交 Issue

- 先搜索是否已有相同 Issue
- 描述问题：复现步骤、期望与实际行为、环境（OS / Python / Node 版本）
- 涉及记账/金额的 Bug，请附上具体的输入与期望结果

## 提交 PR

1. Fork 并创建功能分支，命名建议：`feat/xxx`、`fix/xxx`、`docs/xxx`
2. 编码前运行既有测试，确保基于最新 `main`
3. 遵循现有代码风格（后端 Python / 前端 Vue3+TS）
4. **禁止提交敏感信息**（见上）；`backend/.env`、`*.key`、日志、业务数据已在 `.gitignore`
5. 提交信息用简洁中文描述改动意图
6. 涉及数据库改动时，附上对应的 Alembic 迁移
7. 通过 CI（pytest + vue-tsc）后发起 PR

## 本地验证

```bash
# 后端测试
cd backend && pytest

# 前端类型检查 + 测试
cd web && npm run build && npm run test
```

## 代码规范要点

- **金额**：始终使用两位小数精度处理，禁止浮点直接累计（可参考 `backend/core/money.py`）
- **分类与方向**：新增账目分类时，需同步 `backend/services/category_service.py`、`backend/services/account_mapper.py` 与前端 `web/src/utils/constants.ts`
- **数据库**：所有 schema 变更走 Alembic 迁移，不直接改表
