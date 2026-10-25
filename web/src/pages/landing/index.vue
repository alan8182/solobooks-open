<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton } from 'naive-ui'

// ICP 备案号，页脚自动呈现并链接工信部网站
const ICP_LICENSE = '浙ICP备2025195764号-7'
// 公安备案号，链接公安部备案查询网站
const PSB_RECORD = '浙公网安备 33011002020894号'
const PSB_RECORD_URL = 'https://beian.mps.gov.cn/#/query/webSearch?code=33011002020894'

const router = useRouter()

// ── PPT 式整页翻页：桌面滚轮由 JS 接管 ──
// Chromium 对 CSS scroll-snap 的补位动画近似瞬移（实测 1024px 约 50ms 走完九成），
// 滚轮翻页改走平滑滚动才有过渡过程；scroll-snap（proximity）兜底触屏/键盘/滚动条。
const NAV_HEIGHT = 56 // 与 CSS scroll-padding-top 保持一致
const FLIP_LOCK_MS = 750 // 大于一次平滑翻页实测时长（约 620ms），锁内吞掉后续滚轮
let flipLocked = false
let flipLockTimer: ReturnType<typeof setTimeout> | undefined

function slideEls(): HTMLElement[] {
  return Array.from(
    document.querySelectorAll<HTMLElement>(
      '.landing-page .hero, .landing-page .section, .landing-page .landing-slide-end',
    ),
  )
}

// 所有页都装得进一屏才启用滚轮翻页；存在超高页（小窗口）时回落自由滚动
function canPageFlip(): boolean {
  const budget = window.innerHeight - NAV_HEIGHT
  return slideEls().every((el) => el.offsetHeight <= budget + 1)
}

// 滚轮落点在内部可滚动容器上时不劫持
function insideScrollable(e: WheelEvent): boolean {
  for (const node of e.composedPath()) {
    if (!(node instanceof HTMLElement)) continue
    const overflowY = getComputedStyle(node).overflowY
    if ((overflowY === 'auto' || overflowY === 'scroll') && node.scrollHeight > node.clientHeight) {
      return true
    }
  }
  return false
}

function flip(direction: 1 | -1, animate: boolean) {
  const positions = slideEls().map((el) => Math.max(0, el.offsetTop - NAV_HEIGHT))
  const y = window.scrollY
  const eps = 8
  const target =
    direction > 0
      ? positions.find((p) => p > y + eps)
      : [...positions].reverse().find((p) => p < y - eps)
  if (target == null) return
  window.scrollTo({ top: target, behavior: animate ? 'smooth' : 'auto' })
}

function onLandingWheel(e: WheelEvent) {
  if (e.deltaY === 0 || Math.abs(e.deltaY) < 2 || e.ctrlKey) return
  if (flipLocked) {
    // 翻页动画期间吞掉滚轮，避免原生滚动打断平滑动画
    e.preventDefault()
    return
  }
  if (insideScrollable(e) || !canPageFlip()) return
  e.preventDefault()
  flipLocked = true
  clearTimeout(flipLockTimer)
  flipLockTimer = setTimeout(() => {
    flipLocked = false
  }, FLIP_LOCK_MS)
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  flip(e.deltaY > 0 ? 1 : -1, !reduceMotion)
}

onMounted(() => {
  document.documentElement.classList.add('landing-scroll-snap')
  window.addEventListener('wheel', onLandingWheel, { passive: false })
})

onUnmounted(() => {
  document.documentElement.classList.remove('landing-scroll-snap')
  window.removeEventListener('wheel', onLandingWheel)
  clearTimeout(flipLockTimer)
})

const isLoggedIn = computed(() => Boolean(localStorage.getItem('auth_token')))

// ponytail: 遵循极简主义与 YAGNI 原则，页面轻盈无外部重量库负担

// ── 四大核心支柱 (Four Pillars) ──
const fourPillars = [
  {
    num: '01',
    title: 'AI 智能做账',
    desc: '自然语言理解业务动作，发票拍单一键起草分录，借贷自动试算配平。',
    chips: ['发票/语音智能记账', '1% 税率自动核算', '凭证附原始票据'],
    icon: 'ri-magic-line',
  },
  {
    num: '02',
    title: '微信与企银全量对平',
    desc: '微信商户账单与网银流水 10 秒交叉勾稽，杜绝漏账与重复记账。',
    chips: ['银行流水 100% 勾稽', '微信商业收款核销', '重复入账智能拦截'],
    icon: 'ri-git-merge-line',
  },
  {
    num: '03',
    title: '4 步向导式月末封账',
    desc: '往来核销、设备折旧、小微税负测算，向导一步步指引直达报税底稿。',
    chips: ['往来款一键冲平', '固定资产自动折旧', '税局申报字段对齐'],
    icon: 'ri-calendar-check-line',
  },
  {
    num: '04',
    title: '准则合规与公私隔离',
    desc: '严格遵循小企业会计准则，老板私卡垫资合规冲销，人机双签安全底线。',
    chips: ['小企业准则标准科目', '私卡垫资免税通道', '人机双签入账确认'],
    icon: 'ri-shield-check-line',
  },
]

// ── 账本迁移：历史账套导入与全量导出 (Migration) ──
const migrateIn = {
  title: '账本导入',
  icon: 'ri-download-cloud-2-line',
  items: [
    { b: '凭证批量导入', t: 'Excel / CSV 三列对齐，自动匹配科目' },
    { b: '期初余额带入', t: '科目余额表开新账，借贷平衡校验' },
    { b: '流水与发票', t: '银行、微信、税局发票免模板解析' },
  ],
  tags: ['Excel / CSV', '列名智能识别', '失败行逐条提示'],
}

const migrateOut = {
  title: '账本导出',
  icon: 'ri-upload-cloud-2-line',
  items: [
    { b: '凭证与账表', t: '凭证、明细账、科目余额表导出 Excel' },
    { b: '三大报表', t: '资产负债表、利润表、现金流量表' },
    { b: '完整账套备份', t: '科目、期初、凭证与票据整包带走' },
  ],
  tags: ['Excel / CSV', '全量备份'],
}

const migratePromises = [
  { icon: 'ri-refresh-line', title: '迁入只需一次导入' },
  { icon: 'ri-lock-unlock-line', title: '数据所有权归你' },
  { icon: 'ri-shield-check-line', title: '不做数据锁定' },
]

// ── 对标企微：深度功能交互展台 (Tab Showcase) ──
type ShowcaseKey = 'batch' | 'reconcile' | 'closing' | 'dashboard' | 'mcp'
const activeShowcase = ref<ShowcaseKey>('batch')

const showcaseTabs = [
  { key: 'batch' as ShowcaseKey, num: '01', title: '凭据归集', icon: 'ri-folder-upload-line' },
  { key: 'reconcile' as ShowcaseKey, num: '02', title: '流水对平', icon: 'ri-git-merge-line' },
  { key: 'closing' as ShowcaseKey, num: '03', title: '月末结账', icon: 'ri-calendar-check-line' },
  { key: 'dashboard' as ShowcaseKey, num: '04', title: '经营报表', icon: 'ri-pie-chart-line' },
  { key: 'mcp' as ShowcaseKey, num: '05', title: 'AI 连接 (MCP & Skills)', icon: 'ri-sparkling-2-line' },
]

function switchShowcaseTab(key: ShowcaseKey) {
  activeShowcase.value = key
}

// ── 视觉化指标对比数据 ──
const comparisons = [
  {
    dim: '资金成本',
    hired: '每年 6 到 10 万元',
    agency: '每年数千元 (基础报税)',
    solo: '极低成本，人人用得起',
    highlight: '降本 90%+',
  },
  {
    dim: '对账耗时',
    hired: '依赖月底人工耗费数天',
    agency: '平时不做真实对账',
    solo: '银行与微信流水 10 秒自动对平',
    highlight: '10 秒闭环',
  },
  {
    dim: '业务理解',
    hired: '需专职沟通业务',
    agency: '不理解业务，仅抄发票',
    solo: '自然语言懂语境，从业务动作推导分录',
    highlight: '懂你的业务',
  },
  {
    dim: '操作门槛',
    hired: '老板难以看懂会计账表',
    agency: '完全失去财务掌控',
    solo: '平时无需琐碎随手记，月末一键导入批量搞定',
    highlight: '0 财务门槛',
  },
]

// ── 典型角色与效能指标 ──
const personaChips = [
  {
    role: '独立开发者',
    icon: 'ri-code-s-slash-line',
    metrics: [{ val: '3 个', label: '外包项目收款' }, { val: '100%', label: '订阅对账闭环' }],
    tags: ['外包尾款对账', '服务器订阅费用归集', '软件退税测算'],
  },
  {
    role: '自媒体创作者',
    icon: 'ri-video-line',
    metrics: [{ val: '10 秒', label: '微信账单导入' }, { val: '自动', label: '摄影器材折旧' }],
    tags: ['商单回款核销', '器材折旧计提', '多渠道收支汇总'],
  },
  {
    role: '设计与咨询工作室',
    icon: 'ri-palette-line',
    metrics: [{ val: '合规', label: '公私垫资通道' }, { val: '清晰', label: '分期回款跟踪' }],
    tags: ['项目采买垫资隔离', '阶段付款跟进', '经营报表即时生成'],
  },
  {
    role: '知识创作者',
    icon: 'ri-book-open-line',
    metrics: [{ val: '0 基础', label: '无需会计背景' }, { val: '即时', label: '增值税额测算' }],
    tags: ['课程分账自动归集', '分录推导透明可查', '一键导出报税底稿'],
  },
]

// ── 合规与安全保障 ──
const complianceEvidence = [
  {
    icon: 'ri-scales-3-line',
    title: '100% 遵循小企业准则',
    desc: '严格执行《小企业会计准则》，借贷实时平衡校验。自动生成标准资产负债表与利润表，税局与会计师事务所直接认可。',
    points: ['准则标准科目体系', '税局申报底稿直出'],
  },
  {
    icon: 'ri-shield-user-line',
    title: '人机双签，老板最终把关',
    desc: 'AI 负责智能解析流水与起草分录，入账与封账权限 100% 由老板掌管，彻底消除 AI 乱做账与失控隐患。',
    points: ['AI 仅起草凭证草稿', '入账封账由老板确认'],
  },
  {
    icon: 'ri-file-shield-2-line',
    title: '流水与凭据双向留痕',
    desc: '银行回单、发票与凭证双向永久挂接，带数字时间戳审计流，查账、对账与审计随时调取原始票据举证。',
    points: ['原始票据双向挂接', '时间戳防篡改审计'],
  },
]
</script>

<template>
  <div class="landing-page">
    <!-- 1. 顶部导航：简单吸顶通栏 -->
    <header class="site-nav">
      <div class="nav-inner">
        <a class="nav-brand" href="#" @click.prevent="router.push(isLoggedIn ? '/' : '/landing')">
          <div class="nav-logo-box">
            <img src="/solo-logo.png" alt="梭罗记账" class="nav-logo" />
          </div>
          <span class="nav-brand-text">
            <b>梭罗记账</b>
            <span class="nav-sub">SoloBooks</span>
          </span>
        </a>
        <nav class="nav-links">
          <a href="#pillars">核心优势</a>
          <a href="#showcase">功能展台</a>
          <a href="#showcase" @click.prevent="router.push('/ai-connect')">AI 连接 (MCP & Skills)</a>
          <a href="#compare">方案对比</a>
          <a href="#migrate">账本迁移</a>
          <a href="#personas">适用场景</a>
          <a href="#trust">合规保障</a>
        </nav>
        <div class="nav-actions">
          <template v-if="isLoggedIn">
            <NButton type="primary" size="small" class="btn-primary" @click="router.push('/')">
              进入工作台
            </NButton>
          </template>
          <template v-else>
            <NButton type="primary" size="small" class="btn-primary" @click="router.push('/login')">
              登录 / 注册
            </NButton>
          </template>
        </div>
      </div>
    </header>

    <!-- 2. Hero 区域：开阔舒展、大厂气度、深度视觉锚定 (对标企微) -->
    <section class="hero">
      <div class="hero-glow" />
      <div class="hero-inner">
        <!-- 主标题 -->
        <h1 class="hero-title">
          把账记简单，<br class="hero-br-mobile" />
          <span class="title-highlight">把经营看清楚。</span>
        </h1>

        <!-- 价值副标 -->
        <p class="hero-sub">
          专为没有专职会计的小微企业与一人公司打造。<br class="desktop-only" />
          月末一键导入企银流水、发票与微信账单，AI 自动对平做账，报表直通电子税局。
        </p>

        <!-- 行动按钮 -->
        <div class="hero-action-block">
          <div class="hero-cta">
            <button class="island-cta-btn" @click="router.push('/login')">
              <span>免费注册，即得演示账套</span>
              <span class="btn-arrow-icon"><i class="ri-arrow-right-line" /></span>
            </button>
          </div>
        </div>

        <!-- 底部宽幅 Bento 价值指标看板 (横向舒展 1120px) -->
        <div class="hero-proof-bento">
          <div class="proof-card">
            <div class="proof-card-top">
              <div class="proof-icon-wrap"><i class="ri-time-line" /></div>
              <span class="proof-badge blue">高效省时</span>
            </div>
            <div class="proof-val">10 min</div>
            <div class="proof-title">月末批量全流程搞定</div>
            <p class="proof-desc">企银流水、发票包与微信对账单一拖即入，平时零琐碎记账负担。</p>
          </div>

          <div class="proof-card">
            <div class="proof-card-top">
              <div class="proof-icon-wrap"><i class="ri-shield-check-line" /></div>
              <span class="proof-badge emerald">准则合规</span>
            </div>
            <div class="proof-val">100%</div>
            <div class="proof-title">严格遵循会计准则</div>
            <p class="proof-desc">财政部《小企业会计准则》科目映射，借贷实时平衡，底稿直通税局。</p>
          </div>

          <div class="proof-card hover-glow" @click="router.push('/ai-connect')">
            <div class="proof-card-top">
              <div class="proof-icon-wrap"><i class="ri-terminal-box-line" /></div>
              <span class="proof-badge purple">开放协议</span>
            </div>
            <div class="proof-val text-gradient">MCP & Skills</div>
            <div class="proof-title">Claude / Cursor / Agent 直连</div>
            <p class="proof-desc">24 项原生 MCP 账务工具 + 报税/结账等专属 Skills 技能包，开箱即用，人机双签安全闭环。</p>
          </div>

          <div class="proof-card">
            <div class="proof-card-top">
              <div class="proof-icon-wrap"><i class="ri-play-circle-line" /></div>
              <span class="proof-badge amber">零门槛</span>
            </div>
            <div class="proof-val">¥0</div>
            <div class="proof-title">注册即得演示账套</div>
            <p class="proof-desc">内置脱敏演练数据账套，注册后自动生成，直接体验全套做账。</p>
          </div>
        </div>
      </div>
    </section>

    <!-- 3. 四大核心支柱总览 (Four Pillars) -->
    <section id="pillars" class="section section-pillars">
      <div class="section-inner">
        <div class="section-top text-center">
          <p class="section-tag">核心优势</p>
          <h2 class="section-headline">四大支柱，重塑小微企业记账体验</h2>
          <p class="section-subtitle">
            告别繁琐会计术语，直击真实做账与对账闭环。
          </p>
        </div>

        <div class="pillars-grid">
          <div v-for="p in fourPillars" :key="p.num" class="pillar-card">
            <div class="pillar-head">
              <div class="pillar-icon-box">
                <i :class="p.icon" />
              </div>
              <span class="pillar-num">{{ p.num }}</span>
            </div>
            <h3 class="pillar-title">{{ p.title }}</h3>
            <p class="pillar-desc">{{ p.desc }}</p>
            <div class="pillar-chips">
              <span v-for="c in p.chips" :key="c" class="pillar-chip">
                <i class="ri-check-line" />{{ c }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 4. 【升级对标企微】深度功能交互展台 (Tab Showcase) -->
    <section id="showcase" class="section section-showcase">
      <div id="mcp" class="mcp-anchor-proxy" />
      <div class="section-inner">
        <div class="section-top text-center">
          <p class="section-tag">核心功能</p>
          <h2 class="section-headline">全流程财务自动化，一目了然</h2>
          <p class="section-subtitle">
            告别繁复跨表操作。从凭据导入、流水对平，到月末报税与 AI 助手直连。
          </p>
        </div>

        <!-- 顶部 Tab 导航 -->
        <div class="showcase-nav-bar">
          <button
            v-for="tab in showcaseTabs"
            :key="tab.key"
            class="showcase-tab-btn"
            :class="{ active: activeShowcase === tab.key }"
            @click="switchShowcaseTab(tab.key)"
          >
            <span class="tab-btn-num">{{ tab.num }}</span>
            <i :class="tab.icon" />
            <span class="tab-btn-title">{{ tab.title }}</span>
            <span v-if="tab.key === 'mcp'" class="tab-mcp-badge">MCP + Skills</span>
          </button>
        </div>

        <!-- 展台主体容器 -->
        <div class="showcase-stage">
          <Transition name="fade-slide" mode="out-in">
            <!-- Tab 01: 凭据归集 -->
            <div v-if="activeShowcase === 'batch'" key="batch" class="stage-grid">
              <div class="stage-left">
                <h3 class="stage-title">月末一拖即入，<br />自动生成分录</h3>
                <p class="stage-desc">
                  无需日常琐碎记账。月末将网银流水、发票包与微信对账单一键拖入，AI 自动生成准则分录。
                </p>
                <div class="stage-bullets">
                  <div class="stage-bullet"><i class="ri-check-line" /><span>网银流水自适应解析，智能匹配会计科目</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span>发票智能识别，税率分流与明细提取</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span>凭证与原始票据双向挂接，全程留痕可查</span></div>
                </div>
              </div>
              <div class="stage-right">
                <div class="mockup-window">
                  <div class="window-bar">
                    <div class="window-dots"><span class="dot red" /><span class="dot yellow" /><span class="dot green" /></div>
                    <span class="window-title">SoloBooks 批量凭据归集工作台</span>
                    <span class="window-status"><i class="ri-shield-check-line text-emerald" /> 准则引擎就绪</span>
                  </div>
                  <div class="window-content">
                    <!-- 批量凭据拖入展示 -->
                    <div class="batch-drop-card">
                      <div class="batch-files">
                        <div class="file-chip">
                          <i class="ri-file-excel-2-line file-icon excel" />
                          <div class="file-info">
                            <span class="file-name">招商企银流水_9月.csv</span>
                            <span class="file-meta">18 笔收支明细</span>
                          </div>
                        </div>
                        <div class="file-chip">
                          <i class="ri-file-pdf-2-line file-icon pdf" />
                          <div class="file-info">
                            <span class="file-name">9月进销发票包.zip</span>
                            <span class="file-meta">12 张全电发票</span>
                          </div>
                        </div>
                        <div class="file-chip">
                          <i class="ri-wechat-pay-line file-icon wechat" />
                          <div class="file-info">
                            <span class="file-name">微信商户对账单.csv</span>
                            <span class="file-meta">17 笔经营流水</span>
                          </div>
                        </div>
                      </div>
                      <div class="agent-parse-banner">
                        <div class="parse-left">
                          <span class="parse-pulse" />
                          <span class="parse-text">AI 识别 47 笔交易 · 智能推导借贷会计科目与税率</span>
                        </div>
                        <span class="parse-status-badge"><i class="ri-check-line" />自动对平</span>
                      </div>
                    </div>

                    <!-- 生成凭证单据 -->
                    <div class="voucher-card">
                      <div class="voucher-top">
                        <span class="voucher-serial">记字第 2026-0921 号</span>
                        <span class="badge-balanced">
                          <i class="ri-check-line" />借贷已平 ¥30,000.00
                        </span>
                      </div>
                      <div class="voucher-grid">
                        <div class="v-row">
                          <span class="v-dir debit">借</span>
                          <span class="v-title">银行存款 - 招商银行基本户</span>
                          <span class="v-num">30,000.00</span>
                        </div>
                        <div class="v-row">
                          <span class="v-dir credit">贷</span>
                          <span class="v-title">主营业务收入 - 技术开发服务 <em class="rate-tag">1%</em></span>
                          <span class="v-num">29,702.97</span>
                        </div>
                        <div class="v-row">
                          <span class="v-dir credit">贷</span>
                          <span class="v-title">应交税费 - 应交增值税</span>
                          <span class="v-num">297.03</span>
                        </div>
                      </div>
                      <div class="voucher-bottom">
                        <span class="match-hint"><i class="ri-link-m" />已自动关联招行流水回单 (尾号 8802) · 附发票凭证</span>
                        <span class="btn-confirm-demo">一键确认入账</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Tab 02: 流水对平 -->
            <div v-else-if="activeShowcase === 'reconcile'" key="reconcile" class="stage-grid">
              <div class="stage-left">
                <h3 class="stage-title">微信与企银流水，<br />10 秒自动对平</h3>
                <p class="stage-desc">
                  深度勾稽每一笔银行出入与微信商户收款，自动核销往来账目，智能拦截重复入账。
                </p>
                <div class="stage-bullets">
                  <div class="stage-bullet"><i class="ri-check-line" /><span>企银流水 100% 自动对平，余额分毫不差</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span>微信商户商业收款核销，零散交易自动匹配</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span>重复到账与多次付款实时预警拦截</span></div>
                </div>
              </div>
              <div class="stage-right">
                <div class="mockup-window">
                  <div class="window-bar">
                    <div class="window-dots"><span class="dot red" /><span class="dot yellow" /><span class="dot green" /></div>
                    <span class="window-title">智能流水核销与勾稽监控</span>
                    <span class="window-status text-emerald"><i class="ri-refresh-line" /> 实时对平中</span>
                  </div>
                  <div class="window-content">
                    <div class="recon-detail-list">
                      <div class="recon-card-item success">
                        <div class="recon-card-icon"><i class="ri-bank-line" /></div>
                        <div class="recon-card-main">
                          <div class="recon-card-row">
                            <b>企业基本户流水 · 招商银行</b>
                            <span class="status-pill green"><i class="ri-check-line" />已全部平账</span>
                          </div>
                          <p>共 26 笔交易 · 收入 ¥64,800.00 · 支出 ¥22,150.00，均已关联记账凭证</p>
                        </div>
                      </div>

                      <div class="recon-card-item success">
                        <div class="recon-card-icon"><i class="ri-wechat-pay-line" /></div>
                        <div class="recon-card-main">
                          <div class="recon-card-row">
                            <b>微信商业收款 · 客户分期与零散付款</b>
                            <span class="status-pill green"><i class="ri-check-line" />自动核销</span>
                          </div>
                          <p>共 17 笔商户结算资金 · 自动按客户合同号匹配应收账款明细</p>
                        </div>
                      </div>

                      <div class="recon-card-item warning">
                        <div class="recon-card-icon"><i class="ri-shield-flash-line" /></div>
                        <div class="recon-card-main">
                          <div class="recon-card-row">
                            <b>智能防重看门狗拦截</b>
                            <span class="status-pill amber"><i class="ri-alert-line" />已智能拦截</span>
                          </div>
                          <p>检测到 ¥1,200.00 微信收款与网银转账日期及摘要高度吻合，已阻止重复计收</p>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Tab 03: 月末结账 -->
            <div v-else-if="activeShowcase === 'closing'" key="closing" class="stage-grid">
              <div class="stage-left">
                <h3 class="stage-title">4 步向导结账，<br />底稿直通税局</h3>
                <p class="stage-desc">
                  将复杂的期末损益结转降维为 4 步清晰向导。往来核销、设备折旧与税额测算一步到位。
                </p>
                <div class="stage-bullets">
                  <div class="stage-bullet"><i class="ri-check-line" /><span>应收应付往来款项一键冲平</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span>电脑器材与固定资产自动计提折旧</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span>小微纳税申报底稿自动对齐，防篡改锁账</span></div>
                </div>
              </div>
              <div class="stage-right">
                <div class="mockup-window">
                  <div class="window-bar">
                    <div class="window-dots"><span class="dot red" /><span class="dot yellow" /><span class="dot green" /></div>
                    <span class="window-title">月末结账向导 (2026年09月期)</span>
                    <span class="window-status text-blue"><i class="ri-hourglass-line" /> 向导第 4 步</span>
                  </div>
                  <div class="window-content">
                    <div class="closing-wizard-box">
                      <div class="wizard-step-item done">
                        <div class="w-num"><i class="ri-check-line" /></div>
                        <div class="w-info">
                          <b>第 1 步：往来账目核销</b>
                          <span>客户应收款与供应商款项全部核对平衡</span>
                        </div>
                        <span class="w-tag">无挂账</span>
                      </div>

                      <div class="wizard-step-item done">
                        <div class="w-num"><i class="ri-check-line" /></div>
                        <div class="w-info">
                          <b>第 2 步：固定资产折旧计提</b>
                          <span>MacBook Pro 16" (36期) 计提当月折旧 ¥388.89</span>
                        </div>
                        <span class="w-tag">已计提</span>
                      </div>

                      <div class="wizard-step-item done">
                        <div class="w-num"><i class="ri-check-line" /></div>
                        <div class="w-info">
                          <b>第 3 步：本期税额测算</b>
                          <span>应交增值税 ¥297.03 · 适用小微企业增值税优惠</span>
                        </div>
                        <span class="w-tag">底稿齐备</span>
                      </div>

                      <div class="wizard-step-item current">
                        <div class="w-num">4</div>
                        <div class="w-info">
                          <b>第 4 步：损益结转与封账</b>
                          <span>生成损益结转分录，完成本月账套归档锁定</span>
                        </div>
                        <button class="btn-wizard-action">老板确认封账</button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Tab 04: 经营报表 -->
            <div v-else-if="activeShowcase === 'dashboard'" key="dashboard" class="stage-grid">
              <div class="stage-left">
                <h3 class="stage-title">实时财务三张表，<br />看懂企业经营</h3>
                <p class="stage-desc">
                  账目实时联动资产负债表、利润表与现金流，随时掌握真实净利与可用资金储备。
                </p>
                <div class="stage-bullets">
                  <div class="stage-bullet"><i class="ri-check-line" /><span>标准三张表实时联动生成，无需等待代账滞后汇报</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span>真实净利润与现金流跑道测算，掌控经营底牌</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span>客户应收账款账龄预警，逾期回款随时提醒</span></div>
                </div>
              </div>
              <div class="stage-right">
                <div class="mockup-window">
                  <div class="window-bar">
                    <div class="window-dots"><span class="dot red" /><span class="dot yellow" /><span class="dot green" /></div>
                    <span class="window-title">SoloBooks 财务与经营总览大盘</span>
                    <span class="window-status text-emerald"><i class="ri-checkbox-circle-fill" /> 财务健康度 98分</span>
                  </div>
                  <div class="window-content">
                    <div class="board-metrics-grid">
                      <div class="board-kpi-card">
                        <span class="kpi-label">本月营业收入</span>
                        <div class="kpi-val">¥128,500<span class="kpi-decimal">.00</span></div>
                        <span class="kpi-trend up"><i class="ri-arrow-up-line" /> 环比增长 24%</span>
                      </div>
                      <div class="board-kpi-card">
                        <span class="kpi-label">本月净利润</span>
                        <div class="kpi-val text-emerald">¥76,200<span class="kpi-decimal">.00</span></div>
                        <span class="kpi-trend up"><i class="ri-arrow-up-line" /> 净利率 59.3%</span>
                      </div>
                      <div class="board-kpi-card">
                        <span class="kpi-label">可用资金储备</span>
                        <div class="kpi-val">¥245,800<span class="kpi-decimal">.00</span></div>
                        <span class="kpi-trend neutral">现金充裕 (预计可支撑 14 个月)</span>
                      </div>
                      <div class="board-kpi-card">
                        <span class="kpi-label">待收客户尾款</span>
                        <div class="kpi-val text-blue">¥18,000<span class="kpi-decimal">.00</span></div>
                        <span class="kpi-trend warning">2 笔均处于 30 天账期内</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Tab 05: AI 连接 (MCP & Skills) -->
            <div v-else-if="activeShowcase === 'mcp'" key="mcp" class="stage-grid">
              <div class="stage-left">
                <h3 class="stage-title">原生支持 MCP + Skills，<br />打造懂小微财税的数字员工</h3>
                <p class="stage-desc">
                  全面支持 Model Context Protocol 协议与 Agent Skills 规范。不仅能在 Claude、Cursor 中对话查账起草凭证，更可挂载专属财税技能包，驱动半自动报税核对等端到端高阶工作流。
                </p>
                <div class="stage-bullets">
                  <div class="stage-bullet"><i class="ri-check-line" /><span><b>24 个原生 MCP 工具</b>：覆盖记账起草、三方流水对账、结账五项检查、申报作业包与资产台账</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span><b>专属 Agent Skills 技能包</b>：挂载半自动报税伴侣，联动浏览器自动比对税局底账、识别无票收入、预填申报表</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span><b>人机双签安全红线</b>：AI 只产生待确认草稿不直接入账、不碰税局密码与提交按钮，数据可审计</span></div>
                </div>
                <div class="stage-cta-row">
                  <button class="stage-cta-primary" @click="router.push('/ai-connect')">
                    <i class="ri-book-read-line" />
                    查看完整接入指南
                  </button>
                  <button
                    class="stage-cta-ghost"
                    @click="router.push(isLoggedIn ? '/mine/mcp' : '/login')"
                  >
                    <i class="ri-key-2-line" />
                    签发 MCP 令牌
                  </button>
                </div>
              </div>
              <div class="stage-right">
                <div class="mockup-window mcp-window">
                  <div class="window-bar dark-bar">
                    <div class="window-dots"><span class="dot red" /><span class="dot yellow" /><span class="dot green" /></div>
                    <span class="window-title">Claude Desktop / Agent · SoloBooks MCP + Tax Filing Skill</span>
                    <span class="window-status text-emerald"><i class="ri-broadcast-fill" /> Connected (24 Tools + Skills)</span>
                  </div>
                  <div class="mcp-terminal-body">
                    <!-- 用户提问 -->
                    <div class="chat-bubble user-bubble">
                      <div class="bubble-sender">Founder (You)</div>
                      <div class="bubble-text">
                        帮我准备 Q3 增值税与所得税申报包，并在已打开的电子税务局页面核对开票数据是否一致。
                      </div>
                    </div>

                    <!-- AI 响应 -->
                    <div class="chat-bubble assistant-bubble">
                      <div class="bubble-sender">
                        <i class="ri-sparkling-fill text-purple" /> Claude Assistant (via SoloBooks MCP & Skills)
                      </div>

                      <div class="mcp-skill-chip">
                        <i class="ri-magic-line" />
                        <span>已激活技能：<code>solobooks-tax-filing</code>（半自动纳税申报伴侣）</span>
                      </div>

                      <div class="mcp-call-chip">
                        <i class="ri-cpu-line" />
                        <span>调用工具：<code>mcp__solobooks__generate_filing_package({ period: "2026-Q3" })</code></span>
                      </div>
                      <div class="bubble-text">
                        📊 <b>账套测算完成：</b>季度销售额 ¥245,800.00 未超 30 万起征点，享受小微免征增值税（应缴 ¥0.00）；企业所得税减按 5% 预缴，应补税额 ¥3,410.00。
                      </div>

                      <div class="mcp-call-chip">
                        <i class="ri-window-line" />
                        <span>联动浏览器：<code>chrome_devtools__take_snapshot()</code> 读取税局底账</span>
                      </div>
                      <div class="mcp-voucher-preview">
                        <div class="mcp-v-title">🎯 电子税务局金税底账逆向比对结果</div>
                        <div class="mcp-v-lines">
                          <div>• 税局归集开票销售额：¥245,800.00（与账套发票池 100% 勾稽一致）</div>
                          <div>• 申报表预填状态：小微免税销售额、实际利润额已在税局网页填妥</div>
                          <div>• 视觉标注：已在网页中用黄色边框高亮标注所有已填入输入框</div>
                        </div>
                      </div>
                      <div class="mcp-safety-note">
                        <i class="ri-shield-keyhole-line text-emerald" />
                        <span><b>人机双签安全红线：</b>AI 严禁代点税局「提交申报」按钮。数据已核验无误，请在浏览器中最终由您本人亲手点击提交。</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </Transition>
        </div>
      </div>
    </section>

    <!-- 5. 痛点指标对比 (Visual Comparison Cards) -->
    <section id="compare" class="section section-compare">
      <div class="section-inner">
        <div class="section-top text-center">
          <p class="section-tag">清晰对比</p>
          <h2 class="section-headline">为什么选择梭罗记账？</h2>
          <p class="section-subtitle">为一人公司和小微团队摆脱专职高成本与传统软件的繁重门槛。</p>
        </div>

        <div class="compare-grid">
          <div v-for="comp in comparisons" :key="comp.dim" class="compare-card">
            <div class="compare-header">
              <span class="compare-dim">{{ comp.dim }}</span>
              <span class="compare-badge">{{ comp.highlight }}</span>
            </div>

            <!-- 梭罗记账高亮方案 -->
            <div class="solo-solution">
              <div class="solo-solution-label">
                <i class="ri-sparkling-fill" />梭罗记账
              </div>
              <div class="solo-solution-text">{{ comp.solo }}</div>
            </div>

            <!-- 传统方案对比 -->
            <div class="legacy-options">
              <div class="legacy-item">
                <span class="legacy-tag">专职会计</span>
                <span class="legacy-desc">{{ comp.hired }}</span>
              </div>
              <div class="legacy-item">
                <span class="legacy-tag">传统代账</span>
                <span class="legacy-desc">{{ comp.agency }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 6. 账本迁移：历史账套导入与全量导出 (Migration) -->
    <section id="migrate" class="section section-migrate">
      <div class="section-inner">
        <div class="section-top text-center">
          <p class="section-tag">无痛迁移</p>
          <h2 class="section-headline">换账不搬家，<br class="hero-br-mobile" />历史账本一键带走</h2>
          <p class="section-subtitle">
            从传统代账或其他财务软件迁入，历史凭证不必重录；<br class="hero-br-mobile" />账本随时完整导出，不做锁定。
          </p>
        </div>

        <div class="migrate-grid">
          <div class="migrate-card migrate-in">
            <div class="migrate-head">
              <div class="migrate-icon"><i :class="migrateIn.icon" /></div>
              <h3 class="migrate-title">{{ migrateIn.title }}</h3>
            </div>
            <ul class="migrate-list">
              <li v-for="it in migrateIn.items" :key="it.b">
                <i class="ri-check-line" />
                <span><b>{{ it.b }}</b>：{{ it.t }}</span>
              </li>
            </ul>
            <div class="migrate-tags">
              <span v-for="t in migrateIn.tags" :key="t" class="migrate-tag">{{ t }}</span>
            </div>
          </div>

          <div class="migrate-bridge">
            <div class="bridge-icon"><i class="ri-arrow-left-right-line" /></div>
            <span class="bridge-label">双向互通</span>
          </div>

          <div class="migrate-card migrate-out">
            <div class="migrate-head">
              <div class="migrate-icon"><i :class="migrateOut.icon" /></div>
              <h3 class="migrate-title">{{ migrateOut.title }}</h3>
            </div>
            <ul class="migrate-list">
              <li v-for="it in migrateOut.items" :key="it.b">
                <i class="ri-check-line" />
                <span><b>{{ it.b }}</b>：{{ it.t }}</span>
              </li>
            </ul>
            <div class="migrate-tags">
              <span v-for="t in migrateOut.tags" :key="t" class="migrate-tag">{{ t }}</span>
            </div>
          </div>
        </div>

        <div class="migrate-promises">
          <div v-for="p in migratePromises" :key="p.title" class="promise-item">
            <i :class="p.icon" />
            <span>{{ p.title }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- 7. 典型场景：指标看板卡片 -->
    <section id="personas" class="section">
      <div class="section-inner">
        <div class="section-top text-center">
          <p class="section-tag">量身打造</p>
          <h2 class="section-headline">谁在用梭罗记账？</h2>
          <p class="section-subtitle">独立开发者、工作室与小微团队的真实生产力工具。</p>
        </div>

        <div class="persona-grid">
          <div v-for="p in personaChips" :key="p.role" class="persona-item">
            <div class="p-header">
              <div class="p-avatar"><i :class="p.icon" /></div>
              <h3 class="p-role">{{ p.role }}</h3>
            </div>

            <!-- 关键指标大字 -->
            <div class="p-metrics">
              <div v-for="m in p.metrics" :key="m.label" class="p-metric">
                <span class="p-val">{{ m.val }}</span>
                <span class="p-lbl">{{ m.label }}</span>
              </div>
            </div>

            <!-- 业务场景标签 -->
            <div class="p-tags">
              <span v-for="t in p.tags" :key="t" class="p-tag"><i class="ri-check-line" />{{ t }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 8. 合规保障 (Slide 8) -->
    <section id="trust" class="section section-trust">
      <div class="section-inner">
        <div class="section-top text-center">
          <p class="section-tag">合规保障</p>
          <h2 class="section-headline">严谨合规，放心报税</h2>
          <p class="section-subtitle">
            遵循财政部准则，直通电子税局，让每一笔账目都有据可查。
          </p>
        </div>

        <!-- 3 大核心合规保障卡片 -->
        <div class="compliance-evidence-grid">
          <div v-for="e in complianceEvidence" :key="e.title" class="evidence-card">
            <div class="evidence-icon-wrap">
              <i :class="e.icon" />
            </div>
            <h3 class="evidence-title">{{ e.title }}</h3>
            <p class="evidence-desc">{{ e.desc }}</p>
            <div class="evidence-points">
              <span v-for="pt in e.points" :key="pt" class="evidence-point-tag">
                <i class="ri-check-line" />{{ pt }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 9. 底部精简 CTA 与页脚 (整合为最终幻灯片) -->
    <section class="landing-slide-end">
      <div class="cta-banner">
        <div class="cta-glow-mesh" />
        <div class="cta-inner text-center">
          <h2 class="cta-title">让专属 AI 会计，打理你的公司账目</h2>
          <p class="cta-sub">免费注册，1 分钟建立企业专属智能账本。</p>
          <div class="cta-buttons">
            <button class="island-cta-btn" @click="router.push('/login')">
              <span>免费注册 / 登录</span>
              <span class="btn-arrow-icon"><i class="ri-arrow-right-line" /></span>
            </button>
          </div>
        </div>
      </div>

      <footer class="site-footer">
        <div class="section-inner footer-inner">
          <div class="footer-brand">
            <img src="/solo-logo.png" alt="梭罗记账" class="footer-logo" />
            <div class="footer-brand-info">
              <b>梭罗记账 SoloBooks</b>
              <p>把账记简单，把经营看清楚。</p>
            </div>
          </div>
          <nav class="footer-nav">
            <a href="/agreement" target="_blank">服务协议</a>
            <a href="/privacy" target="_blank">隐私政策</a>
            <a href="/login">进入系统</a>
          </nav>
        </div>
        <div class="section-inner footer-sub">
          <span>© {{ new Date().getFullYear() }} 梭罗记账 SoloBooks · 保留所有权利</span>
          <div class="footer-filings">
            <a v-if="ICP_LICENSE" href="https://beian.miit.gov.cn" target="_blank" rel="noreferrer">{{ ICP_LICENSE }}</a>
            <a v-if="PSB_RECORD" :href="PSB_RECORD_URL" target="_blank" rel="noreferrer">{{ PSB_RECORD }}</a>
          </div>
        </div>
      </footer>
    </section>
  </div>
</template>

<style scoped>
/* ── PPT 幻灯片式整屏滚动：JS 接管桌面滚轮翻页，CSS proximity 轻吸附兜底 ──
   不用 mandatory：Chromium 吸附补位是瞬移，且超高页（小窗口）会被钳制回拽 */
:global(html:has(.landing-page)),
:global(html.landing-scroll-snap) {
  scroll-behavior: smooth;
  scroll-snap-type: y proximity;
  scroll-padding-top: 56px;
}

.landing-page {
  min-height: 100vh;
  background-color: #f8fafc;
  color: #0f172a;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Noto Sans SC", sans-serif;
}

/* ── 顶部导航：简单吸顶通栏 ── */
.site-nav {
  position: sticky;
  top: 0;
  z-index: 100;
  height: 56px;
  background: #ffffff;
  border-bottom: 1px solid #e2e8f0;
}

.nav-inner {
  max-width: 1100px;
  height: 100%;
  margin: 0 auto;
  padding: 0 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.nav-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
  flex-shrink: 0;
  white-space: nowrap;
}

.nav-logo-box {
  width: 32px;
  height: 32px;
}

.nav-logo {
  width: 100%;
  height: 100%;
  display: block;
}

.nav-brand-text {
  display: flex;
  flex-direction: column;
}

.nav-brand-text b {
  font-size: 14.5px;
  font-weight: 750;
  color: #0f172a;
  line-height: 1.1;
  letter-spacing: -0.2px;
}

.nav-sub {
  font-size: 9.5px;
  color: #5b6a7c;
  font-weight: 500;
  letter-spacing: 0.5px;
}

.nav-links {
  display: flex;
  gap: 24px;
  /* 中窄屏空间不足时横向滚动，禁止逐字竖排换行溢出导航条 */
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}

.nav-links::-webkit-scrollbar {
  display: none;
}

.nav-links a {
  font-size: 13px;
  color: #475569;
  text-decoration: none;
  font-weight: 500;
  transition: color 0.2s cubic-bezier(0.16, 1, 0.3, 1);
  position: relative;
  white-space: nowrap;
  flex-shrink: 0;
}

.nav-links a:hover {
  color: #1747b0;
}

.nav-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.btn-primary {
  font-weight: 600;
}

/* ── Hero 区域 (对标企微开阔大厂气度 · Slide 1) ── */
.hero {
  position: relative;
  overflow: hidden;
  border-bottom: 1px solid #e2e8f0;
  background:
    radial-gradient(circle at 50% -20%, rgba(23, 71, 176, 0.09) 0%, rgba(30, 127, 251, 0.03) 45%, transparent 75%),
    linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
  min-height: calc(100vh - 56px);
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 40px 0 60px;
  scroll-snap-align: start;
  scroll-snap-stop: normal;
}

.hero-glow {
  position: absolute;
  top: -160px;
  left: 50%;
  transform: translateX(-50%);
  width: 1100px;
  height: 600px;
  background: radial-gradient(circle, rgba(23, 71, 176, 0.08) 0%, rgba(30, 127, 251, 0.02) 50%, transparent 70%);
  pointer-events: none;
}

.hero-inner {
  position: relative;
  z-index: 1;
  max-width: 1140px;
  margin: 0 auto;
  padding: 0 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.hero-title {
  margin: 0 0 22px;
  font-size: 58px;
  line-height: 1.16;
  font-weight: 850;
  color: #0f172a;
  letter-spacing: -1.2px;
}

.hero-br-mobile {
  display: none;
}

.title-highlight {
  background: linear-gradient(135deg, #1747b0 0%, #1e7ffb 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.hero-sub {
  margin: 0 0 36px;
  font-size: 18px;
  line-height: 1.75;
  color: #475569;
  max-width: 780px;
}

.desktop-only {
  display: inline;
}

/* 行动块 */
.hero-action-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 48px;
}

.hero-cta {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  flex-wrap: wrap;
}

.cursor-pointer {
  cursor: pointer;
}

/* 底部 4 栏 Bento 价值看板 (横向舒展 1120px) */
.hero-proof-bento {
  width: 100%;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 18px;
  text-align: left;
}

.proof-card {
  background: #ffffff;
  border: 1px solid rgba(226, 232, 240, 0.9);
  border-radius: 12px;
  padding: 22px 20px;
  display: flex;
  flex-direction: column;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 4px 16px -2px rgba(15, 23, 42, 0.04),
    0 1px 3px rgba(0, 0, 0, 0.02);
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  position: relative;
}

.proof-card:hover {
  transform: translateY(-3px);
  border-color: rgba(23, 71, 176, 0.3);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 1),
    0 16px 32px -8px rgba(23, 71, 176, 0.12);
}

.proof-card.hover-glow:hover {
  border-color: rgba(126, 34, 206, 0.35);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 1),
    0 16px 32px -8px rgba(126, 34, 206, 0.15);
}

.proof-card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.proof-icon-wrap {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  color: #475569;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
  transition: all 0.2s ease;
}

.proof-card:hover .proof-icon-wrap {
  border-color: #cbd5e1;
  color: #0f172a;
  background: #ffffff;
}

.proof-badge {
  font-size: 11px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 999px;
  letter-spacing: 0.02em;
}

.proof-badge.blue { background: #f0f6ff; color: #1d4ed8; border: 1px solid #dbeafe; }
.proof-badge.emerald { background: #eaf7f1; color: #0f7354; border: 1px solid #dcfce7; }
.proof-badge.purple { background: #faf5ff; color: #6b21a8; border: 1px solid #f3e8ff; }
.proof-badge.amber { background: #fffbeb; color: #a05c10; border: 1px solid #fef3c7; }

.proof-val {
  font-size: 26px;
  font-weight: 800;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  color: #0f172a;
  line-height: 1.15;
  margin-bottom: 6px;
}

.proof-val.text-gradient {
  background: linear-gradient(135deg, #1747b0 0%, #7e22ce 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.proof-title {
  font-size: 14px;
  font-weight: 700;
  color: #1e293b;
  margin-bottom: 6px;
}

.proof-desc {
  margin: 0;
  font-size: 12px;
  color: #5b6a7c;
  line-height: 1.55;
}

/* 岛式按钮架构 (Island Button Architecture) */
.island-cta-btn {
  height: 48px;
  padding: 0 8px 0 24px;
  display: inline-flex;
  align-items: center;
  gap: 12px;
  border-radius: 999px;
  background: #1747b0;
  color: #ffffff;
  border: none;
  font-size: 14.5px;
  font-weight: 600;
  cursor: pointer;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.25),
    0 4px 16px rgba(23, 71, 176, 0.3);
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

.island-cta-btn:hover {
  background: #0d3282;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.3),
    0 8px 24px rgba(23, 71, 176, 0.35);
  transform: translateY(-1px);
}

.island-cta-btn:active {
  transform: scale(0.97);
}

.btn-arrow-icon {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.island-cta-btn:hover .btn-arrow-icon {
  transform: translateX(2px);
}



/* 月末批量凭据拖入组件 */
.batch-drop-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
}

.batch-files {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}

.file-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  padding: 6px 8px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
}

.file-icon {
  font-size: 16px;
  flex-shrink: 0;
}

.file-icon.excel { color: #0f7354; }
.file-icon.pdf { color: #e11d48; }
.file-icon.wechat { color: #0284c7; }

.file-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.file-name {
  font-size: 11px;
  font-weight: 600;
  color: #1e293b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-meta {
  font-size: 10px;
  color: #5b6a7c;
}

.agent-parse-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 6px;
  border-top: 1px dashed #cbd5e1;
  gap: 8px;
}

.parse-left {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.parse-pulse {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #0f7354;
  box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.2);
  flex-shrink: 0;
}

.parse-text {
  font-size: 11px;
  color: #334155;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.parse-status-badge {
  font-size: 10px;
  font-weight: 600;
  color: #0f7354;
  background: #eaf7f1;
  border: 1px solid #a7f3d0;
  border-radius: 4px;
  padding: 1px 6px;
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

/* 凭证单据 */
.voucher-card {
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.03);
}

.voucher-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  background: #f1f5f9;
  border-bottom: 1px solid #e2e8f0;
  font-size: 11.5px;
}

.voucher-serial {
  font-weight: 600;
  color: #475569;
}

.badge-balanced {
  color: #0f7354;
  font-weight: 600;
  background: #d1fae5;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10.5px;
  display: flex;
  align-items: center;
  gap: 2px;
  animation: balance-glow 3s cubic-bezier(0.16, 1, 0.3, 1) infinite;
}

@keyframes balance-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.2); }
  50% { box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.25); }
}

.voucher-grid {
  padding: 4px 0;
}

.v-row {
  display: grid;
  grid-template-columns: 20px 1fr auto;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  font-size: 12px;
  border-bottom: 1px solid #f8fafc;
}

.v-dir {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 3px;
  border-radius: 4px;
  text-align: center;
}

.v-dir.debit { background: #fee2e2; color: #b91c1c; }
.v-dir.credit { background: #e0e7ff; color: #3730a3; }

.v-title {
  color: #1e293b;
  font-weight: 500;
}

.rate-tag {
  font-style: normal;
  font-size: 9.5px;
  background: #edf3ff;
  color: #1747b0;
  padding: 1px 4px;
  border-radius: 4px;
  margin-left: 2px;
}

.v-num {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-weight: 600;
  color: #0f172a;
}

.voucher-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  background: #f8fafc;
  border-top: 1px solid #e2e8f0;
  font-size: 11px;
}

.match-hint {
  color: #0f7354;
  display: flex;
  align-items: center;
  gap: 4px;
}

.btn-confirm-demo {
  background: #1747b0;
  color: #ffffff;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 600;
  font-size: 11px;
  box-shadow: 0 1px 3px rgba(23, 71, 176, 0.3);
}

/* 对账模式 */
.recon-sandbox {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.recon-tile {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.recon-tile:hover {
  transform: translateX(2px);
}

.tile-icon {
  font-size: 18px;
  color: #1747b0;
}

.tile-text {
  display: flex;
  flex-direction: column;
  flex: 1;
}

.tile-text b {
  font-size: 12.5px;
  color: #0f172a;
}

.tile-text span {
  font-size: 11px;
  color: #5b6a7c;
}

.tile-chip {
  font-size: 10.5px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 4px;
}

.tile-chip.success { background: #eaf7f1; color: #0f7354; }
.tile-chip.warn { background: #fffbeb; color: #a05c10; }

/* 结账模式 */
.closing-sandbox {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.close-step-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  font-size: 12px;
}

.close-step-row.done {
  color: #0f7354;
  background: #eaf7f1;
}

.close-step-row.current {
  color: #1747b0;
  background: #f8fbff;
  border-color: rgba(23, 71, 176, 0.3);
}

.step-title {
  font-weight: 600;
  flex: 1;
}

.step-status {
  font-size: 11px;
  color: #5b6a7c;
}

.step-status.highlight {
  color: #1747b0;
  font-weight: 700;
}

/* 沙盒底栏 */
.sandbox-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  background: #f8fafc;
  border-top: 1px solid #e2e8f0;
  font-size: 11px;
  color: #475569;
}

.footer-chip {
  display: flex;
  align-items: center;
  gap: 4px;
}

.text-emerald { color: #0f7354; }

/* ── 通用 Section (幻灯片 Slide 单元) ── */
.section {
  padding: 60px 0;
  position: relative;
  min-height: calc(100vh - 56px);
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  justify-content: center;
  scroll-snap-align: start;
  scroll-snap-stop: normal;
}

.section-inner {
  max-width: 1180px;
  margin: 0 auto;
  padding: 0 24px;
}

.section-top {
  margin-bottom: 44px;
}

.text-center { text-align: center; }

.section-tag {
  font-size: 12px;
  font-weight: 700;
  color: #1747b0;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  margin: 0 0 8px;
}

.section-headline {
  margin: 0 0 10px;
  font-size: 28px;
  font-weight: 800;
  color: #0f172a;
  letter-spacing: -0.4px;
}

.section-subtitle {
  margin: 0;
  font-size: 15px;
  color: #5b6a7c;
}

/* ── 3. 四大核心支柱 (Four Pillars) ── */
.section-pillars {
  background: #f8fafc;
}

.pillars-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 20px;
}

.pillar-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 24px 20px;
  display: flex;
  flex-direction: column;
  position: relative;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 2px 8px rgba(15, 23, 42, 0.03);
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}

.pillar-card:hover {
  transform: translateY(-3px);
  border-color: rgba(23, 71, 176, 0.3);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 1),
    0 16px 32px -8px rgba(23, 71, 176, 0.12);
}

.pillar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.pillar-icon-box {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: #edf3ff;
  color: #1747b0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  box-shadow: 0 2px 8px rgba(23, 71, 176, 0.1);
  transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

.pillar-card:hover .pillar-icon-box {
  transform: scale(1.08);
}

.pillar-num {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  font-weight: 800;
  color: #5f6e80;
}

.pillar-title {
  margin: 0 0 10px;
  font-size: 19px;
  font-weight: 800;
  color: #0f172a;
  letter-spacing: -0.2px;
}

.pillar-desc {
  margin: 0 0 20px;
  font-size: 13.5px;
  color: #475569;
  line-height: 1.6;
}

.pillar-chips {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: auto;
}

.pillar-chip {
  font-size: 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  padding: 6px 10px;
  border-radius: 4px;
  color: #334155;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 6px;
}

.pillar-chip i {
  color: #0f7354;
  font-size: 14px;
}

/* ── 4. 深度功能交互展台 (Tab Showcase) ── */
.section-showcase {
  background: #ffffff;
  border-top: 1px solid #e2e8f0;
  border-bottom: 1px solid #e2e8f0;
  padding: 40px 0 20px;
}

.mcp-anchor-proxy {
  position: relative;
  top: -80px;
  visibility: hidden;
  height: 0;
}

.showcase-nav-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: #f1f5f9;
  padding: 6px;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  max-width: 860px;
  margin: 0 auto 32px;
}

.showcase-tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 18px;
  background: transparent;
  border: none;
  border-radius: 8px;
  font-size: 13.5px;
  font-weight: 600;
  color: #5b6a7c;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
  position: relative;
}

.showcase-tab-btn:hover {
  color: #0f172a;
}

.showcase-tab-btn.active {
  background: #ffffff;
  color: #1747b0;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04);
}

.tab-btn-num {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11.5px;
  font-weight: 700;
  opacity: 0.6;
}

.tab-mcp-badge {
  font-size: 10px;
  background: #e0e7ff;
  color: #4338ca;
  padding: 1px 6px;
  border-radius: 999px;
  font-weight: 700;
}

.showcase-stage {
  min-height: auto;
}

.stage-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.28fr);
  gap: 48px;
  align-items: center;
}

.stage-left {
  display: flex;
  flex-direction: column;
}

.stage-title {
  margin: 0 0 14px;
  font-size: 28px;
  font-weight: 800;
  color: #0f172a;
  line-height: 1.3;
  letter-spacing: -0.4px;
}

.stage-desc {
  margin: 0 0 20px;
  font-size: 15px;
  color: #475569;
  line-height: 1.65;
}

.stage-bullets {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 0;
}

.stage-bullet {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13.5px;
  color: #334155;
  font-weight: 500;
}

.stage-bullet i {
  color: #0f7354;
  font-size: 16px;
  font-weight: bold;
}

/* MCP 展台的行动入口：把「演示」接到「接入指南」与「签发令牌」 */
.stage-cta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 22px;
}

.stage-cta-primary,
.stage-cta-ghost {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 15px;
  border-radius: 8px;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.18s, color 0.18s, border-color 0.18s;
}

.stage-cta-primary {
  background: #1747b0;
  color: #ffffff;
  border: 1px solid #1747b0;
}

.stage-cta-primary:hover {
  background: #2b66e2;
  border-color: #2b66e2;
}

.stage-cta-ghost {
  background: #ffffff;
  color: #182230;
  border: 1px solid #dde3ea;
}

.stage-cta-ghost:hover {
  border-color: #1747b0;
  color: #1747b0;
}

/* 展台右侧视窗框架 */
.mockup-window {
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 20px 48px -12px rgba(15, 23, 42, 0.12);
}

.window-bar {
  height: 38px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  padding: 0 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.window-dots {
  display: flex;
  gap: 6px;
}

.window-dots .dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
}

.window-dots .dot.red { background: #ff5f56; }
.window-dots .dot.yellow { background: #ffbd2e; }
.window-dots .dot.green { background: #27c93f; }

.window-title {
  font-size: 11.5px;
  font-weight: 600;
  color: #5b6a7c;
}

.window-status {
  font-size: 11px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 4px;
}

.window-content {
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* 对账详情展台卡片 */
.recon-detail-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.recon-card-item {
  display: flex;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.recon-card-item:hover {
  transform: translateX(2px);
  border-color: rgba(23, 71, 176, 0.3);
}

.recon-card-item.success {
  background: #fcfdfe;
}

.recon-card-item.warning {
  background: #fffdfa;
  border-color: rgba(217, 119, 6, 0.3);
}

.recon-card-icon {
  font-size: 22px;
  color: #1747b0;
  padding-top: 2px;
}

.recon-card-item.warning .recon-card-icon {
  color: #d97706;
}

.recon-card-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.recon-card-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.recon-card-row b {
  font-size: 13.5px;
  color: #0f172a;
}

.recon-card-main p {
  margin: 0;
  font-size: 12px;
  color: #5b6a7c;
}

.status-pill {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.status-pill.green {
  background: #eaf7f1;
  color: #0f7354;
}

.status-pill.amber {
  background: #fffbeb;
  color: #d97706;
}

/* 结账向导卡片 */
.closing-wizard-box {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.wizard-step-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #ffffff;
}

.wizard-step-item.done {
  background: #eaf7f1;
  border-color: rgba(5, 150, 105, 0.25);
}

.wizard-step-item.current {
  background: #f8fbff;
  border-color: rgba(23, 71, 176, 0.35);
  box-shadow: 0 2px 8px rgba(23, 71, 176, 0.08);
}

.w-num {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #e2e8f0;
  color: #475569;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
}

.wizard-step-item.done .w-num {
  background: #0f7354;
  color: #ffffff;
}

.wizard-step-item.current .w-num {
  background: #1747b0;
  color: #ffffff;
}

.w-info {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.w-info b {
  font-size: 13px;
  color: #0f172a;
}

.w-info span {
  font-size: 11.5px;
  color: #5b6a7c;
}

.w-tag {
  font-size: 11px;
  font-weight: 600;
  color: #0f7354;
  background: #eaf7f1;
  padding: 2px 8px;
  border-radius: 4px;
}

.btn-wizard-action {
  background: #1747b0;
  color: #ffffff;
  border: none;
  padding: 6px 12px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(23, 71, 176, 0.3);
}

/* 经营大盘 KPI 卡片 */
.board-metrics-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

.board-kpi-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
}

.kpi-label {
  font-size: 12px;
  color: #5b6a7c;
  font-weight: 500;
  margin-bottom: 6px;
}

.kpi-val {
  font-size: 22px;
  font-weight: 800;
  color: #0f172a;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.kpi-decimal {
  font-size: 14px;
  font-weight: 600;
  color: #5f6e80;
}

.kpi-trend {
  margin-top: 8px;
  font-size: 11px;
  font-weight: 600;
}

.kpi-trend.up { color: #0f7354; }
.kpi-trend.neutral { color: #475569; }
.kpi-trend.warning { color: #d97706; }

/* MCP 终端仿真视窗 */
.mcp-window {
  border-color: #334155;
  box-shadow: 0 20px 48px -12px rgba(15, 23, 42, 0.25);
}

.dark-bar {
  background: #0b1120 !important;
  border-color: #1e293b !important;
}

.dark-bar .window-title {
  color: #94a3b8 !important;
}

.mcp-terminal-body {
  background: #0f172a;
  color: #f1f5f9;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 12.5px;
}

.chat-bubble {
  border-radius: 8px;
  padding: 10px 12px;
}

.user-bubble {
  background: #1e293b;
  border: 1px solid #334155;
  align-self: flex-end;
  max-width: 90%;
}

.assistant-bubble {
  background: #162032;
  border: 1px solid #1e293b;
}

.bubble-sender {
  font-size: 11px;
  font-weight: 700;
  color: #94a3b8;
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.text-purple { color: #a855f7; }

.mcp-skill-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(168, 85, 247, 0.15);
  color: #d8b4fe;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 11px;
  margin: 4px 0;
}

.mcp-skill-chip code {
  color: #c084fc;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.mcp-call-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(59, 130, 246, 0.15);
  color: #93c5fd;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 11px;
  margin: 6px 0;
}

.mcp-call-chip code {
  color: #60a5fa;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.mcp-voucher-preview {
  background: #090d16;
  border: 1px dashed rgba(56, 189, 248, 0.4);
  border-radius: 8px;
  padding: 10px 12px;
  margin: 8px 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11.5px;
  color: #e2e8f0;
}

.mcp-v-title {
  color: #38bdf8;
  font-weight: 700;
  margin-bottom: 6px;
}

.mcp-v-lines div {
  line-height: 1.5;
}

.mcp-safety-note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.2);
  border-radius: 4px;
  padding: 8px 10px;
  font-size: 11px;
  color: #cbd5e1;
  margin-top: 8px;
}

.mcp-safety-note i {
  margin-top: 1px;
}

.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

/* ── 痛点对比卡片 ── */
.section-compare {
  background: #f8fafc;
}

.compare-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 18px;
}

.compare-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 20px 18px;
  display: flex;
  flex-direction: column;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.8),
    0 2px 6px rgba(0, 0, 0, 0.02);
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}

.compare-card:hover {
  transform: translateY(-3px);
  border-color: rgba(23, 71, 176, 0.25);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 1),
    0 16px 32px -8px rgba(23, 71, 176, 0.1);
}

.compare-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.compare-dim {
  font-size: 13.5px;
  font-weight: 700;
  color: #0f172a;
}

.compare-badge {
  font-size: 11px;
  font-weight: 700;
  background: #edf3ff;
  color: #1747b0;
  padding: 2px 7px;
  border-radius: 999px;
}

/* 梭罗方案 */
.solo-solution {
  background: #f8fbff;
  border: 1.5px solid #1747b0;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 14px;
  box-shadow: 0 2px 8px rgba(23, 71, 176, 0.08);
}

.solo-solution-label {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 700;
  color: #1747b0;
  margin-bottom: 4px;
}

.solo-solution-text {
  font-size: 12.5px;
  font-weight: 600;
  color: #0f172a;
  line-height: 1.45;
}

/* 传统方案对比 */
.legacy-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: auto;
}

.legacy-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 8px;
  background: #f8fafc;
  border-radius: 4px;
}

.legacy-tag {
  font-size: 10.5px;
  color: #5f6e80;
  font-weight: 600;
}

.legacy-desc {
  font-size: 11.5px;
  color: #5b6a7c;
  line-height: 1.35;
}

/* ── 账本迁移：导入 / 导出双向互通 ── */
.section-migrate {
  background: #ffffff;
  border-top: 1px solid #e2e8f0;
}

.migrate-grid {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  gap: 20px;
  align-items: stretch;
}

.migrate-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 26px 24px;
  display: flex;
  flex-direction: column;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}

.migrate-card:hover {
  transform: translateY(-3px);
  border-color: rgba(23, 71, 176, 0.3);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 1),
    0 16px 32px -8px rgba(23, 71, 176, 0.12);
}

.migrate-head {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  margin-bottom: 18px;
}

.migrate-icon {
  width: 44px;
  height: 44px;
  flex-shrink: 0;
  border-radius: 12px;
  background: #edf3ff;
  color: #1747b0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  box-shadow: 0 2px 8px rgba(23, 71, 176, 0.1);
}

.migrate-out .migrate-icon {
  background: #eaf7f1;
  color: #0f7354;
  box-shadow: 0 2px 8px rgba(5, 150, 105, 0.12);
}

.migrate-title {
  margin: 0;
  font-size: 17px;
  font-weight: 800;
  color: #0f172a;
  letter-spacing: -0.2px;
}

.migrate-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.migrate-list li {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  font-size: 13px;
  line-height: 1.6;
  color: #475569;
}

.migrate-list li i {
  flex-shrink: 0;
  margin-top: 3px;
  font-size: 14px;
  color: #1747b0;
}

.migrate-out .migrate-list li i {
  color: #0f7354;
}

.migrate-list b {
  color: #0f172a;
  font-weight: 700;
}

.migrate-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: auto;
  padding-top: 18px;
}

.migrate-tag {
  font-size: 11.5px;
  font-weight: 600;
  color: #334155;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 4px 9px;
}

.migrate-bridge {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 56px;
}

.bridge-icon {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: #edf3ff;
  border: 1px solid rgba(23, 71, 176, 0.2);
  color: #1747b0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
}

.bridge-label {
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: #5f6e80;
  white-space: nowrap;
}

.migrate-promises {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  margin-top: 26px;
}

.promise-item {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 13px 16px;
  background: #f0f5ff;
  border: 1px solid rgba(23, 71, 176, 0.14);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 700;
  color: #0f172a;
}

.promise-item i {
  flex-shrink: 0;
  font-size: 15px;
  color: #1747b0;
}

.promise-text span {
  font-size: 11.5px;
  color: #5b6a7c;
}

/* ── 典型场景 ── */
.persona-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 18px;
}

.persona-item {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 20px 18px;
  display: flex;
  flex-direction: column;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.8),
    0 2px 6px rgba(0, 0, 0, 0.02);
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}

.persona-item:hover {
  transform: translateY(-3px);
  border-color: rgba(23, 71, 176, 0.25);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 1),
    0 16px 32px -8px rgba(23, 71, 176, 0.1);
}

.p-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}

.p-avatar {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: #edf3ff;
  color: #1747b0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  box-shadow: 0 2px 6px rgba(23, 71, 176, 0.1);
}

.p-role {
  margin: 0;
  font-size: 14px;
  font-weight: 750;
  color: #0f172a;
}

.p-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  padding: 8px 10px;
  background: #f8fafc;
  border-radius: 8px;
  margin-bottom: 14px;
}

.p-metric {
  display: flex;
  flex-direction: column;
}

.p-val {
  font-size: 16px;
  font-weight: 800;
  color: #1747b0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.p-lbl {
  font-size: 10.5px;
  color: #5b6a7c;
  margin-top: 2px;
}

.p-tags {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.p-tag {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  color: #475569;
}

.p-tag i {
  color: #0f7354;
}

/* ── 合规架构看板与放心报税佐证 ── */
.section-trust {
  padding: 80px 0;
  background: #ffffff;
  border-top: 1px solid #e2e8f0;
  border-bottom: 1px solid #e2e8f0;
}

/* ── 合规保障 3 大核心卡片 ── */
.compliance-evidence-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 24px;
  max-width: 1140px;
  margin: 0 auto;
}

.evidence-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 32px 26px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.02);
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

.evidence-card:hover {
  transform: translateY(-3px);
  background: #ffffff;
  border-color: rgba(23, 71, 176, 0.28);
  box-shadow: 0 16px 36px -8px rgba(23, 71, 176, 0.09);
}

.evidence-icon-wrap {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: #edf3ff;
  color: #1747b0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  margin-bottom: 18px;
  box-shadow: 0 2px 6px rgba(23, 71, 176, 0.1);
}

.evidence-title {
  margin: 0 0 10px;
  font-size: 18px;
  font-weight: 750;
  color: #0f172a;
  letter-spacing: -0.3px;
}

.evidence-desc {
  margin: 0 0 20px;
  font-size: 13.5px;
  color: #5b6a7c;
  line-height: 1.65;
  flex: 1;
}

.evidence-points {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding-top: 16px;
  border-top: 1px solid #e2e8f0;
}

.evidence-point-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: #0f7354;
}

.evidence-point-tag i {
  font-size: 13.5px;
  font-weight: bold;
}

/* ── 最后一页：底部 CTA + 页脚整合 (Slide 7 · 首尾呼应) ── */
.landing-slide-end {
  min-height: calc(100vh - 56px);
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  scroll-snap-align: start;
  scroll-snap-stop: normal;
  position: relative;
  overflow: hidden;
  border-top: 1px solid #e2e8f0;
  background:
    radial-gradient(circle at 50% 45%, rgba(23, 71, 176, 0.08) 0%, rgba(30, 127, 251, 0.02) 45%, transparent 70%),
    linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
}

.landing-slide-end .cta-banner {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 80px 0 40px;
  position: relative;
}

.cta-glow-mesh {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 900px;
  height: 500px;
  background: radial-gradient(circle, rgba(23, 71, 176, 0.07) 0%, rgba(30, 127, 251, 0.02) 50%, transparent 70%);
  pointer-events: none;
}

.cta-inner {
  position: relative;
  z-index: 1;
  max-width: 680px;
  margin: 0 auto;
  padding: 0 24px;
}

.cta-title {
  margin: 0 0 14px;
  font-size: 38px;
  font-weight: 850;
  color: #0f172a;
  letter-spacing: -0.8px;
  line-height: 1.25;
}

.cta-sub {
  margin: 0 0 32px;
  font-size: 16px;
  color: #5b6a7c;
  line-height: 1.6;
}

.cta-buttons {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  flex-wrap: wrap;
}

/* ── 页脚 ── */
.site-footer {
  background: transparent;
  padding: 28px 0 24px;
  border-top: 1px solid #f1f5f9;
  position: relative;
  z-index: 1;
}

.footer-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
}

.footer-brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.footer-logo {
  width: 32px;
  height: 32px;
  border-radius: 8px;
}

.footer-brand-info b {
  display: block;
  font-size: 14px;
  color: #0f172a;
}

.footer-brand-info p {
  margin: 2px 0 0;
  font-size: 11.5px;
  color: #5b6a7c;
}

.footer-nav {
  display: flex;
  gap: 20px;
}

.footer-nav a {
  font-size: 13px;
  color: #475569;
  text-decoration: none;
  transition: color 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.footer-nav a:hover {
  color: #1747b0;
}

.footer-sub {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid #f1f5f9;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11.5px;
  color: #5f6e80;
}

.footer-sub a {
  color: #5f6e80;
  text-decoration: none;
}

.footer-filings {
  display: flex;
  align-items: center;
  gap: 16px;
}

/* ── 响应式适配 ── */
/* 中窄屏：收紧导航间距并隐藏副标题，避免链接区被挤压 */
@media (max-width: 1200px) {
  .nav-inner {
    gap: 16px;
  }

  .nav-links {
    gap: 14px;
  }

  .nav-sub {
    display: none;
  }
}

@media (max-width: 1024px) {
  .nav-inner {
    padding: 0 16px;
  }

  .hero-inner {
    padding: 60px 20px 0;
  }

  .hero-title {
    font-size: 44px;
  }

  .hero-proof-bento {
    grid-template-columns: repeat(2, 1fr);
    gap: 14px;
  }

  .pillars-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .stage-grid {
    grid-template-columns: 1fr;
    gap: 36px;
  }

  .showcase-nav-bar {
    overflow-x: auto;
    justify-content: flex-start;
    padding: 6px;
  }

  .showcase-tab-btn {
    white-space: nowrap;
    flex-shrink: 0;
  }

  .compare-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .persona-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .compliance-evidence-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  :global(html:has(.landing-page)),
  :global(html.landing-scroll-snap) {
    scroll-snap-type: none;
  }

  .hero,
  .section,
  .landing-slide-end {
    min-height: auto;
    padding: 56px 0;
  }

  .migrate-grid {
    grid-template-columns: 1fr;
  }

  .migrate-bridge {
    width: auto;
    flex-direction: row;
    gap: 10px;
  }

  .migrate-promises {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .nav-links {
    display: none;
  }

  .hero-title {
    font-size: 32px;
    letter-spacing: -0.6px;
  }

  .hero-br-mobile {
    display: inline;
  }

  .hero-sub {
    font-size: 15px;
    margin-bottom: 28px;
  }

  .hero-action-block {
    margin-bottom: 36px;
  }

  .hero-proof-bento {
    grid-template-columns: 1fr;
  }

  .stage-title {
    font-size: 24px;
  }

  .pillars-grid,
  .compare-grid,
  .persona-grid,
  .board-metrics-grid {
    grid-template-columns: 1fr;
  }

  .hero-cta button,
  .cta-buttons button {
    width: 100%;
    justify-content: center;
  }
}
</style>
