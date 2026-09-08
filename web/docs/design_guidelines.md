# SoloBooks 系统设计规范与前端开发指南

本规范旨在定义 SoloBooks 全站页面的 UI 布局、容器宽度、排版字号、组件风格与视觉标准。为了给用户提供一致、高级、极具质感的 SaaS 财务管理体验，所有页面（包括新开发页面）必须遵循此开发规范。

---

## 1. 核心设计原则

*   **宽度绝对统一**：全站所有页面最大宽度硬性统一为 `920px`，使导航时页面标题、正文内容的左右边缘严密贴合在同一垂直线上。
*   **字号层级规范**：严格限制标题大小，全站大标题字号必须高度统一，副标题清晰置灰。
*   **极光玻璃态美学**：卡片与面板使用圆角毛玻璃微发光设计，搭配柔和过渡。

---

## 2. 页面容器宽度规范 (Container Widths)

系统通过全局样式表 [layout.css](file:///Users/ecarx/Documents/solobooks/web/src/assets/styles/layout.css) 对主内容区域的路由视图 `.content > *` 进行了强覆盖：
*   **全站统一宽度**：全站所有页面的最大宽度统一设定为 `920px`。
*   **对齐与居中**：使用 `margin: 0 auto` 实现自动水平居中，并设定统一的顶部内边距 `padding-top: 16px`。
*   **用户体验效益**：标题位置和内容宽度 100% 相同。当用户在不同菜单（如账本、对账、报表、个人中心等）之间进行切换时，页面标题与主内容边缘完全重合，不会产生左右忽宽忽窄或标题忽上忽下的跳动感。

---

## 3. 页面头部与标题规范 (Headers & Typography)

为保障每个界面的大标题大小与外边距达到全局绝对统一，系统在全局进行了规则覆盖：

### 3.1 页面头部包裹器 (Header Wrapper)
*   **CSS 类**：`.page-header` 或 `.report-header`。
*   **布局逻辑**：使用 Flex 横向布局，两端对齐，垂直居中对齐，下方统一 `margin-bottom: 24px` 的呼吸感间距。

### 3.2 页面大标题 (Page Title)
*   **标签要求**：必须在头部中使用 `h2` 标签。
*   **统一字体**：
    *   **字号**：`22px`
    *   **字重**：`700`
    *   **颜色**：`var(--solo-text-primary)`（Slate 深灰）
    *   **排版**：行高 `1.3`，字距缩紧 `letter-spacing: -0.5px`。

### 3.3 页面副标题 (Page Subtitle)
*   **CSS 类**：`.subtitle`、`.report-subtitle` 或 `.text-muted`。
*   **统一字体**：字号 `13px`，字重 `normal`，颜色 `var(--solo-text-secondary)`（次要 Slate 灰）。

### 3.4 示例 HTML 结构：
```html
<div class="ledger-page"> <!-- 自动适配 920px 宽度 -->
  <div class="page-header">
    <div class="title-section">
      <h2>账本</h2> <!-- 自动适配 22px 加粗大标题 -->
      <p class="subtitle">记录企业日常经营的所有资金往来流向</p> <!-- 自动适配 13px 副标题 -->
    </div>
    <NSpace>
      <NButton type="primary" size="small">新增记录</NButton>
    </NSpace>
  </div>
  ...
</div>
```

---

## 4. 玻璃态卡片组件规范 (Card Wrappers)

为了让数据容器产生通透、现代的视觉效果，全站卡片容器统一采用高斯模糊毛玻璃微光样式：
*   **类名**：`.report-card` 配合 Naive UI `<NCard :bordered="false">` 使用。
*   **卡片内小标题**：使用 `h3` 标签并赋予 `.report-section-title` 类，字号统一为 `14.5px`，字重 `750`。

```html
<NCard :bordered="false" class="report-card mb-4">
  <h3 class="report-section-title">
    <i class="ri-line-chart-line"></i> 月度趋势分析
  </h3>
  <!-- 卡片图表或列表内容 -->
</NCard>
```

---

## 5. 财务数据表格与对齐规范 (Data Tables)

表格排版必须遵循严谨的财务对齐原则：

*   **数值/金额列**：文字必须右对齐（`text-align: right`），使用等宽字体（`Outfit` 或 `Inter` monospaced）并附加 `.amount-cell` 类。
*   **科目代码列**：固定宽度并使用 `.code-cell` 类，展示为等宽字符。
*   **合计/总计行**：在行上添加 `.total-row` 类，将自动添加 2px 上边框并赋予淡底色高亮。
*   **利润与损失高亮**：收入/盈利数据附加 `.income`（绿色），支出/负债数据附加 `.expense`（红色）。

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

---

## 6. 月度结账向导交互设计规范 (Closing Wizard & Canvas)

月度结账向导（Monthly Closing Wizard）代表了系统级核心引导流程的视觉标准，其交互规范如下：

### 6.1 分步流程进度条 (Steps Layout)
*   **进度展示**：使用 Naive UI 的 `<NSteps>` 组件进行顶层展示，统一放入 `.steps-card` 中以确保呼吸感间距。
*   **卡片内边距**：容器内边距设定为 `16px 24px`，确保进度指示器的文字和点不拥挤。

### 6.2 拖拽上传区域 (Drag & Drop Zone)
*   **CSS 类**：`.uploader-zone`。
*   **视觉规范**：
    *   背景采用微透明的 `var(--solo-bg-input)`，配合虚线边框（Dashed border）。
    *   鼠标悬停（`:hover`）时，边框颜色渐变为 `var(--solo-primary)`，背景微光发亮，并伴随 `0.2s` 的缩放缓动。
    *   文字结构包含：大图标、引导标题、次要副标题说明。

### 6.3 智能对账画布工作区 (Match Canvas)
对账工作台使用三栏式网格架构（发票 - 已配对 - 银行流水），使用 `.canvas-layout` 进行排列：
*   **分栏容器** (`.canvas-column`)：
    *   三栏等宽，使用 `flex: 1` 均分。
    *   中间“已配对账单”列使用柔和绿底淡框高亮展示（`rgba(16, 185, 129, 0.02)` 背景，`rgba(16, 185, 129, 0.15)` 边框）。
*   **对账选择卡片** (`.canvas-card`)：
    *   两端对齐，标题文字居左，金额居右，并根据金额正负自动附带 `.income` 或 `.expense` 颜色。
    *   被选中时（`.selected`），边框变为 `var(--solo-primary)`，并施加蓝色阴影（Box Shadow）以确保强交互提示。
*   **匹配对账卡** (`.matched-pair-card`)：
    *   上部包含 `confidence-badge`（置信度标签：99%、98% 或 手动匹配），下方纵向展示配对的发票名称与流水账单。
    *   尾部提供「拆开」与「确认匹配」的双动作微型按钮。

### 6.4 CFO 诊断与仪式感关账 (Diagnosis & Dashboard)
*   **关账状态容器** (`.closing-ceremony-modal`)：垂直居中对齐，大图标使用光晕背景包裹，文字限制最大宽度为 `480px` 以保证居中阅读体验。
*   **核心指标行** (`.metrics-row`)：
    *   包含收入、成本、净利润三项看板块 (`.metric-box`)。
    *   采用毛玻璃卡片设计，内部金额以大字号（`20px`）和粗体展现，直观明了。
*   **AI CFO 诊断气泡**：
    *   带左侧粗边框的浅色毛玻璃卡片（`border-left: 4px solid var(--solo-primary)`）。
    *   以极其丰富的排版（结合 Emoji 表情符号、小警告图标和小标题高亮）突出财务健康和税务合规风险。
```
