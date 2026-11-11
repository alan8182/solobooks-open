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

// 移动端折叠抽屉与吸底操作栏状态
const mobileMenuOpen = ref(false)
const showStickyCta = ref(false)

function navigateToAnchor(hash: string) {
  mobileMenuOpen.value = false
  const target = document.querySelector(hash)
  if (target) {
    const top = target.getBoundingClientRect().top + window.scrollY - NAV_HEIGHT
    window.scrollTo({ top, behavior: 'smooth' })
  }
}

function onLandingScroll() {
  showStickyCta.value = window.scrollY > 480
}

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
  window.addEventListener('scroll', onLandingScroll, { passive: true })
})

onUnmounted(() => {
  document.documentElement.classList.remove('landing-scroll-snap')
  window.removeEventListener('wheel', onLandingWheel)
  window.removeEventListener('scroll', onLandingScroll)
  clearTimeout(flipLockTimer)
})

const isLoggedIn = computed(() => Boolean(localStorage.getItem('auth_token')))

// ponytail: 遵循极简主义与 YAGNI 原则，页面轻盈无外部重量库负担

// ── 四大核心支柱 ──
const fourPillars = [
  {
    num: '01',
    title: '一键确认入账',
    desc: '流水与发票智能勾稽，AI 预制入账分录，老板确认即可入账，零会计门槛。',
    chips: ['免记借贷分录', '人机双签确认', '企银/微信自动对平'],
    icon: 'ri-cursor-line',
  },
  {
    num: '02',
    title: '随时财税咨询',
    desc: '合同节税、个人垫资转回等疑难，大白话发问，结合账本给出具体合规方案。',
    chips: ['联动真实账目', '合同节税建议', '金税四期避坑'],
    icon: 'ri-chat-smile-3-line',
  },
  {
    num: '03',
    title: '公私垫资隔离',
    desc: '建立合规垫资冲销通道，避免大额借款挂账，彻底排除 20% 股东借款个税风险。',
    chips: ['垫资免税冲销', '股东借款预警', '0违规挂账'],
    icon: 'ri-shield-star-line',
  },
  {
    num: '04',
    title: '标准财务报表',
    desc: '严格遵循小企业会计准则，资产负债表与利润表实时联动，支持只读协审分享。',
    chips: ['小企业准则科目', '一键协审链接', '账套全量导出'],
    icon: 'ri-medal-line',
  },
]

// ── 账本迁移 ──
const migrateIn = {
  title: '前任代账账套 · 一键体检洗净',
  icon: 'ri-radar-line',
  items: [
    { b: '历史糊涂账排雷', t: '导入科目余额表，AI 自动扫描大额往来挂账与公私混同隐患' },
    { b: '费用科目重梳理', t: '纠正粗暴计入管理费用的支出，还原企业真实净利润' },
    { b: '流水发票免模板', t: '企银流水、商户账单、税局发票直接拖入，无缝开新账' },
  ],
  tags: ['Excel / CSV 直导', '历史风险扫描', '无缝平滑切换'],
}

const migrateOut = {
  title: '标准规范账套 · 随时交付',
  icon: 'ri-links-line',
  items: [
    { b: 'Solo Audit Share', t: '一键签发只读协审链接，银行与投资人免注册在线验账' },
    { b: '三大报表与明细', t: '科目余额表、明细账、资产负债表与利润表一键导出' },
    { b: '数据绝不锁定', t: '完整账套与原始凭证票据整包带走，所有权 100% 归你' },
  ],
  tags: ['一键协审链接', '准则报表直出', '数据完全归你'],
}

const migratePromises = [
  { icon: 'ri-stethoscope-line', title: '迁入即享账本风险体检' },
  { icon: 'ri-share-forward-box-line', title: '一键签发银行/投资人只读链接' },
  { icon: 'ri-shield-check-line', title: '数据所有权 100% 归你' },
]

// ── 展台导航 ──
type ShowcaseKey = 'batch' | 'reconcile' | 'closing' | 'dashboard' | 'mcp'
const activeShowcase = ref<ShowcaseKey>('batch')

const showcaseTabs = [
  { key: 'batch' as ShowcaseKey, num: '01', title: '智能做账', icon: 'ri-cursor-line' },
  { key: 'reconcile' as ShowcaseKey, num: '02', title: '财税咨询', icon: 'ri-chat-voice-line' },
  { key: 'closing' as ShowcaseKey, num: '03', title: '月末结账', icon: 'ri-calendar-check-line' },
  { key: 'dashboard' as ShowcaseKey, num: '04', title: '经营看板 & 协审', icon: 'ri-medal-line' },
  { key: 'mcp' as ShowcaseKey, num: '05', title: 'AI 连接 (MCP & Skills)', icon: 'ri-plug-line' },
]

function switchShowcaseTab(key: ShowcaseKey) {
  activeShowcase.value = key
}

// ── 方案对比 ──
const comparisons = [
  {
    dim: '账本质量',
    hired: '账目规范，但年薪成本 8~15 万',
    agency: '科目混乱乱挂往来，报表失真',
    solo: '严格遵循《小企业会计准则》，科目规范不乱挂',
    highlight: '准则合规',
  },
  {
    dim: '财税答疑',
    hired: '依赖个别会计经验',
    agency: '1 人管 200 家，回复迟缓，只管抄税',
    solo: '7×24h 随时问，结合公司真实账目给具体合规方案',
    highlight: '随时在线',
  },
  {
    dim: '经营掌控',
    hired: '报表晦涩难懂，沟通成本高',
    agency: '账本是黑盒，真实赚多少、欠多少看不懂',
    solo: 'AI 预制方案，老板点一点确认，看清净利与现金',
    highlight: '一目了然',
  },
  {
    dim: '数据自主权',
    hired: '人员交接易断层，历史账目难追溯',
    agency: '账套数据被中介扣留，更换代账扯皮繁琐',
    solo: '数据 100% 归企业，账套凭证随时完整导出，绝不锁定',
    highlight: '自主可控',
  },
]

// ── 适用场景 ──
const personaChips = [
  {
    role: '出海贸易与跨境电商',
    icon: 'ri-global-line',
    metrics: [{ val: '10 秒', label: '账单自动导入' }, { val: '100%', label: '流水对平闭环' }],
    tags: ['多平台账单对平', '分期回款自动核销', '汇兑流水清晰留痕'],
  },
  {
    role: '设计与咨询工作室',
    icon: 'ri-palette-line',
    metrics: [{ val: '0 风险', label: '个人垫资免税冲销' }, { val: '透明', label: '合伙人分红对账' }],
    tags: ['项目采买垫资隔离', '阶段收款核对', '合伙人透明分红'],
  },
  {
    role: '技术团队与一人公司',
    icon: 'ri-code-s-slash-line',
    metrics: [{ val: '0 门槛', label: '一键确认入账' }, { val: 'MCP', label: 'Claude / Agent 直连' }],
    tags: ['云服务器与算力归集', '无形资产合规摊销', 'MCP 自动化做账'],
  },
  {
    role: '新零售与现代主理人',
    icon: 'ri-store-2-line',
    metrics: [{ val: '98 分', label: '账本健康度' }, { val: '0 挂账', label: '借款红线清零' }],
    tags: ['垫资免税转回', '避开 20% 视同分红税', '银行经营贷标准账表'],
  },
]

// ── 合规与数据安全 ──
const complianceEvidence = [
  {
    icon: 'ri-shield-keyhole-line',
    title: '仅本人可访问 · 多层防护',
    desc: '仅本人账号授权方可访问账本。手机动态核验、微信防冒用与多层账号防护，底层行级强隔离防穿透。',
    points: ['仅本人账号授权访问', '多层防护与防穿透隔离'],
  },
  {
    icon: 'ri-robot-2-line',
    title: 'AI 不用企业数据训练',
    desc: '对接 DeepSeek 等商用专线 API，财务流水绝不进入公网大模型训练集；AI 仅出草稿，人机双签。',
    points: ['商用 API 绝不入训练集', 'AI 仅出草稿不越权'],
  },
  {
    icon: 'ri-lock-2-line',
    title: '全链路双向加密与留痕',
    desc: '全站强制 TLS 1.3 双向传输加密与核心密钥密文存储；每笔操作时间戳留痕，对外查验支持只读脱敏。',
    points: ['全链路双向传输加密', '审计留痕与只读脱敏'],
  },
  {
    icon: 'ri-database-2-line',
    title: '数据自主，绝不锁客',
    desc: '账套资产 100% 归企业所有。随时一键导出带数据指纹的 Excel 账套与凭证，自由迁移无后顾之忧。',
    points: ['带指纹标准账套导出', '不锁客随时自由迁移'],
  },
]

// ── 开源社区与商业定价方案 ──
const copiedDocker = ref(false)
const dockerCmd = 'docker run -d -p 8000:8000 --name solobooks jiezixingchen/solobooks:latest'

function copyDockerCmd() {
  navigator.clipboard?.writeText(dockerCmd)
  copiedDocker.value = true
  setTimeout(() => {
    copiedDocker.value = false
  }, 2500)
}

function handlePricingAction(action: string) {
  if (action === 'github') {
    window.open('https://github.com/jiezixingchen/solobooks-open', '_blank')
  } else if (action === 'contact') {
    router.push('/login?plan=pro')
  } else {
    router.push('/login?plan=cloud')
  }
}

interface PricingFeature {
  text: string
  included: boolean
  note?: string
}

interface PricingTier {
  key: string
  badge: string
  badgeType: 'neutral' | 'primary' | 'premium'
  name: string
  target: string
  price: string
  unit: string
  popular?: boolean
  desc: string
  features: PricingFeature[]
  btnText: string
  btnAction: string
}

const pricingTiers: PricingTier[] = [
  {
    key: 'openSource',
    badge: '开源免费 · 本地自建',
    badgeType: 'neutral',
    name: '开源社区版',
    target: '适合极客与技术团队自建',
    price: '0',
    unit: '永久免费',
    desc: '复式记账内核与算法全公开，数据留在自己硬盘，断网可用、绝不锁客。',
    features: [
      { text: '标准复式记账内核，自动生成三表与科目余额表', included: true },
      { text: 'Docker 一键部署，数据 100% 存本地', included: true },
      { text: '手工凭证记账与带指纹标准账套导出', included: true },
      { text: '支持接入自有大模型 API Key 辅助分录', included: true },
      { text: '全电发票与银行流水自动解析', included: false, note: '需手工录入' },
      { text: '多端实时云同步与免运维托管', included: false, note: '单机离线' },
    ],
    btnText: '查看 GitHub 源码',
    btnAction: 'github',
  },
  {
    key: 'cloud',
    badge: '开箱即用 · 每日仅 ¥1.6',
    badgeType: 'primary',
    name: '商业云端版',
    target: '一人公司 · 独立主理人 · 初创小微',
    price: '598',
    unit: '元 / 年',
    popular: true,
    desc: '免服务器与运维配置。流水发票全自动导入，内置 AI 财务官，省时省心。',
    features: [
      { text: '免买云服务器，Web 与移动端实时云同步', included: true },
      { text: '流水自适应解析：银行流水 + 微信/支付宝商业账单', included: true },
      { text: '发票批量 OCR 识别，智能提取税额并匹配入账', included: true },
      { text: '老板私卡垫资与免税提现冲销台账（规避 20% 个税风险）', included: true },
      { text: '内置开箱即用 AI 财务官（免配置 Key，全天候答疑）', included: true },
      { text: '电子税局申报小抄模式，辅助核对申报数据', included: true },
    ],
    btnText: '开启商业云端版',
    btnAction: 'login',
  },
  {
    key: 'pro',
    badge: '持牌财税机构联审',
    badgeType: 'premium',
    name: '人机双签护航版',
    target: '流水稳定 · 规避申报失误',
    price: '1,980',
    unit: '元 / 年',
    popular: false,
    desc: 'AI 自动化做账 + 持牌财税机构资深会计师季度复核把关，彻底打消申报顾虑。',
    features: [
      { text: '包含 598 云端版全部自动化功能与 AI 算力', included: true },
      { text: '持牌财税机构资深会计师季度电子账复核并出具报告', included: true },
      { text: '电子税务局季度申报代操 / 直连通道', included: true },
      { text: '财税专家线上排雷门诊（重点排查往来借款与发票链）', included: true },
      { text: '研发费用辅助账归集与融资贷款脱敏协审空间', included: true },
    ],
    btnText: '预约专家护航',
    btnAction: 'contact',
  },
]
</script>

<template>
  <div class="landing-page">
    <!-- 1. 顶部导航：简单吸顶通栏 -->
    <header class="site-nav">
      <div class="nav-inner">
        <a class="nav-brand" href="#" @click.prevent="router.push('/')">
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
          <a href="/audit-scan" @click.prevent="router.push('/audit-scan')">AI 账本体检</a>
          <a href="#compare">方案对比</a>
          <a href="#story">初衷手记</a>
          <a href="#migrate">账本迁移</a>
          <a href="#personas">适用场景</a>
          <a href="#trust">合规与安全</a>
          <a href="#pricing">开源与定价</a>
        </nav>
        <div class="nav-actions">
          <NButton quaternary size="small" class="desktop-action-btn" @click="router.push('/landing')">
            对比原版官网
          </NButton>
          <template v-if="isLoggedIn">
            <NButton type="primary" size="small" class="btn-primary desktop-action-btn" @click="router.push('/')">
              进入工作台
            </NButton>
          </template>
          <template v-else>
            <NButton type="primary" size="small" class="btn-primary desktop-action-btn" @click="router.push('/login')">
              登录 / 注册
            </NButton>
          </template>
          <!-- 移动端汉堡切换按钮 -->
          <button
            class="mobile-menu-btn"
            :class="{ open: mobileMenuOpen }"
            :aria-label="mobileMenuOpen ? '关闭导航菜单' : '打开导航菜单'"
            @click="mobileMenuOpen = !mobileMenuOpen"
          >
            <i :class="mobileMenuOpen ? 'ri-close-line' : 'ri-menu-line'" />
          </button>
        </div>
      </div>

      <!-- 移动端折叠抽屉菜单 -->
      <Transition name="mobile-drawer">
        <div v-if="mobileMenuOpen" class="mobile-drawer-backdrop" @click="mobileMenuOpen = false">
          <div class="mobile-drawer" @click.stop>
            <div class="mobile-drawer-links">
              <a href="#pillars" @click.prevent="navigateToAnchor('#pillars')">
                <i class="ri-layout-grid-line" />
                <span>核心优势</span>
              </a>
              <a href="#showcase" @click.prevent="navigateToAnchor('#showcase')">
                <i class="ri-layout-grid-line" />
                <span>功能展台</span>
              </a>
              <a href="/ai-connect" @click.prevent="mobileMenuOpen = false; router.push('/ai-connect')">
                <i class="ri-terminal-box-line" />
                <span>AI 连接 (MCP & Skills)</span>
                <span class="drawer-badge">新</span>
              </a>
              <a href="/audit-scan" @click.prevent="mobileMenuOpen = false; router.push('/audit-scan')">
                <i class="ri-shield-flash-line" />
                <span>AI 账本体检</span>
                <span class="drawer-badge highlight">免费</span>
              </a>
              <a href="#compare" @click.prevent="navigateToAnchor('#compare')">
                <i class="ri-scales-3-line" />
                <span>方案对比</span>
              </a>
              <a href="#story" @click.prevent="navigateToAnchor('#story')">
                <i class="ri-quill-pen-line" />
                <span>初衷手记</span>
              </a>
              <a href="#migrate" @click.prevent="navigateToAnchor('#migrate')">
                <i class="ri-exchange-box-line" />
                <span>账本迁移</span>
              </a>
              <a href="#personas" @click.prevent="navigateToAnchor('#personas')">
                <i class="ri-user-star-line" />
                <span>适用场景</span>
              </a>
              <a href="#trust" @click.prevent="navigateToAnchor('#trust')">
                <i class="ri-shield-check-line" />
                <span>合规与安全</span>
              </a>
              <a href="#pricing" @click.prevent="navigateToAnchor('#pricing')">
                <i class="ri-price-tag-3-line" />
                <span>开源与定价</span>
              </a>
            </div>
            <div class="mobile-drawer-bottom">
              <button
                class="mobile-drawer-login-btn"
                @click="mobileMenuOpen = false; router.push(isLoggedIn ? '/' : '/login')"
              >
                {{ isLoggedIn ? '进入工作台' : '免费注册 / 登录' }}
                <i class="ri-arrow-right-line" />
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </header>

    <!-- 2. Hero 区域：清爽精简版 -->
    <section class="hero">
      <div class="hero-glow" />
      <div class="hero-inner">
        <!-- 身份胶囊 -->
        <div class="hero-kicker-pill">
          <span>驻场 AI 财务官 · 专为小微与一人公司设计</span>
        </div>

        <!-- 主标题 -->
        <h1 class="hero-title">
          点一点把账做清爽，<br class="hero-br-mobile" />
          <span class="title-highlight">问一问把公司养值钱。</span>
        </h1>

        <!-- 价值副标：干净短促，绝不拖泥带水 -->
        <p class="hero-sub">
          日常账务一键确认，财税疑难随时提问。<br class="desktop-only" />
          告别外包糊涂账与公私混同风险，做一本科目规范、借贷平衡的干净账。
        </p>

        <!-- 行动按钮 -->
        <div class="hero-action-block">
          <div class="hero-cta">
            <button class="island-cta-btn" @click="router.push('/login')">
              <span>免费开启干净账本</span>
              <span class="btn-arrow-icon"><i class="ri-arrow-right-line" /></span>
            </button>
            <button class="hero-scan-btn" @click="router.push('/audit-scan')">
              <i class="ri-shield-flash-line" />
              <span>30秒 AI 账本体检</span>
            </button>
          </div>
        </div>

        <!-- 底部 4 张 Bento 卡片：干练清晰 -->
        <div class="hero-proof-bento">
          <div class="proof-card">
            <div class="proof-card-top">
              <div class="proof-icon-wrap"><i class="ri-cursor-line" /></div>
              <span class="proof-badge blue">零门槛</span>
            </div>
            <div class="proof-val">点一点 · 问一问</div>
            <div class="proof-title">不懂会计也能管好账</div>
            <p class="proof-desc">AI 预制入账方案，老板只需确认；疑难问题随时大白话提问。</p>
          </div>

          <div class="proof-card">
            <div class="proof-card-top">
              <div class="proof-icon-wrap"><i class="ri-shield-keyhole-line" /></div>
              <span class="proof-badge emerald">公私防雷</span>
            </div>
            <div class="proof-val">公私合规隔离</div>
            <div class="proof-title">杜绝 20% 股东借款个税</div>
            <p class="proof-desc">个人垫资合规冲销，避免公私混同被税局误判为股东分红。</p>
          </div>

          <div class="proof-card">
            <div class="proof-card-top">
              <div class="proof-icon-wrap"><i class="ri-medal-line" /></div>
              <span class="proof-badge amber">信用资产</span>
            </div>
            <div class="proof-val">小企业准则</div>
            <div class="proof-title">标准科目 · 双向留痕</div>
            <p class="proof-desc">遵循小企业会计准则，资产负债表与利润表规范联动，凭证回单双向穿透。</p>
          </div>

          <div class="proof-card hover-glow" @click="router.push('/ai-connect')">
            <div class="proof-card-top">
              <div class="proof-icon-wrap"><i class="ri-terminal-box-line" /></div>
              <span class="proof-badge purple">开放协议</span>
            </div>
            <div class="proof-val text-gradient">MCP & Skills</div>
            <div class="proof-title">Claude / Cursor / Agent 直连</div>
            <p class="proof-desc">27 项原生账务工具与报税 Skills，人机双签，只出草稿不越权。</p>
          </div>
        </div>
      </div>
    </section>

    <!-- 3. 四大核心支柱总览 (Four Pillars) -->
    <section id="pillars" class="section section-pillars">
      <div class="section-inner">
        <div class="section-top text-center">
          <p class="section-tag">核心优势</p>
          <h2 class="section-headline">四个底气，告别外包糊涂账</h2>
          <p class="section-subtitle">
            告别外包糊涂账，做一本规范透明、经得起查验的干净账本。
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
          <h2 class="section-headline">极简流程，财务一目了然</h2>
          <p class="section-subtitle">
            从日常流水对平、财税咨询，到期末报税与银行协审。
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
            <!-- Tab 01: 点一点 · 智能管账 -->
            <div v-if="activeShowcase === 'batch'" key="batch" class="stage-grid">
              <div class="stage-left">
                <h3 class="stage-title">流水发票一拖即入，<br />确认即可入账</h3>
                <p class="stage-desc">
                  无需繁琐记账。月末将银行流水、发票包与平台账单拖入，AI 自动预制分录，老板确认即完成入账。
                </p>
                <div class="stage-bullets">
                  <div class="stage-bullet"><i class="ri-checkbox-circle-line" /><span><b>① 硬规则直解</b>：税费、手续费等确定性项精准硬确认，零误差不走大模型</span></div>
                  <div class="stage-bullet"><i class="ri-sparkling-line" /><span><b>② AI 语义推导</b>：依据交易对手与备注推导分录给出依据，人工点一点确认</span></div>
                  <div class="stage-bullet"><i class="ri-brain-line" /><span><b>③ 专属记忆沉淀</b>：纠正一次即永久沉淀为企业专属规则，系统越用越准</span></div>
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

                    <!-- AI 财务专家入账建议气泡 -->
                    <div class="agent-advice-bubble">
                      <div class="advice-head">
                        <i class="ri-lightbulb-flash-line text-amber" />
                        <span class="advice-title">AI 财务官入账建议</span>
                        <span class="advice-badge">合规节税推荐</span>
                      </div>
                      <p class="advice-content">
                        💡 识别到技术服务采购 ¥30,000.00，建议适用小微增值税 1% 优惠入账，预计当期节税 ¥600.00。
                      </p>
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
                        <span class="btn-confirm-demo"><i class="ri-cursor-fill" /> 点一点采纳</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Tab 02: 问一问 · 随身财务顾问 -->
            <div v-else-if="activeShowcase === 'reconcile'" key="reconcile" class="stage-grid">
              <div class="stage-left">
                <h3 class="stage-title">财税疑难随时发问，<br />给出合规方案</h3>
                <p class="stage-desc">
                  垫资怎么拿回？合同怎么签省税？随时提问，AI 结合公司真实账面给出清晰合规路径。
                </p>
                <div class="stage-bullets">
                  <div class="stage-bullet"><i class="ri-check-line" /><span>联动真实账本数据，量身定制合规方案</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span>金税四期口径预警，拦截公私混同与 20% 分红个税雷区</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span>咨询方案一键转化为合规凭据草稿，闭环落地</span></div>
                </div>
              </div>
              <div class="stage-right">
                <div class="mockup-window">
                  <div class="window-bar">
                    <div class="window-dots"><span class="dot red" /><span class="dot yellow" /><span class="dot green" /></div>
                    <span class="window-title">SoloBooks AI 财务军师 · 实时咨询工作台</span>
                    <span class="window-status text-emerald"><i class="ri-checkbox-circle-line" /> 7×24h 在线解答</span>
                  </div>
                  <div class="window-content">
                    <div class="advisor-dialog-body">
                      <div class="advisor-msg user">
                        <div class="msg-avatar"><i class="ri-user-smile-line" /></div>
                        <div class="msg-bubble">
                          <div class="msg-author">创始人（你）</div>
                          <div class="msg-text">我用个人卡替公司垫了 3.8 万元采购设备，怎么转回给我合规不被查税？</div>
                        </div>
                      </div>
                      <div class="advisor-msg assistant">
                        <div class="msg-avatar"><i class="ri-chat-3-line" /></div>
                        <div class="msg-bubble">
                          <div class="msg-author">SoloBooks AI 财务官 · 实时处方</div>
                          <div class="advisor-warning-alert">
                            <i class="ri-alert-fill" />
                            <span><b>风险拦截：</b>切勿直接由公司转账给个人并挂“其他应收款”，税局认定视同分红将面临 20%（¥7,600）个税风险。</span>
                          </div>
                          <div class="msg-text">
                            <b>合规解决路径：</b><br />
                            走系统【垫资冲销通道】，凭个人付款凭据与设备发票入账冲销，公户附言“还垫资款”转回个人，合法免税。
                          </div>
                          <div class="advisor-action-row">
                            <span class="btn-advisor-action"><i class="ri-file-add-line" /> 一键生成垫资冲销单</span>
                            <span class="advisor-action-tag"><i class="ri-shield-check-line" /> 0个税风险 · 凭证合规冲平</span>
                          </div>
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
                <h3 class="stage-title">4 步向导结账，<br />报税数据直出</h3>
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
                  <div class="stage-bullet"><i class="ri-check-line" /><span>研发费用辅助账自动归集：基于入驻园区研发补助实战，流水工时自动打标，轻松申报科小与税惠</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span>真实净利润与现金流跑道测算，应收账款账龄智能预警</span></div>
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

                    <div class="audit-share-banner">
                      <div class="share-icon-wrap"><i class="ri-share-forward-box-line" /></div>
                      <div class="share-info">
                        <b>Solo Audit Share 专属协审链接</b>
                        <span>https://solobooks.net/share/cpa_9a8f27... (投资人与银行免注册只读在线验账)</span>
                      </div>
                      <span class="btn-copy-share"><i class="ri-file-copy-line" /> 复制链接</span>
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
                  <div class="stage-bullet"><i class="ri-check-line" /><span><b>27 个原生 MCP 工具</b>：覆盖记账起草、三方流水对账、结账五项检查、申报作业包与资产台账</span></div>
                  <div class="stage-bullet"><i class="ri-check-line" /><span><b>专属 Agent Skills 技能包</b>：挂载半自动报税伴侣，一键生成报税数据、识别无票收入、引导申报表填写</span></div>
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
                        帮我准备 Q3 增值税与所得税申报包，并核对开票数据是否一致。
                      </div>
                    </div>

                    <!-- AI 响应 -->
                    <div class="chat-bubble assistant-bubble">
                      <div class="bubble-sender">
                        <i class="ri-chat-3-line text-purple" /> Claude Assistant (via SoloBooks MCP & Skills)
                      </div>

                      <div class="mcp-skill-chip">
                        <i class="ri-tools-line" />
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
                        <i class="ri-file-chart-line" />
                        <span>生成报税数据：<code>增值税及附加税费申报表 + 企业所得税预缴申报表</code></span>
                      </div>
                      <div class="mcp-voucher-preview">
                        <div class="mcp-v-title">🎯 Q3 报税数据包已生成</div>
                        <div class="mcp-v-lines">
                          <div>• 开票销售额合计：¥245,800.00（与账套发票池 100% 勾稽一致）</div>
                          <div>• 增值税：小微免征，免税销售额 ¥245,800.00 已按申报表口径列示</div>
                          <div>• 企业所得税：实际利润额与应补税额 ¥3,410.00 已生成，可直接对照电子税务局填写</div>
                        </div>
                      </div>
                      <div class="mcp-safety-note">
                        <i class="ri-shield-keyhole-line text-emerald" />
                        <span><b>人机双签安全红线：</b>AI 只生成报税数据，不代客申报。数据已核验无误，请在电子税务局由您本人核对后亲手提交。</span>
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
          <p class="section-tag">合规优先 · 方案对比</p>
          <h2 class="section-headline">为什么不用传统代账？</h2>
          <p class="section-subtitle">
            金税四期“以数治税、穿透监管”，过去廉价代账留下的公私不分与糊涂烂账成了定时炸弹。规范透明，才是小微企业的立身之本。
          </p>
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
                梭罗 AI 财务官
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

    <!-- 5.5 创始人手记：为什么我们要做梭罗记账？ (Origin Story) -->
    <section id="story" class="section section-story">
      <div class="section-inner">
        <div class="story-card">
          <div class="story-header text-center">
            <span class="story-badge">
              <i class="ri-quill-pen-line" />
              <span>创始人真实手记</span>
            </span>
            <h2 class="story-title">原本，这只是我们写给自己用的工具</h2>
            <p class="story-sub">
              从评选入驻政府科技园区、获得官方研发补助支持，到踩遍小微记账的所有坑——这是一位创业者做给更多创业者的干净账税系统。
            </p>
          </div>

          <div class="story-grid">
            <!-- 左侧：亲身踩过的三难困境 -->
            <div class="story-pane story-pain">
              <div class="pane-head">
                <span class="pane-tag pain-tag">切身体会的小微三难</span>
                <h3 class="pane-title">找代账糊涂、自记账繁琐、招会计太贵</h3>
              </div>
              <div class="story-pains-list">
                <div class="pain-box">
                  <div class="pain-icon-wrap"><i class="ri-close-line" /></div>
                  <div class="pain-content">
                    <b>找过外部廉价代账</b>
                    <p>200 块一个月，只管填表不管账，发票乱塞、往来乱挂。金税四期大数据一穿透，全是随时暴雷的个税与补税炸弹；</p>
                  </div>
                </div>
                <div class="pain-box">
                  <div class="pain-icon-wrap"><i class="ri-close-line" /></div>
                  <div class="pain-content">
                    <b>用过市面自记账产品</b>
                    <p>满屏都是死板复杂的会计术语和大表格，既不专业又难用，根本无法长期坚持；</p>
                  </div>
                </div>
                <div class="pain-box">
                  <div class="pain-icon-wrap"><i class="ri-close-line" /></div>
                  <div class="pain-content">
                    <b>想招专职会计坐班</b>
                    <p>一年 8~12 万的刚性成本，对于初创小团队、一人公司来说，养专职财务实在太重太奢侈。</p>
                  </div>
                </div>
              </div>
            </div>

            <!-- 右侧：自己造工具 + 专家打磨 + 开放共创 -->
            <div class="story-pane story-resolve">
              <div class="pane-head">
                <span class="pane-tag resolve-tag">从自用到开放共创</span>
                <h3 class="pane-title">索性自己造，并与专业财务反复打磨</h3>
              </div>
              <div class="story-resolve-text">
                <p>
                  既然市面上没有称心的，<b>我们索性结合工程师的自动化能力，为自己开发了这套系统</b>。
                  后来我们找了资深专业财务朋友，严格对照《小企业会计准则 (2013)》反复打磨：从硬规则直解、AI推导到规则记忆，把底层借贷平衡、已过账只准红冲、私卡垫资隔离等专业规矩一件件焊死在底层。
                </p>
                <p>
                  身边很多创业朋友、独立主理人看我们用得顺畅，纷纷问能不能用。我们才决定<b>开源复式记账内核破除锁客，正式上线商业云端版</b>。
                </p>
                <p class="resolve-highlight">
                  目前系统正在面向技术、设计、咨询、电商、实体等更多行业深度测试。我们真诚欢迎你来试用，并给出宝贵的使用反馈与批评意见。
                </p>
              </div>

              <!-- 背书信赖胶囊条 -->
              <div class="story-badges-row">
                <div class="s-badge-item">
                  <i class="ri-government-line text-blue" />
                  <span>政府园区入驻评审</span>
                </div>
                <div class="s-badge-item">
                  <i class="ri-funds-box-line text-emerald" />
                  <span>科技创新研发补助支持</span>
                </div>
                <div class="s-badge-item">
                  <i class="ri-shield-check-line text-indigo" />
                  <span>资深 CPA 联合打磨</span>
                </div>
                <div class="s-badge-item">
                  <i class="ri-user-smile-line text-amber" />
                  <span>多行业创业者测试中</span>
                </div>
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
          <h2 class="section-headline">前任代账账套，<br class="hero-br-mobile" />一键导入洗干净</h2>
          <p class="section-subtitle">
            直接导入历史科目余额表，AI 自动扫描挂账风险；账套随时完整导出，绝不锁定。
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
          <h2 class="section-headline">适合谁用？</h2>
          <p class="section-subtitle">专为需要账目清爽、合规规范的小微团队与一人公司设计。</p>
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

    <!-- 8. 安全与合规保障 (Slide 8) -->
    <section id="trust" class="section section-trust">
      <div class="section-inner">
        <div class="section-top text-center">
          <p class="section-tag">数据安全与合规</p>
          <h2 class="section-headline">企业级数据安全，底线经得起查验</h2>
          <p class="section-subtitle">
            全链路双向加密、多层账号防护仅本人可访问、AI 商业数据绝不入训练集。
          </p>
        </div>

        <!-- 4 大核心安全与合规卡片 -->
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

    <!-- 9. 开源社区与商业定价方案 (Pricing) -->
    <section id="pricing" class="section section-pricing">
      <div class="section-inner">
        <div class="section-top text-center">
          <p class="section-tag">方案与定价</p>
          <h2 class="section-headline">你可以把数据留在自己电脑里，也可以交给我们全自动打理</h2>
          <p class="section-subtitle">
            核心代码公开，数据自主不锁客；或直接开启开箱即用的云端全自动记账。
          </p>
        </div>

        <div class="pricing-grid">
          <div
            v-for="tier in pricingTiers"
            :key="tier.key"
            class="pricing-card"
            :class="{ 'is-popular': tier.popular, [`tier-${tier.badgeType}`]: true }"
          >
            <!-- 推荐标签 -->
            <div v-if="tier.badge" class="pricing-badge" :class="`badge-${tier.badgeType}`">
              <i v-if="tier.popular" class="ri-fire-line" />
              <i v-else-if="tier.badgeType === 'neutral'" class="ri-github-fill" />
              <i v-else class="ri-shield-star-line" />
              <span>{{ tier.badge }}</span>
            </div>

            <!-- 卡片头部：名称、适用对象、价格 -->
            <div class="pricing-header">
              <h3 class="tier-name">{{ tier.name }}</h3>
              <p class="tier-target">{{ tier.target }}</p>
              <div class="tier-price-box">
                <span class="price-currency">¥</span>
                <span class="price-num">{{ tier.price }}</span>
                <span class="price-unit">/ {{ tier.unit }}</span>
              </div>
              <p class="tier-desc">{{ tier.desc }}</p>
            </div>

            <!-- 分割线 -->
            <div class="pricing-divider" />

            <!-- 特性列表 -->
            <ul class="pricing-features">
              <li
                v-for="(f, idx) in tier.features"
                :key="idx"
                class="feature-row"
                :class="{ 'is-excluded': !f.included }"
              >
                <div class="feature-icon">
                  <i v-if="f.included" class="ri-check-line" />
                  <i v-else class="ri-close-line" />
                </div>
                <div class="feature-text">
                  <span class="feature-main">{{ f.text }}</span>
                  <span v-if="f.note" class="feature-note">（{{ f.note }}）</span>
                </div>
              </li>
            </ul>

            <!-- 操作按钮 -->
            <div class="pricing-action">
              <button
                class="pricing-btn"
                :class="{ 'btn-popular': tier.popular, 'btn-ghost': !tier.popular && tier.key === 'openSource', 'btn-pro': tier.key === 'pro' }"
                @click="handlePricingAction(tier.btnAction)"
              >
                <span>{{ tier.btnText }}</span>
                <i v-if="tier.btnAction === 'github'" class="ri-external-link-line" />
                <i v-else class="ri-arrow-right-line" />
              </button>
            </div>
          </div>
        </div>

        <!-- Docker 本地快速启动 -->
        <div class="docker-callout">
          <div class="docker-callout-left">
            <div class="docker-badge">
              <i class="ri-terminal-box-line" />
              <span>本地极速体验</span>
            </div>
            <p class="docker-title">终端一行命令启动开源版，零配置立即体验</p>
            <p class="docker-sub">
              无需联网激活，全量代码公开，财务数据完整保存在本地。
            </p>
          </div>
          <div class="docker-callout-right">
            <div class="docker-code-box">
              <code>{{ dockerCmd }}</code>
              <button
                class="docker-copy-btn"
                :class="{ copied: copiedDocker }"
                :aria-label="copiedDocker ? '已复制' : '复制命令'"
                @click="copyDockerCmd"
              >
                <i :class="copiedDocker ? 'ri-check-line' : 'ri-file-copy-line'" />
                <span>{{ copiedDocker ? '已复制' : '复制' }}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 10. 底部精简 CTA 与页脚 (整合为最终幻灯片) -->
    <section class="landing-slide-end">
      <div class="cta-banner">
        <div class="cta-glow-mesh" />
        <div class="cta-inner text-center">
          <h2 class="cta-title">给公司配一个懂业务的 AI 财务官</h2>
          <p class="cta-sub">告别外包糊涂账，做一本规范透明、经得起查验的干净账本。</p>
          <div class="cta-buttons">
            <button class="island-cta-btn" @click="router.push('/login')">
              <span>免费开启清爽账本</span>
              <span class="btn-arrow-icon"><i class="ri-arrow-right-line" /></span>
            </button>
          </div>

          <!-- 开放共创测试提示 -->
          <div class="cta-co-build-bar">
            <span class="co-build-pill"><i class="ri-compass-3-line" /> 开放共测</span>
            <span class="co-build-text">
              目前系统正面向技术、设计、咨询、电商、实体等更多行业开放测试。诚邀体验并反馈真实意见，与我们一起打磨更懂创业者的账税工具。
            </span>
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

    <!-- 10. 移动端吸底浮动操作栏 (滑出首屏后呈现) -->
    <Transition name="fade-slide-up">
      <div v-if="showStickyCta" class="mobile-sticky-bar">
        <div class="sticky-bar-info">
          <b>梭罗记账 SoloBooks</b>
          <span>AI 智能对账 · 免费体验</span>
        </div>
        <button class="sticky-bar-btn" @click="router.push(isLoggedIn ? '/' : '/login')">
          <span>{{ isLoggedIn ? '进入工作台' : '免费试用' }}</span>
          <i class="ri-arrow-right-line" />
        </button>
      </div>
    </Transition>
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
  width: 100%;
  overflow-x: hidden;
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
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Noto Sans SC", sans-serif;
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

.hero-scan-btn {
  height: 48px;
  padding: 0 22px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border-radius: 999px;
  background: #ffffff;
  color: #1747b0;
  border: 1px solid rgba(23, 71, 176, 0.25);
  font-size: 14.5px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 2px 10px rgba(23, 71, 176, 0.06);
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.hero-scan-btn:hover {
  background: #edf3ff;
  border-color: #1747b0;
  transform: translateY(-1px);
}

.hero-scan-btn:active {
  transform: scale(0.97);
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
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  justify-content: center;
  scroll-snap-align: start;
  scroll-snap-stop: normal;
}

.section-inner {
  width: 100%;
  max-width: 1180px;
  margin: 0 auto;
  padding: 0 24px;
  box-sizing: border-box;
}

.section-top {
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
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
  position: absolute;
  top: 0;
  left: 0;
  width: 0;
  height: 0;
  pointer-events: none;
  visibility: hidden;
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
  width: 100%;
  max-width: 860px;
  box-sizing: border-box;
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

/* ── 合规与数据安全 4 大核心保障卡片 ── */
.compliance-evidence-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 20px;
  max-width: 1200px;
  margin: 0 auto;
}

.evidence-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 24px 20px;
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

/* ── 移动端汉堡切换按钮与折叠抽屉 ── */
.mobile-menu-btn {
  display: none;
  width: 36px;
  height: 36px;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-size: 20px;
  color: #1e293b;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
  padding: 0;
  flex-shrink: 0;
}

.mobile-menu-btn:hover {
  background: #f1f5f9;
}

.mobile-drawer-backdrop {
  position: fixed;
  top: 56px;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(15, 23, 42, 0.4);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  z-index: 99;
}

.mobile-drawer {
  background: #ffffff;
  border-bottom: 1px solid #e2e8f0;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.12);
  padding: 16px 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: calc(100vh - 56px);
  overflow-y: auto;
}

.mobile-drawer-links {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.mobile-drawer-links a {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #334155;
  text-decoration: none;
  transition: all 0.15s ease;
}

.mobile-drawer-links a i {
  font-size: 18px;
  color: #1747b0;
}

.mobile-drawer-links a:hover,
.mobile-drawer-links a:active {
  background: #f1f5f9;
  color: #1747b0;
}

.drawer-badge {
  font-size: 10px;
  font-weight: 700;
  background: #ede9fe;
  color: #7c3aed;
  padding: 1px 6px;
  border-radius: 999px;
  margin-left: auto;
}

.drawer-badge.highlight {
  background: #dcfce7;
  color: var(--solo-income);
}

.mobile-drawer-bottom {
  padding-top: 8px;
  border-top: 1px solid #f1f5f9;
}

.mobile-drawer-login-btn {
  width: 100%;
  height: 44px;
  background: #1747b0;
  color: #ffffff;
  border: none;
  border-radius: 8px;
  font-size: 15px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(23, 71, 176, 0.25);
}

.mobile-drawer-enter-active,
.mobile-drawer-leave-active {
  transition: opacity 0.25s ease;
}

.mobile-drawer-enter-active .mobile-drawer,
.mobile-drawer-leave-active .mobile-drawer {
  transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

.mobile-drawer-enter-from,
.mobile-drawer-leave-to {
  opacity: 0;
}

.mobile-drawer-enter-from .mobile-drawer,
.mobile-drawer-leave-to .mobile-drawer {
  transform: translateY(-12px);
}

/* ── 移动端浮动吸底操作栏 ── */
.mobile-sticky-bar {
  display: none;
  position: fixed;
  bottom: max(16px, env(safe-area-inset-bottom, 16px));
  left: 16px;
  right: 16px;
  z-index: 90;
  background: rgba(255, 255, 255, 0.94);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(203, 213, 225, 0.85);
  border-radius: 12px;
  padding: 10px 14px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  box-shadow:
    0 10px 25px -5px rgba(15, 23, 42, 0.15),
    0 2px 6px -1px rgba(15, 23, 42, 0.08);
}

.sticky-bar-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.sticky-bar-info b {
  font-size: 13.5px;
  color: #0f172a;
  line-height: 1.2;
}

.sticky-bar-info span {
  font-size: 11px;
  color: #5b6a7c;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sticky-bar-btn {
  background: #1747b0;
  color: #ffffff;
  border: none;
  border-radius: 8px;
  padding: 8px 14px;
  font-size: 13px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  flex-shrink: 0;
  box-shadow: 0 2px 6px rgba(23, 71, 176, 0.28);
}

.fade-slide-up-enter-active,
.fade-slide-up-leave-active {
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

.fade-slide-up-enter-from,
.fade-slide-up-leave-to {
  opacity: 0;
  transform: translateY(16px);
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
    grid-template-columns: repeat(2, 1fr);
    gap: 16px;
  }
}

/* 移动平板与手机通用适配 (<= 768px) */
@media (max-width: 768px) {
  :global(html:has(.landing-page)),
  :global(html.landing-scroll-snap) {
    scroll-snap-type: none;
  }

  .mobile-menu-btn {
    display: flex;
  }

  .mobile-sticky-bar {
    display: flex;
  }

  .nav-links {
    display: none;
  }

  .hero,
  .section,
  .landing-slide-end {
    min-height: auto;
    padding: 42px 0 36px;
    width: 100%;
    max-width: 100%;
    box-sizing: border-box;
    overflow-x: hidden;
  }

  .section-inner {
    width: 100%;
    max-width: 100%;
    padding: 0 16px;
    box-sizing: border-box;
  }

  .section-top {
    width: 100%;
    max-width: 100%;
    margin-bottom: 24px;
    box-sizing: border-box;
  }

  .section-headline {
    font-size: 24px;
    line-height: 1.35;
    word-break: break-word;
    letter-spacing: -0.4px;
  }

  .section-subtitle {
    font-size: 13.5px;
    line-height: 1.55;
    word-break: break-word;
  }

  .hero {
    padding-top: 24px;
  }

  .hero-glow {
    width: 100vw;
    max-width: 100%;
  }

  .hero-inner {
    padding: 24px 16px 0;
  }

  .hero-title {
    font-size: 34px;
    line-height: 1.2;
    letter-spacing: -0.6px;
    margin-bottom: 14px;
  }

  .hero-br-mobile {
    display: inline;
  }

  .hero-sub {
    font-size: 14.5px;
    line-height: 1.6;
    margin-bottom: 22px;
  }

  .hero-action-block {
    margin-bottom: 26px;
    width: 100%;
    max-width: 360px;
  }

  .hero-cta,
  .hero-cta button,
  .cta-buttons button {
    width: 100%;
    justify-content: center;
  }

  /* Bento 价值看板：移动端保持精巧 2x2 栅格，严防 1 列超长堆叠 */
  .hero-proof-bento {
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }

  .proof-card {
    padding: 12px 10px;
    border-radius: 10px;
  }

  .proof-card-top {
    margin-bottom: 8px;
  }

  .proof-icon-wrap {
    width: 28px;
    height: 28px;
    font-size: 13px;
  }

  .proof-badge {
    font-size: 10px;
    padding: 1px 5px;
  }

  .proof-val {
    font-size: 19px;
    margin-bottom: 3px;
  }

  .proof-title {
    font-size: 12.5px;
    margin-bottom: 3px;
    line-height: 1.3;
  }

  .proof-desc {
    font-size: 10.5px;
    line-height: 1.4;
    color: #64748b;
  }

  /* 四大支柱 */
  .pillars-grid {
    grid-template-columns: 1fr;
    gap: 12px;
  }

  .pillar-card {
    padding: 16px 14px;
  }

  .pillar-head {
    margin-bottom: 12px;
  }

  .pillar-icon-box {
    width: 38px;
    height: 38px;
    font-size: 18px;
  }

  .pillar-title {
    font-size: 17px;
    margin-bottom: 6px;
  }

  .pillar-desc {
    font-size: 13px;
    margin-bottom: 14px;
  }

  /* 核心功能展台 Showcase */
  .section-showcase {
    padding: 32px 0 16px;
  }

  .showcase-nav-bar {
    width: 100%;
    max-width: 100%;
    box-sizing: border-box;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
    justify-content: flex-start;
    padding: 4px;
    gap: 4px;
    margin-bottom: 18px;
    border-radius: 10px;
  }

  .showcase-nav-bar::-webkit-scrollbar {
    display: none;
  }

  .showcase-tab-btn {
    padding: 7px 11px;
    font-size: 12px;
    gap: 6px;
  }

  .tab-btn-title {
    font-size: 12px;
  }

  .tab-mcp-badge {
    font-size: 9px;
    padding: 0 4px;
  }

  .stage-grid {
    grid-template-columns: 1fr;
    gap: 20px;
    width: 100%;
    max-width: 100%;
    min-width: 0;
  }

  .stage-left,
  .stage-right {
    width: 100%;
    max-width: 100%;
    min-width: 0;
    box-sizing: border-box;
  }

  .stage-title {
    font-size: 22px;
    line-height: 1.3;
  }

  .stage-desc {
    font-size: 13.5px;
    margin-bottom: 14px;
  }

  .stage-bullets {
    gap: 8px;
  }

  .stage-bullet {
    font-size: 12.5px;
  }

  .stage-cta-row {
    flex-direction: column;
    width: 100%;
    margin-top: 16px;
  }

  .stage-cta-primary,
  .stage-cta-ghost {
    width: 100%;
    justify-content: center;
    padding: 9px 14px;
  }

  /* 展台视窗 Mockup */
  .mockup-window {
    border-radius: 10px;
    box-shadow: 0 8px 24px -4px rgba(15, 23, 42, 0.08);
  }

  .window-bar {
    height: 34px;
    padding: 0 10px;
  }

  .window-title {
    font-size: 10.5px;
    max-width: 180px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .window-status {
    font-size: 10px;
  }

  .window-content {
    padding: 12px;
    gap: 10px;
  }

  /* Tab 1: 批量归集单列完整排布 */
  .batch-files {
    grid-template-columns: 1fr;
    gap: 6px;
  }

  .file-chip {
    padding: 7px 10px;
  }

  .file-name {
    font-size: 11.5px;
  }

  .agent-parse-banner {
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
    padding-top: 8px;
  }

  .voucher-top {
    padding: 6px 8px;
    font-size: 10.5px;
  }

  .voucher-bottom {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
    padding: 8px 10px;
  }

  .btn-confirm-demo {
    text-align: center;
    width: 100%;
    padding: 5px 0;
  }

  /* Tab 2: 对账卡片 */
  .recon-card-item {
    padding: 10px 10px;
    gap: 8px;
  }

  .recon-card-row {
    flex-wrap: wrap;
    gap: 4px;
  }

  .recon-card-row b {
    font-size: 12.5px;
  }

  /* Tab 3: 结账向导 */
  .wizard-step-item {
    padding: 9px 10px;
    gap: 8px;
  }

  .w-info b {
    font-size: 12px;
  }

  .w-info span {
    font-size: 10.5px;
  }

  .btn-wizard-action {
    padding: 5px 8px;
    font-size: 11px;
    white-space: nowrap;
  }

  /* Tab 4: 经营大盘 KPI 保持 2x2 */
  .board-metrics-grid {
    grid-template-columns: repeat(2, 1fr);
    gap: 8px;
  }

  .board-kpi-card {
    padding: 10px 10px;
  }

  .kpi-label {
    font-size: 11px;
    margin-bottom: 4px;
  }

  .kpi-val {
    font-size: 17px;
  }

  .kpi-decimal {
    font-size: 11.5px;
  }

  .kpi-trend {
    font-size: 10px;
    margin-top: 4px;
  }

  /* Tab 5: MCP 终端视窗防破框 */
  .mcp-terminal-body {
    padding: 12px;
    gap: 8px;
    font-size: 11.5px;
  }

  .user-bubble {
    max-width: 100%;
  }

  .mcp-skill-chip,
  .mcp-call-chip {
    word-break: break-all;
    white-space: normal;
    line-height: 1.4;
    font-size: 10.5px;
  }

  .mcp-voucher-preview {
    overflow-x: auto;
    font-size: 11px;
    padding: 8px 10px;
  }

  /* 痛点方案对比 */
  .compare-grid {
    grid-template-columns: 1fr;
    gap: 12px;
  }

  .compare-card {
    padding: 16px 14px;
  }

  /* 账本迁移 */
  .migrate-grid {
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .migrate-bridge {
    width: auto;
    flex-direction: row;
    gap: 8px;
    margin: 4px 0;
  }

  .migrate-card {
    padding: 16px 14px;
  }

  .migrate-promises {
    grid-template-columns: 1fr;
    gap: 8px;
    margin-top: 18px;
  }

  /* 适用场景与合规保障 */
  .persona-grid {
    grid-template-columns: 1fr;
    gap: 12px;
  }

  .persona-item {
    padding: 16px 14px;
  }

  .compliance-evidence-grid {
    grid-template-columns: 1fr;
    gap: 12px;
  }

  .evidence-card {
    padding: 16px 14px;
  }

  /* 底部 CTA 与页脚 */
  .cta-title {
    font-size: 22px;
  }

  .cta-sub {
    font-size: 13.5px;
    margin-bottom: 22px;
  }

  .site-footer {
    padding: 24px 0 max(32px, env(safe-area-inset-bottom, 32px));
  }

  .footer-inner {
    flex-direction: column;
    align-items: center;
    text-align: center;
    gap: 16px;
  }

  .footer-brand {
    flex-direction: column;
    align-items: center;
    text-align: center;
    gap: 8px;
  }

  .footer-brand-info p {
    text-align: center;
  }

  .footer-sub {
    flex-direction: column;
    gap: 8px;
    text-align: center;
    margin-top: 16px;
    padding-top: 14px;
  }

  .footer-filings {
    flex-direction: column;
    gap: 6px;
    align-items: center;
  }
}

/* 极窄屏幕微调 (<= 480px, 如 iPhone SE / mini 等) */
@media (max-width: 480px) {
  .nav-inner {
    padding: 0 12px;
  }

  .section-inner {
    padding: 0 12px;
  }

  .hero-title {
    font-size: 28px;
    letter-spacing: -0.4px;
  }

  .hero-inner {
    padding: 16px 10px 0;
  }

  .hero-proof-bento {
    gap: 8px;
  }

  .proof-card {
    padding: 10px 8px;
  }

  .proof-val {
    font-size: 17px;
  }

  .proof-title {
    font-size: 11.5px;
  }

  .proof-desc {
    font-size: 10px;
  }

  .section-headline {
    font-size: 20px;
    line-height: 1.35;
  }

  .section-subtitle {
    font-size: 12.5px;
  }

  .section-top {
    margin-bottom: 20px;
  }

  .desktop-action-btn {
    display: none;
  }

  .mobile-sticky-bar {
    left: 10px;
    right: 10px;
    bottom: max(10px, env(safe-area-inset-bottom, 10px));
    padding: 8px 12px;
  }
}

/* ── 高维定位新增视觉组件 ── */
.hero-kicker-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(23, 71, 176, 0.18);
  box-shadow: 0 2px 10px rgba(23, 71, 176, 0.06);
  padding: 6px 14px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
  color: #1747b0;
  margin-bottom: 20px;
  backdrop-filter: blur(8px);
}

.agent-advice-bubble {
  background: #fffdf5;
  border: 1px solid #fef08a;
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  box-shadow: 0 2px 8px rgba(217, 119, 6, 0.05);
}

.advice-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.advice-title {
  font-size: 12px;
  font-weight: 700;
  color: #854d0e;
}

.advice-badge {
  font-size: 10px;
  font-weight: 600;
  background: #fef9c3;
  color: var(--solo-warning);
  padding: 1px 6px;
  border-radius: 4px;
}

.advice-content {
  margin: 0;
  font-size: 11.5px;
  color: #713f12;
  line-height: 1.5;
}

/* 问一问实时顾问对话模拟 */
.advisor-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.advisor-msg {
  display: flex;
  gap: 10px;
}

.advisor-msg.user {
  flex-direction: row-reverse;
}

.msg-avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  flex-shrink: 0;
}

.advisor-msg.user .msg-avatar {
  background: #e2e8f0;
  color: #334155;
}

.advisor-msg.assistant .msg-avatar {
  background: #edf3ff;
  color: #1747b0;
}

.msg-bubble {
  max-width: 88%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.advisor-msg.user .msg-bubble {
  align-items: flex-end;
}

.msg-author {
  font-size: 11px;
  font-weight: 600;
  color: #64748b;
}

.advisor-msg.user .msg-text {
  background: #1747b0;
  color: #ffffff;
  padding: 8px 12px;
  border-radius: 10px 2px 10px 10px;
  font-size: 12px;
  line-height: 1.5;
}

.advisor-msg.assistant .msg-bubble {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 2px 10px 10px 10px;
  padding: 12px;
}

.advisor-warning-alert {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  background: #fff1f2;
  border: 1px solid #fecdd3;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 11px;
  color: #9f1239;
  line-height: 1.45;
  margin-bottom: 6px;
}

.advisor-warning-alert i {
  color: #e11d48;
  font-size: 14px;
  margin-top: 1px;
}

.advisor-msg.assistant .msg-text {
  font-size: 12px;
  color: #1e293b;
  line-height: 1.6;
}

.advisor-action-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 6px;
  padding-top: 8px;
  border-top: 1px dashed #e2e8f0;
}

.btn-advisor-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: #1747b0;
  color: #ffffff;
  font-size: 11.5px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
}

.advisor-action-tag {
  font-size: 10.5px;
  color: #0f7354;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 2px;
}

/* 协审链接分享横幅 */
.audit-share-banner {
  margin-top: 12px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.share-icon-wrap {
  width: 32px;
  height: 32px;
  border-radius: 6px;
  background: #edf3ff;
  color: #1747b0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}

.share-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.share-info b {
  font-size: 12px;
  color: #0f172a;
}

.share-info span {
  font-size: 11px;
  color: #64748b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.btn-copy-share {
  font-size: 11px;
  font-weight: 600;
  color: #1747b0;
  background: #edf3ff;
  border: 1px solid #dbeafe;
  padding: 4px 8px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  flex-shrink: 0;
}

/* ── 开源社区与商业定价方案 (Pricing) ── */
.section-pricing {
  background: #f8fafc;
  position: relative;
}

.pricing-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
  align-items: stretch;
  margin-bottom: 28px;
}

.pricing-card {
  position: relative;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 24px 22px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03), 0 4px 12px -2px rgba(0, 0, 0, 0.03);
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}

.pricing-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px -4px rgba(15, 23, 42, 0.08);
}

.pricing-card.is-popular {
  border: 2px solid #2563eb;
  background: linear-gradient(180deg, #ffffff 0%, #f9fbff 100%);
  box-shadow: 0 8px 30px -4px rgba(37, 99, 235, 0.12);
}

.pricing-card.tier-premium {
  border-color: #cbd5e1;
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
}

/* 顶部徽标 */
.pricing-badge {
  position: absolute;
  top: -12px;
  left: 20px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.2px;
  line-height: 1.4;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.06);
}

.pricing-badge.badge-primary {
  background: #2563eb;
  color: #ffffff;
  box-shadow: 0 2px 8px rgba(37, 99, 235, 0.3);
}

.pricing-badge.badge-neutral {
  background: #0f172a;
  color: #f8fafc;
}

.pricing-badge.badge-premium {
  background: #475569;
  color: #f8fafc;
}

/* 头部信息 */
.pricing-header {
  margin-top: 4px;
}

.tier-name {
  font-size: 18px;
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 4px 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.tier-target {
  font-size: 11.5px;
  color: #64748b;
  margin: 0 0 14px 0;
  font-weight: 500;
}

.tier-price-box {
  display: flex;
  align-items: baseline;
  gap: 3px;
  margin-bottom: 10px;
}

.price-currency {
  font-size: 18px;
  font-weight: 700;
  color: #0f172a;
}

.price-num {
  font-size: 34px;
  font-weight: 800;
  color: #0f172a;
  line-height: 1;
  letter-spacing: -0.5px;
}

.is-popular .price-num {
  color: #1747b0;
}

.price-unit {
  font-size: 12px;
  color: #64748b;
  margin-left: 2px;
}

.tier-desc {
  font-size: 12.5px;
  color: #475569;
  line-height: 1.5;
  min-height: 38px;
  margin: 0;
}

.pricing-divider {
  height: 1px;
  background: #f1f5f9;
  margin: 16px 0;
}

/* 特性清单 */
.pricing-features {
  list-style: none;
  padding: 0;
  margin: 0 0 20px 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex: 1;
}

.feature-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12px;
  line-height: 1.45;
  color: #334155;
}

.feature-icon {
  margin-top: 2px;
  font-size: 14px;
  flex-shrink: 0;
  color: #16a34a;
}

.feature-row.is-excluded {
  color: #94a3b8;
}

.feature-row.is-excluded .feature-icon {
  color: #cbd5e1;
}

.feature-text {
  flex: 1;
}

.feature-main {
  font-weight: 500;
}

.feature-note {
  font-size: 11px;
  color: #64748b;
  margin-left: 2px;
}

.is-excluded .feature-note {
  color: #94a3b8;
}

/* 按钮区 */
.pricing-action {
  margin-top: auto;
}

.pricing-btn {
  width: 100%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px 16px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
  border: 1px solid transparent;
}

.pricing-btn.btn-popular {
  background: #2563eb;
  color: #ffffff;
  border-color: #2563eb;
  box-shadow: 0 2px 8px rgba(37, 99, 235, 0.25);
}

.pricing-btn.btn-popular:hover {
  background: #1d4ed8;
  border-color: #1d4ed8;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(37, 99, 235, 0.35);
}

.pricing-btn.btn-ghost {
  background: #f8fafc;
  color: #334155;
  border-color: #cbd5e1;
}

.pricing-btn.btn-ghost:hover {
  background: #edf2f7;
  color: #0f172a;
  border-color: #94a3b8;
}

.pricing-btn.btn-pro {
  background: #ffffff;
  color: #0f172a;
  border-color: #94a3b8;
}

.pricing-btn.btn-pro:hover {
  background: #0f172a;
  color: #ffffff;
  border-color: #0f172a;
}

/* ── 底部 Docker Callout ── */
.docker-callout {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  background: #0f172a;
  color: #f8fafc;
  border-radius: 12px;
  padding: 18px 24px;
  box-shadow: 0 8px 24px -4px rgba(15, 23, 42, 0.15);
  border: 1px solid #1e293b;
}

.docker-callout-left {
  flex: 1;
  min-width: 0;
}

.docker-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(255, 255, 255, 0.1);
  color: #93c5fd;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
  margin-bottom: 6px;
}

.docker-title {
  font-size: 13.5px;
  font-weight: 600;
  color: #ffffff;
  margin: 0 0 3px 0;
}

.docker-sub {
  font-size: 11.5px;
  color: #94a3b8;
  margin: 0;
  line-height: 1.4;
}

.docker-callout-right {
  flex-shrink: 0;
}

.docker-code-box {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 7px 12px;
}

.docker-code-box code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 12px;
  color: #38bdf8;
  user-select: all;
  white-space: nowrap;
}

.docker-copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  font-weight: 600;
  color: #e2e8f0;
  background: #334155;
  border: 1px solid #475569;
  border-radius: 5px;
  padding: 4px 9px;
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}

.docker-copy-btn:hover {
  background: #475569;
  color: #ffffff;
}

.docker-copy-btn.copied {
  background: #166534;
  border-color: #22c55e;
  color: #ffffff;
}

/* 响应式调整 */
@media (max-width: 900px) {
  .pricing-grid {
    grid-template-columns: 1fr;
    gap: 20px;
  }

  .docker-callout {
    flex-direction: column;
    align-items: stretch;
    gap: 14px;
    padding: 16px;
  }

  .docker-code-box {
    overflow-x: auto;
    justify-content: space-between;
  }

  .docker-code-box code {
    font-size: 11px;
  }
}

/* ── 5.5 创始人真实手记 (Origin Story) ── */
.section-story {
  background: #ffffff;
  position: relative;
}

.story-card {
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  padding: 36px 32px;
  box-shadow: 0 4px 20px -2px rgba(15, 23, 42, 0.05);
}

.story-header {
  margin-bottom: 28px;
}

.story-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #eff6ff;
  border: 1px solid #dbeafe;
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 600;
  padding: 4px 12px;
  border-radius: 999px;
  margin-bottom: 12px;
}

.story-title {
  font-size: 24px;
  font-weight: 800;
  color: #0f172a;
  letter-spacing: -0.4px;
  margin: 0 0 8px 0;
}

.story-sub {
  font-size: 13.5px;
  color: #64748b;
  max-width: 680px;
  margin: 0 auto;
  line-height: 1.55;
}

.story-grid {
  display: grid;
  grid-template-columns: 1fr 1.1fr;
  gap: 28px;
  align-items: stretch;
}

.story-pane {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 24px;
  display: flex;
  flex-direction: column;
}

.pane-head {
  margin-bottom: 18px;
}

.pane-tag {
  display: inline-block;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
  margin-bottom: 6px;
}

.pain-tag {
  background: #fff1f2;
  color: #e11d48;
}

.resolve-tag {
  background: #f0fdf4;
  color: #16a34a;
}

.pane-title {
  font-size: 15px;
  font-weight: 700;
  color: #0f172a;
  margin: 0;
}

.story-pains-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.pain-box {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.pain-icon-wrap {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #ffe4e6;
  color: #e11d48;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  flex-shrink: 0;
  margin-top: 2px;
}

.pain-content b {
  display: block;
  font-size: 13px;
  color: #1e293b;
  margin-bottom: 2px;
}

.pain-content p {
  font-size: 12px;
  color: #64748b;
  line-height: 1.45;
  margin: 0;
}

.story-resolve-text {
  font-size: 13px;
  color: #334155;
  line-height: 1.65;
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex: 1;
}

.story-resolve-text p {
  margin: 0;
}

.resolve-highlight {
  background: #f8fafc;
  border-left: 3px solid #2563eb;
  padding: 8px 12px;
  border-radius: 0 6px 6px 0;
  font-size: 12.5px;
  color: var(--solo-info) !important;
}

.story-badges-row {
  margin-top: 20px;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  padding-top: 16px;
  border-top: 1px dashed #e2e8f0;
}

.s-badge-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  font-weight: 600;
  color: #334155;
  background: #f8fafc;
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid #f1f5f9;
}

.s-badge-item i {
  font-size: 14px;
}

/* 底部 CTA 开放共测卡条 */
.cta-co-build-bar {
  margin-top: 24px;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.06);
  padding: 8px 18px;
  border-radius: 999px;
  max-width: 680px;
}

.co-build-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  color: #1d4ed8;
  font-size: 11.5px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 999px;
  flex-shrink: 0;
}

.co-build-text {
  font-size: 12.5px;
  color: #334155;
  line-height: 1.5;
  text-align: left;
}

@media (max-width: 900px) {
  .story-card {
    padding: 24px 16px;
  }

  .story-grid {
    grid-template-columns: 1fr;
    gap: 18px;
  }

  .story-title {
    font-size: 20px;
  }

  .story-badges-row {
    grid-template-columns: 1fr;
  }

  .cta-co-build-bar {
    flex-direction: column;
    border-radius: 12px;
    align-items: flex-start;
    padding: 12px 14px;
    margin-top: 18px;
  }
}



</style>
