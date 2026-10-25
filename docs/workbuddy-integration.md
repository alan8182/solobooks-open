# 梭罗记账 WorkBuddy 连接器（Connector）接入指南

本文档指导如何将 **梭罗记账（SoloBooks）** 接入 **腾讯 WorkBuddy** 平台，使 WorkBuddy AI 助手获得专业的财税核算、结账体检、对账及报税准备能力。

> **核心原则：AI 起草、人工确认**
> 财务账套是严肃资产。通过 WorkBuddy 连接器调用的写入操作（如凭证起草、对账匹配提议）**绝不直接过账**，而是以待办草稿形式推送到梭罗记账产品内。最终入账必须由经营者在 Web 工作台亲自确认。

---

## 一、 获取访问令牌 (PAT)

每个接入 WorkBuddy 的用户或账套，均需要一个专用的个人访问令牌：

1. 登录梭罗记账 Web 端（如 `https://solobooks.cn` 或本地部署实例）；
2. 点击右上角/侧边栏进入 **「我的」 → 「AI 连接（MCP）」**；
3. 点击 **「新建令牌」**：
   - 名称建议：`WorkBuddy 桌面端`
   - 权限选择：`读写草稿`（允许查询 + 起草凭证建议）或 `只读`（仅允许查询报表流水）
   - 有效期：按需选择（默认 90 天或长期）
4. **立即复制弹出的明文令牌**（以 `sbk_` 开头），该令牌只在创建时展示一次。

---

## 二、 接入方式

### 方式一：个人桌面端快速接入（开发者 / 私有化实例）

直接通过 WorkBuddy 本地配置文件注册自定义连接器：

1. 打开用户根目录下的 WorkBuddy 配置文件：
   - **macOS / Linux**：`~/.workbuddy/mcp.json`
   - **Windows**：`C:\Users\<用户名>\.workbuddy\mcp.json`
   *(若文件不存在请手动创建；注意文件名是 `mcp.json`，文件夹是 `.workbuddy`)*

2. 在 `mcpServers` 对象中增加 `solobooks` 配置（参考 `deploy/workbuddy/mcp.json.template`）：

```json
{
  "mcpServers": {
    "solobooks": {
      "url": "https://solobooks.cn/mcp",
      "headers": {
        "Authorization": "Bearer sbk_替换为你的PAT令牌"
      }
    }
  }
}
```

3. **在客户端点击「信任」并验证连接状态**：
   - 打开 WorkBuddy 客户端，进入左侧导航栏的 **「连接器」** 页面；
   - 切换到 **「自定义连接器」** 标签，找到 `solobooks`；
   - 点击 **「信任」** 按钮激活连接器，状态变为 **「已连接」（绿色状态点）**；
   - *(注：若令牌过期或无效，WorkBuddy 会在执行任务时提示并在输入框弹出「重新连接」引导卡片，更新令牌即可恢复)*。

4. **在对话任务中启用与测试**：
   - 新建对话，在输入框左下角点击 `+` > **连接器** 确保勾选了 `solobooks`；
   - 输入：“帮我查一下上个月的收支汇总”，即可验证连接。

---

### 方式二：开放平台发布（上架 WorkBuddy 应用/连接器市场）

如果你需要将 SoloBooks 作为标准连接器上架至 **WorkBuddy 开放平台**（`open.workbuddy.cn`）供企业或公众用户安装：

1. 登录 [WorkBuddy 开放平台控制台](https://open.workbuddy.cn)；
2. 进入 **资产管理 → 连接器 → 创建连接器**；
3. 参照本仓库提供的元数据定义文件 [`deploy/workbuddy/connector-meta.json`](file:///Users/ecarx/Documents/solobooks/deploy/workbuddy/connector-meta.json) 填写基础资料（或直接导入资料包）：
   - **标识符 (source)**：`solobooks-accounting`
   - **中文名称 (name_zh)**：`梭罗记账`
   - **英文名称 (name_en)**：`SoloBooks`
   - **中英文描述**：`面向小微企业与一人公司的 AI 数字会计连接器...`
   - **中英文示例 (examples_zh / examples_en)**：预置 4 组中英文自然语言使用提示词
   - **图标 (icon)**：根目录提供 `icon.png`
   - **通信协议**：`MCP (Model Context Protocol)`
   - **服务地址**：`https://solobooks.cn/mcp`
   - **鉴权类型**：`Bearer Token`（提示用户输入 `sbk_...` 格式个人令牌）
4. 勾选关联技能：导入本仓库 [`skills/solobooks-tax-filing`](file:///Users/ecarx/Documents/solobooks/skills/solobooks-tax-filing)（半自动纳税申报伴侣）；
5. 提交平台审核，审核通过后状态变为“已启用”。

---

## 三、 连通性自检

在正式接入前，可使用仓库内置的轻量探测脚本验证你的服务是否兼容 WorkBuddy 连接器调用规范：

```bash
# 1. 验证未鉴权拦截（安全边界）与 tools 广播
python scripts/verify_workbuddy_mcp.py --url https://solobooks.cn/mcp --token sbk_你的令牌
```

预期输出：
```text
==> 正在检测 WorkBuddy MCP 端点: https://.../mcp
[PASS] 1/3 安全边界测试通过：未带 Token 请求正确返回 HTTP 401
[PASS] 2/3 MCP 握手测试通过：成功获取工具列表 (共广播 24 个工具)
[PASS] 3/3 核心工具完备性检查通过：关键工具均就绪
🎉 SoloBooks MCP 连接器自检全部通过，已完全就绪接入 WorkBuddy！
```

---

## 四、 典型会话指令集

连接器启用后，用户可以在 WorkBuddy 中直接通过自然语言驱动财税作业：

| 业务场景 | 典型指令示例 | 调用的底层 MCP 工具 |
| :--- | :--- | :--- |
| **经营查账** | “帮我看看上个月总共赚了多少钱，利润率怎样？” | `get_month_summary` |
| **未对账流水排查** | “看一下银行账单里还有哪几笔流水没对上？” | `list_unmatched` |
| **结账合规体检** | “运行一下 9 月份的结账五项检查，看看能不能关账。” | `run_close_checks` |
| **发票与账龄查询** | “查一下最近有没有未开票的支出，以及应收款账龄。” | `list_invoice_entries`、`get_aging` |
| **起草记账凭证** | “今天微信扫码支付了 128 元办公文具费，帮我记一笔。” | `propose_voucher` *(生成草稿，待用户确认)* |
| **纳税申报准备** | “准备本季度的增值税申报表，看看要交多少税。” | `generate_filing_package` + `solobooks-tax-filing` |

---

## 五、 部署与网络排错

WorkBuddy 作为桌面客户端与云端服务交互，需注意以下网络要点：

1. **反向代理长连接配置 (Nginx)**：
   MCP Streamable HTTP 协议涉及长时间挂起的连接与流式响应，Nginx 必须关闭代理缓冲并放宽超时：
   ```nginx
   location /mcp {
       proxy_pass http://backend:8001;
       proxy_set_header Host $host;
       proxy_buffering off;             # 必须关闭缓冲，确保 SSE/流式即时返回
       proxy_read_timeout 600s;         # 放宽超时到 10 分钟
   }
   ```
2. **跨域与 HTTPS**：
   生产环境必须开启标准 HTTPS。WorkBuddy 客户端禁止访问混合内容的未加密 HTTP 端点。
