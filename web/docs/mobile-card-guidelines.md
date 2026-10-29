# SoloBooks 移动端卡片设计与交互规范
# (Mobile Card Design Guidelines)

> **核心哲学**：克制、简约、高效、有质感。
> 面向 SoloBooks 移动端（H5 / 响应式 Web / 微信小程序 / UniApp）。
> 视觉与色彩真值统一遵循 [`DESIGN.md`](../../DESIGN.md) 与 `variables.css`。

---

## 一、核心原则（四大支柱）

| 原则 | 含义与落地表现 | 严厉禁止（Don'ts） |
|---|---|---|
| **克制** (Restrained) | **平面优先（Flat-at-Rest）**。静止状态依靠底层微色差（`#F6F7F9` 与 `#FFFFFF`）和 1px 薄雾边框定界，不靠浮夸投影。语义色仅用于核心数字与状态徽章。 | 严禁彩色大阴影、渐变炫光、毛玻璃叠层、霓虹描边。 |
| **简约** (Minimalist) | **分组容器胜过散装行卡**。长列表由一张外层大卡片承载，行间用 0.5px/1px 内嵌分割线（Divider Inset），视觉呼吸通透。 | 严禁卡片套卡片（Cards-in-Card）、列表每行都做独立漂浮卡。 |
| **高效** (Efficient) | **一眼判状态，单手定动作**。首屏强化大字金额与状态胶囊，手部易触达区常驻核心操作（44px 触控热区）；金额强制等宽对齐。 | 严禁无效装饰图表、冗长文字堆砌、非必要的多级页面跳转。 |
| **质感** (Tactile) | **精微的材质对比与触控韵律**。利用物理视网膜细线、微弱内嵌浅底（`#F1F4F8`）、微暗化按下态（Active State）营造真实稳固的物理手感。 | 严禁生硬的高饱和对比、粗制滥造的 3D 拟物、布局抖动式缩活动效。 |

---

## 二、基础物理参数与规则

### 1. 结构与尺寸规范

- **卡片圆角**：统一使用 **`12px`**（页面级独立卡片）。卡片内部嵌套的元数据标签/按钮采用 `4px` 或 `8px`，状态胶囊使用 `999px`。
- **页面水平边距（Gutter）**：屏幕两翼留白固定为 **`16px`**（小屏 375px）~ **`20px`**（大屏 ≥414px）。
- **卡片内边距（Padding）**：
  - 标准卡片：`16px`（上下）× `16px`（左右）；
  - 紧凑指标/辅助卡片：`12px` × `14px`；
  - 核心大看板：`20px` × `18px`。
- **卡片垂直间距（Stack Gap）**：卡片与卡片之间统一间距 **`12px`**（紧密关系）或 **`16px`**（大区块分割）。

### 2. 材质、背景与边框

- **页面底色**：`var(--solo-bg-page)`（`#F6F7F9`），提供温和清爽的承载底色，杜绝死白与冷灰。
- **卡片表面**：`var(--solo-bg-card)`（`#FFFFFF`）。
- **边框规范**：
  - 移动端卡片默认配备 **1px 细弱中性边框** `var(--solo-border-light)`（`#EDF0F4`）或在 iOS 高清屏启用物理 0.5px 边框；
  - 次级内嵌区（如说明、草稿行、折叠摘要）：使用 `var(--solo-bg-soft)`（`#F1F4F8`），无边框。
- **阴影纪律**：
  - 静止态：`box-shadow: none` 或极轻微贴面 `0 1px 2px rgba(24, 34, 48, 0.03)`；
  - 浮动层/底部抽屉：`0 8px 24px rgba(24, 34, 48, 0.08)`；
  - 触摸按下反馈（Active State）：背景切换为 `#F8FAFC` 或遮罩 `rgba(0, 0, 0, 0.03)`，**不使用缩小形变**。

### 3. 文字与数字排印

- **金额数字**：必须开启等宽数字，防止动态跳变：
  ```css
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
  ```
- **字号与阶梯**：
  - 看板大金额：`28px` / `32px`，字重 `700`，行高 `1.1`；
  - 列表行金额：`16px` / `17px`，字重 `650`；
  - 卡片主标题：`15px` / `16px`，字重 `600`，颜色 `var(--solo-text-primary)`（`#182230`）；
  - 辅助说明/时间：`12px` / `13px`，字重 `400`，颜色 `var(--solo-text-muted)`（`#636E7C`）。

---

## 三、四大核心卡片类型标准

### 1. 经营看板/指标卡（Metric Summary Card）

> **场景**：工作台顶部经营概览（净利润/收入/支出）、纳税申报期税额预测、资产总览。

- **布局结构**：
  ```
  +-------------------------------------------------------+
  |  [标签/标题: 本月净利润]          [时间片切换: 本月 v]  |
  |  ¥ 48,250.00                        [健康状态胶囊]   |
  |  ---------------------------------------------------  |
  |  收入 ¥82,000.00          支出 ¥33,750.00             |
  +-------------------------------------------------------+
  ```
- **交互与质感要点**：
  - 顶栏清晰呈现标题与辅助维度切换；
  - 核心大数字下方配以 1px 淡色细分割线，次级指标采用并排双列或三列网格（`display: flex; justify-content: space-between;`）；
  - 收入金额用 `var(--solo-income)`（`#0F7354`），支出用 `var(--solo-expense)`（`#BD3D3D`），大金额净结余保留墨石主色，不滥用彩色。

### 2. 聚合列表卡（Grouped List Card）

> **场景**：最近账目流水、发票列表、待办事项、银行对账差异项。

- **布局结构**：
  ```
  +-------------------------------------------------------+
  |  [卡片头部: 最近记账]                      [查看全部 >] |
  |  ---------------------------------------------------  |
  |  (图标)  阿里云计算技术有限公司服务费        - ¥1,200.00  |
  |          10月28日 · 研发支出 · 待补票         已过账     |
  |  ···················································  |
  |  (图标)  上海聚跃广告有限公司结算款        + ¥35,000.00  |
  |          10月27日 · 主营业务收入             已过账     |
  +-------------------------------------------------------+
  ```
- **交互与质感要点**：
  - **单卡多行**：禁止每一笔流水做一张独立圆角卡。统一包裹在一张白卡中；
  - **内缩进分割线（Divider Inset）**：行与行之间使用 `1px solid var(--solo-border-light)`，分割线左侧留出与图标对齐的内缩进（例如 `margin-left: 52px`），右侧平齐；
  - **点击区贯穿**：整行具备统一的 `48px` 最小点击热区，行尾预留微弱指示箭头或状态徽章。

### 3. 行动待办卡（Actionable Task Card）

> **场景**：今天要处理、结账阻断项、税务申报一键抄报、折旧确认。

- **布局结构**：
  ```
  +-------------------------------------------------------+
  |  [!] 增值税小规模纳税人季度申报            [距截止 3 天] |
  |      本期应税销售额 ¥24,500.00，免税额 ¥735.00          |
  |  ---------------------------------------------------  |
  |  [ 查看明细 ]                           [ 标记已申报 ] |
  +-------------------------------------------------------+
  ```
- **交互与质感要点**：
  - **左侧状态指示条（Accent Line）**：高优/紧急待办卡可在最左侧配以 3px 宽的语义警示条（如 `--solo-warning`）；
  - **底栏动作明确化**：底部提供明确的主副按钮，主按钮采用 `var(--solo-primary)`（高度 36px），次按钮为透明幽灵按钮，支持单手大拇指 1 次触击闭环。

### 4. AI 协同/建议卡（Explainable AI Insight Card）

> **场景**：智能记账建议、开票分类推荐、资产确认提议、结账合规诊断。

- **布局结构**：
  ```
  +-------------------------------------------------------+
  |  (AI星芒) 发现一笔待建档的设备采购流水                  |
  |  匹配到 10/24 购买「MacBook Pro」¥16,999.00，建议确    |
  |  认为固定资产并按 36 个月自动计提折旧。                 |
  |  [ 稍后处理 ]                           [ 确认生成卡片 ] |
  +-------------------------------------------------------+
  ```
- **交互与质感要点**：
  - **材质底色**：区别于普通白卡，AI 建议卡采用专属的柔和品牌浅蓝底 `var(--solo-bg-blue-soft)`（`#EDF3FF`），配以微蓝边界 `#D4E2FC`；
  - **言之有物**：严禁空洞的 AI 拟人化废话，直接给出「事实 + 依据 + 建议操作」；
  - **非阻塞**：始终具备「稍后 / 忽略」次动作，保证用户掌控权。

---

## 四、组件代码落地参考（Vue 3 / Tailwind 示例）

```html
<template>
  <!-- 聚合列表卡示例 -->
  <div class="solo-mobile-card">
    <!-- 卡片标头 -->
    <div class="card-header flex items-center justify-between pb-3 border-b border-[var(--solo-border-light)]">
      <span class="text-[15px] font-semibold text-[var(--solo-text-primary)]">待处理账目</span>
      <span class="text-[12px] text-[var(--solo-text-muted)] flex items-center gap-1">
        共 3 笔 <i class="ri-arrow-right-s-line" />
      </span>
    </div>

    <!-- 列表行 -->
    <div class="divide-y divide-[var(--solo-border-light)]">
      <div 
        v-for="item in items" 
        :key="item.id"
        class="py-3.5 flex items-center justify-between active:bg-[var(--solo-bg-soft)] transition-colors"
      >
        <div class="flex items-center gap-3 min-w-0 pr-3">
          <div class="w-9 h-9 rounded-lg bg-[var(--solo-bg-soft)] flex items-center justify-center shrink-0">
            <i :class="item.icon" class="text-[18px] text-[var(--solo-text-secondary)]" />
          </div>
          <div class="min-w-0">
            <div class="text-[14px] font-medium text-[var(--solo-text-primary)] truncate">{{ item.title }}</div>
            <div class="text-[12px] text-[var(--solo-text-muted)] mt-0.5">{{ item.subtext }}</div>
          </div>
        </div>

        <div class="text-right shrink-0">
          <div 
            class="text-[15px] font-semibold tabular-nums" 
            :class="item.amount > 0 ? 'text-[var(--solo-income)]' : 'text-[var(--solo-expense)]'"
          >
            {{ item.amount > 0 ? '+' : '' }}{{ item.amountText }}
          </div>
          <span class="inline-block mt-1 text-[11px] px-1.5 py-0.5 rounded-full bg-[var(--solo-bg-soft)] text-[var(--solo-text-secondary)]">
            {{ item.statusText }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.solo-mobile-card {
  background-color: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 12px;
  /* 平面优先：静止无重阴影，仅留微贴面 */
  box-shadow: 0 1px 2px rgba(24, 34, 48, 0.02);
}
</style>
```

---

## 五、自检核对清单（Mobile Checklist）

- [ ] **是否套娃**：卡片内部是否存在第二层带边框阴影的独立小卡片？（若是，改用淡灰背景块或分割线）
- [ ] **信息密度**：375px 屏宽下一屏能否有效浏览 3~4 个关键业务对象？
- [ ] **热区合规**：所有可点按元素的有效触控范围是否 ≥ 44px × 44px？
- [ ] **色彩克制**：除状态 Badge、收支红绿和主按钮外，卡片主体是否保持纯净的墨石黑白灰？
- [ ] **等宽数字**：金额是否均应用了 `tabular-nums`，且未出现小数位错位？
