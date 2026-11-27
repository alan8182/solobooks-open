# 微信开放平台网站应用扫码登录（运维手册）

> 适用范围：PC 浏览器「微信扫码登录」。小程序登录（`code2session`）与微信支付
> 是另外两套独立配置，排障时不要混看。

## 1. 开放平台配置

1. 微信开放平台 → 管理中心 → 网站应用「梭罗记账」：
   - AppID：`wx7a9a4c5aa9d3ecfe`
   - 授权回调域：`solobooks.cn`（不要带协议、路径、`www`）
   - 「生成」AppSecret 后立即填入服务器 `.env`，**只保存一次**，不要提交 git。
2. 网站应用 AppID 不能用于微信支付；支付继续用 `WECHAT_PAY_APP_ID`。

## 2. 环境变量（三处都要，缺一处链路就断）

| 位置 | 用途 |
| --- | --- |
| `backend/.env` | 本地开发 |
| `deploy/.env`（或 staging 对应文件） | Compose 变量来源 |
| `deploy/docker-compose.yml`、`deploy/docker-compose.app.yml` | 容器 `environment` 显式透传 |

```dotenv
WECHAT_WEB_APPID=wx7a9a4c5aa9d3ecfe
WECHAT_WEB_SECRET=<开放平台生成，仅存服务器>
```

派生配置（无需填写）：

- `WECHAT_WEB_REDIRECT_URI = ${APP_PUBLIC_URL}/api/auth/wechat/web/callback`
- state / ticket TTL 默认 600 秒，可用 `WECHAT_WEB_STATE_TTL_SECONDS`、`WECHAT_WEB_TICKET_TTL_SECONDS` 调整。

`APP_PUBLIC_URL` 必须是 `https://solobooks.cn`。`www.solobooks.cn` 已在宿主 nginx
301 到 apex，否则 OAuth 回调、Cookie 与弹窗 `postMessage` 会跨 origin 失效。

## 3. 部署步骤

1. 备份数据库。
2. 迁移（用属主账号，应用账号没有 ALTER 权限）：

```bash
DATABASE_URL="$MIGRATION_DATABASE_URL" venv/bin/python -m alembic upgrade head
```

迁移 `f2b6d8e4a9c1` 只加列/表/索引，不改历史数据；回滚：

```bash
DATABASE_URL="$MIGRATION_DATABASE_URL" venv/bin/python -m alembic downgrade e5f1a9c3b7d2
```

3. 写入环境变量后重建后端容器。
4. 验证 `GET /api/auth/wechat/web/config` 返回 `{"enabled": true}`。
5. 桌面浏览器走一遍：扫码 → 新用户绑手机 → 登录；再次扫码应免绑。

## 4. 链路与安全约束

```
登录页勾选协议 → /authorize-url 下发签名 HttpOnly state cookie（10 分钟）
   → tab 内 iframe 内嵌 open.weixin.qq.com/connect/qrconnect（不再弹窗）
   → 扫码后微信在 iframe 内跳 /api/auth/wechat/web/callback
       校验 state → code 换 openid/unionid
       已有手机号老账号 → login ticket
       新用户/未绑手机 → bind ticket
   → Set-Cookie wx_web_pending（HttpOnly，ticket 明文只在此处）
   → iframe 内 302 到前端 /wechat-login-callback
   → 回调页 postMessage ready/error 给登录页（window.parent）
   → 登录页 POST /exchange（login ticket → JWT；bind ticket → phone_required）
   → POST /bind-phone（短信通过 → 合并老账号或新建 → JWT）
```

- 回调页按上下文分流：有 opener 走弹窗路径（兼容保留）、无 opener 被内嵌走
  parent postMessage、整页访问自己 exchange；ticket 一次性、10 分钟、库里只存
  SHA-256；`login` / `bind` 用途不可串用。
- 二维码按需现做：勾选协议 / 切到 tab 才出码；超时或报错后点「刷新二维码」重新
  出码（旧 state 随之作废）。
- 短信失败或号码冲突会回滚，ticket 不烧掉，可换号重试。
- 绑定手机号时若号码已有账号：复用原账号与账套，保留套餐/点数；已绑其他微信则 409。
- 小程序 `openid` 与网站 `wechat_web_openid` 严格分开；小程序登录不会清空网站身份。

## 5. 常见错误码（`/login?wechat_error=...`）

| 错误码 | 含义 | 处理 |
| --- | --- | --- |
| `state_invalid` / `state_expired` | state cookie 缺失/被篡改/超时 | 重新扫码；检查是否多标签页并发 |
| `access_denied` | 用户在微信侧取消授权 | 重新扫码 |
| `code_invalid` | code 过期或已使用 | 重新扫码 |
| `config_missing` | 服务器未配置 AppID/AppSecret | 补 `.env` 并重启 |
| `identity_conflict` | 同一微信身份对应了两个账号 | 查库人工处理，不要自动搬移 |
| `account_disabled` | 老账号已禁用 | 联系管理员 |
| 绑定 409 | 手机号已绑其他微信，或微信已属于其他账号 | 换手机号/走人工解绑 |
| 会话已失效 | pending ticket 过期或已消费 | 重新扫码 |

## 6. 本地开发

微信无法回调 `localhost`。本地只跑单元测试（`tests/test_wechat_web_auth.py`，
微信 HTTP 与短信均 mock）；真实扫码只能在 staging/生产域名验证。
未配置 `WECHAT_WEB_SECRET` 时 `config` 返回 `enabled=false`，登录页不显示入口，
不影响现有手机号登录。

## 7. 回滚

- 关功能：清空 `WECHAT_WEB_APPID` / `WECHAT_WEB_SECRET` 并重启 → 入口消失，数据不动。
- 回滚 schema：见第 3 节 `alembic downgrade`（会删除 ticket 表与三列）。
- 审计：`auth_users.wechat_web_bound_at` 与登录审计事件保留合并时间与方法。
