<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<!-- 免费财务软件专题页（SEO/GEO P0）：承接「免费财务软件 / 自托管 / 一人公司」搜索意图。
     文案只写可验证事实（价格、额度、数据位置），FAQ 与 seo.ts 的 FAQPage 共用 content/faq.ts。 -->
<script setup lang="ts">
import { useRouter } from 'vue-router'
import { FREE_FAQ } from '@/content/faq'

const router = useRouter()

const SELF_HOST_URL = 'https://github.com/jiezixingchen/solobooks-open'

const freePaths = [
  {
    badge: '0 元 · 免部署',
    title: '云端免费试用版',
    desc: '注册即用，功能与标准版一致。适合先拿真实账目跑一遍，再决定要不要升级。',
    points: ['每月 10 万 AI Token', '最多 3 个企业账套', 'Web 与移动端云同步'],
    action: '免费开启云端体验',
    primary: true,
  },
  {
    badge: '永久免费 · 自己部署',
    title: '自托管版',
    desc: '源码可用，装在你自己的服务器上。适合重视数据自主、有技术能力维护环境的团队。',
    points: ['本主体内部记账永久免费', '数据 100% 留在本地', '自备大模型 API Key'],
    action: '查看部署方式',
    primary: false,
  },
]

const compareRows = [
  ['费用', '0 元', '0 元（软件许可）', '598 元 / 年'],
  ['部署方式', '免部署，注册即用', '自备服务器 + Docker', '免部署，注册即用'],
  ['企业账套', '最多 3 个', '不限（受服务器资源限制）', '最多 3 个'],
  ['AI 额度', '每月 10 万 Token', '自备模型 API Key，按模型商价格计费', '每年 500 万 Token'],
  ['数据位置', '云端', '你自己的服务器', '云端'],
  ['适合谁', '想先试用、不想折腾', '重数据自主、有技术能力', '要省心与 1 对 1 服务'],
]

const checks = [
  {
    title: '免费是永久还是试用',
    desc: '问清试用期结束后的价格、降级后还能不能看历史账，以及数据怎么带走。',
  },
  {
    title: '核心功能有没有阉割',
    desc: '报表、对账、结账、导出是否完整。只给录入不给报表的「免费」，等于没免费。',
  },
  {
    title: '数据能不能完整导出',
    desc: '账本、凭证、报表和原始单据都要能导出。导不出的免费工具，换起来成本最高。',
  },
  {
    title: '是不是标准复式记账',
    desc: '借贷平衡、凭证账簿报表齐全，后续找会计交接、应对审计才能对得上。',
  },
  {
    title: '免费额度的边界',
    desc: '账套数、AI 调用量、协作人数分别限制多少，超限后怎么计费，提前算清。',
  },
]

function openSelfHost() {
  window.open(SELF_HOST_URL, '_blank', 'noopener')
}
</script>

<template>
  <div class="free-page">
    <header class="site-nav">
      <div class="nav-inner">
        <button class="brand" @click="router.push('/')">
          <img src="/solo-logo.png" alt="梭罗记账 SoloBooks" />
          <span>梭罗记账 SoloBooks</span>
        </button>
        <nav class="nav-links">
          <a href="/learn">学习中心</a>
          <a href="/ai-connect">AI 连接</a>
          <button class="nav-cta" @click="router.push('/login')">免费注册</button>
        </nav>
      </div>
    </header>

    <main>
      <section class="hero">
        <div class="hero-glow" />
        <div class="hero-inner">
          <p class="eyebrow">免费财务软件 · 小微企业 / 一人公司</p>
          <h1>
            免费财务软件怎么选？<br />
            <span>先分清两种「免费」。</span>
          </h1>
          <p class="hero-sub">
            一种是免部署的云端免费试用，一种是源码可用、装在自己服务器上的自托管版。
            我们把免费边界、额度和适用人群一次写清，你按自己的情况选。
          </p>
          <div class="hero-actions">
            <button class="btn-primary" @click="router.push('/login')">免费开启云端体验</button>
            <button class="btn-ghost" @click="openSelfHost">查看自托管部署</button>
          </div>
          <p class="hero-note">云端免费版每月 10 万 Token、最多 3 个账套；自托管版本主体内部记账永久免费。</p>
        </div>
      </section>

      <section class="section">
        <div class="section-inner">
          <p class="section-tag">两种免费路径</p>
          <h2 class="section-title">不想折腾，还是数据必须在自己手里？</h2>
          <div class="path-grid">
            <article
              v-for="path in freePaths"
              :key="path.title"
              class="path-card"
              :class="{ 'path-card--dark': !path.primary }"
            >
              <span class="path-badge">{{ path.badge }}</span>
              <h3>{{ path.title }}</h3>
              <p class="path-desc">{{ path.desc }}</p>
              <ul class="path-points">
                <li v-for="point in path.points" :key="point">
                  <i class="ri-check-line" />{{ point }}
                </li>
              </ul>
              <button
                class="path-action"
                :class="{ 'path-action--primary': path.primary }"
                @click="path.primary ? router.push('/login') : openSelfHost()"
              >
                {{ path.action }}
              </button>
            </article>
          </div>
        </div>
      </section>

      <section class="section section-soft">
        <div class="section-inner">
          <p class="section-tag">免费边界</p>
          <h2 class="section-title">免费到底免费到什么程度</h2>
          <p class="section-lead">不玩文字游戏：把三档方案的费用、额度、数据位置摆在一起。</p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">项目</th>
                  <th scope="col">云端免费试用版</th>
                  <th scope="col">自托管版</th>
                  <th scope="col">云端标准版</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in compareRows" :key="row[0]">
                  <th scope="row">{{ row[0] }}</th>
                  <td>{{ row[1] }}</td>
                  <td>{{ row[2] }}</td>
                  <td>{{ row[3] }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="table-note">
            自托管版的软件许可永久免费；模型调用费由你直接支付给模型服务商，不经过梭罗记账。
          </p>
        </div>
      </section>

      <section class="section">
        <div class="section-inner">
          <p class="section-tag">选型自检</p>
          <h2 class="section-title">选免费财务软件，先看这 5 件事</h2>
          <div class="check-grid">
            <article v-for="(item, index) in checks" :key="item.title" class="check-card">
              <span class="check-num">{{ String(index + 1).padStart(2, '0') }}</span>
              <h3>{{ item.title }}</h3>
              <p>{{ item.desc }}</p>
            </article>
          </div>
        </div>
      </section>

      <section id="faq" class="section section-soft">
        <div class="section-inner section-inner--narrow">
          <p class="section-tag">常见问题</p>
          <h2 class="section-title">关于免费，大家最常问的 8 个问题</h2>
          <div class="faq-list">
            <details v-for="item in FREE_FAQ" :key="item.q" class="faq-item">
              <summary>{{ item.q }}</summary>
              <p>{{ item.a }}</p>
            </details>
          </div>
        </div>
      </section>

      <section class="cta-section">
        <div class="section-inner">
          <div class="cta-card">
            <h2>先免费用起来，再决定要不要付费</h2>
            <p>云端免费版 0 元注册即用；重视数据自主，就选永久免费的自托管版。</p>
            <div class="cta-actions">
              <button class="btn-primary" @click="router.push('/login')">免费注册</button>
              <a class="cta-link" href="/learn">看更多选型文章</a>
            </div>
          </div>
        </div>
      </section>
    </main>

    <footer class="site-footer">
      <div class="footer-inner">
        <div class="footer-links">
          <a href="/">官网首页</a>
          <a href="/learn">学习中心</a>
          <a href="/ai-connect">AI 连接</a>
          <a href="/agreement">服务协议</a>
          <a href="/privacy">隐私政策</a>
        </div>
        <p>© {{ new Date().getFullYear() }} 梭罗记账 SoloBooks · <a href="https://beian.miit.gov.cn" rel="noreferrer">浙ICP备2025195764号-7</a></p>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.free-page {
  min-height: 100vh;
  background: var(--solo-bg-page);
  color: var(--solo-text-primary);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Noto Sans SC', sans-serif;
}

.site-nav {
  position: sticky;
  top: 0;
  z-index: 20;
  background: var(--solo-bg-nav);
  border-bottom: 1px solid var(--solo-border-light);
  backdrop-filter: blur(12px);
}

.nav-inner {
  max-width: 1120px;
  margin: 0 auto;
  padding: 12px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  border: 0;
  background: transparent;
  color: var(--solo-text-primary);
  font-size: 15px;
  font-weight: 700;
  cursor: pointer;
}

.brand img {
  width: 28px;
  height: 28px;
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 22px;
}

.nav-links a {
  color: var(--solo-text-secondary);
  font-size: 14px;
  text-decoration: none;
}

.nav-links a:hover {
  color: var(--solo-primary);
}

.nav-cta {
  border: 0;
  border-radius: var(--solo-radius-control);
  background: var(--solo-primary);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  padding: 8px 16px;
  cursor: pointer;
}

.hero {
  position: relative;
  overflow: hidden;
  padding: 88px 24px 72px;
  text-align: center;
  background: linear-gradient(180deg, var(--solo-bg-card) 0%, var(--solo-bg-page) 100%);
}

.hero-glow {
  position: absolute;
  top: -180px;
  left: 50%;
  width: 760px;
  height: 420px;
  transform: translateX(-50%);
  background: var(--solo-primary-glow);
  filter: blur(80px);
  pointer-events: none;
}

.hero-inner {
  position: relative;
  max-width: 880px;
  margin: 0 auto;
}

.eyebrow {
  display: inline-block;
  margin: 0 0 18px;
  padding: 6px 14px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-pill);
  background: var(--solo-bg-card);
  color: var(--solo-primary);
  font-size: 13px;
  font-weight: 600;
}

.hero h1 {
  margin: 0 0 20px;
  font-size: clamp(30px, 4.4vw, 48px);
  line-height: 1.25;
  letter-spacing: -0.8px;
}

.hero h1 span {
  color: var(--solo-primary);
}

.hero-sub {
  max-width: 720px;
  margin: 0 auto 30px;
  color: var(--solo-text-secondary);
  font-size: 16px;
  line-height: 1.9;
}

.hero-actions {
  display: flex;
  justify-content: center;
  gap: 14px;
  flex-wrap: wrap;
}

.btn-primary,
.btn-ghost {
  border-radius: var(--solo-radius-control);
  font-size: 15px;
  font-weight: 600;
  padding: 13px 26px;
  cursor: pointer;
}

.btn-primary {
  border: 1px solid var(--solo-primary);
  background: var(--solo-primary);
  color: #fff;
}

.btn-primary:hover {
  background: var(--solo-primary-light);
}

.btn-ghost {
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
  color: var(--solo-text-primary);
}

.btn-ghost:hover {
  border-color: var(--solo-primary);
  color: var(--solo-primary);
}

.hero-note {
  margin: 22px 0 0;
  color: var(--solo-text-muted);
  font-size: 13px;
}

.section {
  padding: 72px 24px;
}

.section-soft {
  background: var(--solo-bg-card);
  border-top: 1px solid var(--solo-border-light);
  border-bottom: 1px solid var(--solo-border-light);
}

.section-inner {
  max-width: 1120px;
  margin: 0 auto;
}

.section-inner--narrow {
  max-width: 820px;
}

.section-tag {
  margin: 0 0 8px;
  color: var(--solo-primary);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 1.4px;
}

.section-title {
  margin: 0 0 12px;
  font-size: clamp(24px, 3vw, 32px);
  line-height: 1.35;
  letter-spacing: -0.4px;
}

.section-lead {
  margin: 0 0 28px;
  color: var(--solo-text-secondary);
  font-size: 15px;
}

.path-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
  margin-top: 28px;
}

.path-card {
  display: flex;
  flex-direction: column;
  padding: 30px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
  box-shadow: var(--solo-shadow-sm);
}

.path-card--dark {
  border-color: var(--solo-primary-dark);
  background: var(--solo-primary-dark);
  color: #fff;
}

.path-badge {
  align-self: flex-start;
  margin-bottom: 14px;
  padding: 4px 10px;
  border-radius: var(--solo-radius-sm);
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  font-size: 12px;
  font-weight: 700;
}

.path-card--dark .path-badge {
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
}

.path-card h3 {
  margin: 0 0 10px;
  font-size: 21px;
}

.path-desc {
  margin: 0 0 18px;
  color: var(--solo-text-secondary);
  font-size: 14.5px;
  line-height: 1.8;
}

.path-card--dark .path-desc {
  color: rgba(255, 255, 255, 0.78);
}

.path-points {
  flex: 1;
  margin: 0 0 22px;
  padding: 0;
  list-style: none;
}

.path-points li {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 10px;
  font-size: 14.5px;
}

.path-points i {
  color: var(--solo-primary);
  margin-top: 2px;
}

.path-card--dark .path-points i {
  color: #fff;
}

.path-action {
  align-self: flex-start;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-control);
  background: var(--solo-bg-card);
  color: var(--solo-text-primary);
  font-size: 14.5px;
  font-weight: 600;
  padding: 11px 22px;
  cursor: pointer;
}

.path-action--primary {
  border-color: var(--solo-primary);
  background: var(--solo-primary);
  color: #fff;
}

.path-card--dark .path-action {
  border-color: rgba(255, 255, 255, 0.35);
  background: transparent;
  color: #fff;
}

.table-wrap {
  margin-top: 26px;
  overflow-x: auto;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
}

table {
  width: 100%;
  min-width: 760px;
  border-collapse: collapse;
  font-size: 14.5px;
}

th,
td {
  padding: 14px 18px;
  text-align: left;
  border-bottom: 1px solid var(--solo-border-light);
  vertical-align: top;
}

thead th {
  background: var(--solo-bg-soft);
  color: var(--solo-text-secondary);
  font-size: 13px;
  font-weight: 700;
}

tbody th {
  color: var(--solo-text-primary);
  font-weight: 600;
  white-space: nowrap;
}

tbody tr:last-child th,
tbody tr:last-child td {
  border-bottom: 0;
}

.table-note {
  margin: 14px 0 0;
  color: var(--solo-text-muted);
  font-size: 13px;
}

.check-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-top: 28px;
}

.check-card {
  padding: 24px;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
}

.check-num {
  color: var(--solo-primary);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 1px;
}

.check-card h3 {
  margin: 10px 0 8px;
  font-size: 16.5px;
}

.check-card p {
  margin: 0;
  color: var(--solo-text-secondary);
  font-size: 14px;
  line-height: 1.8;
}

.faq-list {
  margin-top: 26px;
}

.faq-item {
  border-bottom: 1px solid var(--solo-border);
}

.faq-item summary {
  padding: 18px 0;
  cursor: pointer;
  font-size: 16px;
  font-weight: 600;
  list-style: none;
}

.faq-item summary::-webkit-details-marker {
  display: none;
}

.faq-item summary::after {
  content: '+';
  float: right;
  color: var(--solo-primary);
  font-size: 20px;
  line-height: 1;
}

.faq-item[open] summary::after {
  content: '−';
}

.faq-item p {
  margin: 0 0 18px;
  color: var(--solo-text-secondary);
  font-size: 14.5px;
  line-height: 1.9;
}

.cta-section {
  padding: 40px 24px 76px;
}

.cta-card {
  padding: 48px 32px;
  border-radius: var(--solo-radius-lg);
  background: var(--solo-primary-dark);
  color: #fff;
  text-align: center;
}

.cta-card h2 {
  margin: 0 0 10px;
  font-size: clamp(22px, 3vw, 30px);
}

.cta-card p {
  margin: 0 0 24px;
  color: rgba(255, 255, 255, 0.78);
  font-size: 15px;
}

.cta-actions {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
}

.cta-card .btn-primary {
  border-color: #fff;
  background: #fff;
  color: var(--solo-primary-dark);
}

.cta-link {
  color: rgba(255, 255, 255, 0.85);
  font-size: 14px;
}

.site-footer {
  border-top: 1px solid var(--solo-border-light);
  background: var(--solo-bg-card);
}

.footer-inner {
  max-width: 1120px;
  margin: 0 auto;
  padding: 26px 24px 34px;
  color: var(--solo-text-muted);
  font-size: 13px;
}

.footer-links {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  margin-bottom: 10px;
}

.footer-links a {
  color: var(--solo-text-secondary);
  text-decoration: none;
}

.footer-links a:hover {
  color: var(--solo-primary);
}

.footer-inner p {
  margin: 0;
}

.footer-inner p a {
  color: var(--solo-text-muted);
}

@media (max-width: 900px) {
  .path-grid,
  .check-grid {
    grid-template-columns: 1fr;
  }

  .hero {
    padding: 64px 20px 56px;
  }

  .section {
    padding: 56px 20px;
  }

  .nav-links a {
    display: none;
  }
}

@media (max-width: 640px) {
  .nav-inner {
    padding: 10px 16px;
  }

  .brand span {
    font-size: 14px;
  }

  .hero-actions,
  .cta-actions {
    flex-direction: column;
    align-items: stretch;
  }

  .btn-primary,
  .btn-ghost {
    width: 100%;
  }

  .path-card {
    padding: 24px 20px;
  }
}
</style>
