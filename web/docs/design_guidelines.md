# SoloBooks 实现规范与前端开发指南

> **设计语言看 [`DESIGN.md`](../../DESIGN.md)**（色彩意图、字体层级、Do's and Don'ts）。
> 本文只管「落到代码上怎么摆」，面向新增页面和改动现有页面。
> 两者冲突时以 `DESIGN.md` 的意图 + `variables.css` 的真值为准，本文的类名与宽度以代码实际引用为准。
>
> 最后核对：2026-10-20（对应设计评估整改批次，见 `design-audit/web-2026-10-20/`）

---

## 1. 新增一个页面：照着这个顺序做

1. **路由与导航**：在 `src/router/index.ts` 注册，在 `utils/constants.ts` 的 `NAV_ITEMS` 挂一项。
   **必须同时给 `group`（会计视图）和 `bossGroup`（老板视图）**——老板视图是默认视图，
   只挂 `group` 等于只有切到会计视图才能进。低频页挂到「专项核算」或「设置」的子项。
2. **容器宽度**：按 §2 选档，写在页面根元素的 class 上。
3. **页面头部**：`.page-header` + `h2` 标题 + `.subtitle`，见 §3。
4. **状态与空态**：徽章用 `SoloStatusBadge`（正常态 `plain`），空态用 `SoloEmpty`（guide / hint），
   加载首屏用 `SoloSkeleton`。规则见 `DESIGN.md` §7.2–7.4。
5. **长列表**：超过一屏的流水按日分组 + 组头吸顶，用 `utils/entryGrouping.ts`。
6. **自检**：`npx vitest run`、`npx vue-tsc --noEmit -p tsconfig.app.json`、
   `npm run check:palette`，再加 §7 的验收口径。

---

## 2. 容器宽度分层

外层上限由 `layout.css` 的 `.content > *` 统一给到 `max-width: 1440px` 并居中；
页面再按内容密度收窄。**不要再用「全站统一 920px」**——宽表在那套规则下会横向拥挤、
折行断流。

| 档位 | 宽度 | 适用 |
| --- | --- | --- |
| 宽表 | 1200–1240 | 报表中心与 12 张子报表（`.report-container` / `.report-page`）、银行对账（`.bank-page`）、发票导入（`.invoice-import-page`）、经营账本与研发台账（`.ledger-page` / `.rd-page`）、薪酬 / 期初 / 会计科目（`.salary-page` / `.opening-page` / `.accounts-page`）、税务申报（`.tax-filing-page`）、固定资产与无形资产 |
| 标准 | 1080–1200 | 大多数列表与看板 |
| 聚焦 | 640–720 | 单据详情、凭证打印预览、新增主体、设置类表单 |
| 步骤向导 | 920 → 1240 | 月度结账：Step 1–3 保持 920 引导，Step 4–5 对账核验自动展宽（`.closing-wizard--wide`） |

宽表卡片需配 `overflow-x: auto`，并在 `≤768px` 收紧间距，避免小屏撑破主布局。

---

## 3. 页面头部与标题

### 3.1 头部包裹器

`.page-header` 或 `.report-header`：Flex 两端对齐、底边对齐，`margin-bottom: 24px`。

### 3.2 页面大标题

- 标签：`h2`（统一由 `layout.css` 接管，**不要在页面里覆盖字号**）
- 字号 `24px`，字重 `650`，行高 `1.3`，`letter-spacing: -0.5px`
- 颜色 `var(--solo-text-primary)`

### 3.3 副标题

`.subtitle` / `.report-subtitle`：`13px`、`normal`、`var(--solo-text-secondary)`。

> 注意与 `--solo-text-muted` 的区别：`secondary` 用于「副标题、说明」，`muted` 用于
> 「日期、元数据、时间戳」这类更弱的辅助信息。两者都在浅底上过 AA，别混用层级。

### 3.4 结构示例

```html
<div class="ledger-page">           <!-- 宽度档位见 §2 -->
  <div class="page-header">
    <div class="title-section">
      <h2>账本</h2>
      <p class="subtitle">记录企业日常经营的所有资金往来流向</p>
    </div>
    <NSpace>
      <NButton type="primary" size="small">新增记录</NButton>
    </NSpace>
  </div>
  ...
</div>
```

---

## 4. 卡片与容器

**平面优先。** 静止表面靠背景差、1px 边框和间距分层，不靠毛玻璃、发光或大面积阴影。
（这一条取代了本文旧版「高斯模糊毛玻璃微光」的说法，与 `DESIGN.md` 的
The Flat-at-Rest Rule / The No Glass Rule 一致。）

- **类名**：`.report-card` 搭配 `<NCard :bordered="false">`。
- **圆角**：全站四档——内嵌小块 4px、控件与卡片 8px、页面级大容器 12px、胶囊 999px。
- **背景**：默认 `--solo-bg-card`；次级区块用 `--solo-bg-soft`；品牌弱的强调用 `--solo-bg-blue-soft`。
- **边框**：`--solo-border-light` 或 `--solo-border`。**禁止彩色粗侧边条**（>1px 的
  `border-left` 强调），需要强调用背景色或左侧 2px 以内的中性线。
- **卡片内小标题**：`h3` + `.report-section-title`，`14.5px` / `750`。
- **阴影**：静止不用；悬停 `--solo-shadow`，浮层 `--solo-shadow-lg`。

```html
<NCard :bordered="false" class="report-card mb-4">
  <h3 class="report-section-title">
    <i class="ri-line-chart-line"></i> 月度趋势分析
  </h3>
</NCard>
```

> naive-ui 的内容层类名是 `.n-card-content`（**无下划线**）。要在卡片内部做
> flex/grid 布局，必须 `:deep(.n-card-content)`；写成 `.n-card__content` 是静默失配，
> 不报错也不生效。

---

## 5. 财务数据表格

- **金额列**：右对齐 + 等宽数字（`.amount-cell`），并加 `font-variant-numeric: tabular-nums`。
- **科目代码列**：固定宽度、等宽字体（`.code-cell`）。
- **合计行**：`.total-row`，2px 上边框 + 淡底。
- **收支高亮**：`.income`（绿）/ `.expense`（红）取 token，不写裸 hex。
- **长表**：可排序的表格用「日期列 `fixed: 'left'` + 表格 `max-height` 让表头吸顶」；
  不可排序的流水用按日分组 + 组头吸顶。两者不要混用，理由见 `DESIGN.md` §7.6。

```html
<div class="report-table-wrapper">
  <table class="report-table">
    <thead>
      <tr>
        <th class="code-col">科目代码</th>
        <th>科目名称</th>
        <th style="text-align: right;">期末余额</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td class="code-cell">1002</td>
        <td>银行存款</td>
        <td class="amount-cell income">84,520.00</td>
      </tr>
    </tbody>
    <tfoot>
      <tr class="total-row">
        <td colspan="2">资产总计</td>
        <td class="amount-cell income">84,520.00</td>
      </tr>
    </tfoot>
  </table>
</div>
```

---

## 6. 特殊流程

### 6.1 月度结账向导

- 顶层进度用 `<NSteps>`，放进 `.steps-card`，内边距 `16px 24px`。
- 步骤条的「未到达」态已在 `App.vue` 的 `Steps` 主题里抬到可读灰——不要再用
  naive 默认（`#c2c2c2`，1.78:1）。
- 银行流水在 `?step=4`。

### 6.2 拖拽上传区

- `.uploader-zone`：`--solo-bg-soft` 底 + 虚线边框。
- 悬停时边框转 `--solo-primary`，`0.2s` 过渡；不做缩放动效（避免布局抖动）。
- 结构：大图标 + 引导标题 + 次要说明。

### 6.3 智能对账画布

三栏网格（发票 / 已配对 / 银行流水），`.canvas-layout` + `.canvas-column`：

- 三栏等宽 `flex: 1`；中间「已配对」列用 `--solo-income-bg` 淡底 + `--solo-income` 细边框区分。
- `.canvas-card`：两端对齐，金额左标题右数值，正负分别用 `.income` / `.expense`。
- 选中态 `.selected`：边框转 `--solo-primary`，加 `--solo-primary-glow` 焦点环。
- `.matched-pair-card` 上部的 `confidence-badge` 用 `SoloStatusBadge`
  （置信度正常 → `plain neutral`，需要人工确认 → `warning`）。

### 6.4 关账确认

- `.closing-ceremony-modal` 垂直居中，正文限宽 `480px`。
- `.metrics-row` 三项看板（收入 / 成本 / 净利润）：平面卡片 + 1px 边框，
  金额 `20px` 粗体、等宽数字。**不用毛玻璃**。
- AI 诊断块：普通浅底卡片，左侧强调用 `--solo-info` 的 2px 内线或直接靠标题层级，
  不用 4px 彩色粗边、不用 Emoji 表情符号。

---

## 7. 验收口径

改完不要只看截图，跑这三条 + 两条实测：

```bash
cd web
npx vitest run                                   # 单测
npx vue-tsc --noEmit -p tsconfig.app.json        # 类型
npm run check:design                             # 色板棘轮 + 文档与 token 一致性
```

`check:design` = `check:palette`（非 token hex 只许降）+ `check:docs`
（`DESIGN.md` / `DESIGN_SYSTEM.md` 的色值必须与 `variables.css` 一致）。

实测两条：

1. **对比度**：新增/改动的文字节点按 WCAG 2.1 AA 自查，且要按**最暗的实际背景**算，
   不是白底。项目里已有一套页面级审计脚本的写法（遍历文本节点 + 逐级向上合成背景），
   需要时重建即可。
2. **吸顶**：有 sticky 元素时，滚动容器后读 `getBoundingClientRect().top` 是否恒定。
   只确认「有 `overflow: auto`」是不够的——祖先不真的滚动时 sticky 完全不激活。

更多踩过的坑见 `DESIGN.md` §7.8 反模式清单。
