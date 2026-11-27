<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
/**
 * AI 连接中心（MCP & Skills 实战指南）
 *
 * 公开免登录页面，路由 /ai-connect（别名 /docs/mcp-skills）。
 * 定位：外部访客与开发者的接入枢纽——「是什么 → 怎么装 → 各客户端怎么连 → 技能包怎么用 → 怎么共建」。
 *
 * 令牌处理：后端只存 sha256，明文仅签发时显示一次，所以本页无法「自动注入真实令牌」。
 * 折中方案是用户在页内临时粘贴一次，只在内存里替换配置占位符——不落 localStorage、不进 URL。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import CodeBlock from './CodeBlock.vue'
import {
  CLIENT_GUIDES,
  GITHUB_REPO,
  GITHUB_URL,
  READ_TOOL_COUNT,
  READ_TOOL_GROUPS,
  ROADMAP_SKILLS,
  SAFETY_RULES,
  SKILL_INSTALL_STEPS,
  TAX_SKILL_STEPS,
  TOKEN_PAGE,
  TOKEN_PLACEHOLDER,
  TOOL_TOTAL,
  TROUBLESHOOTING,
  WRITE_TOOLS,
  detectOS,
  remoteConfig,
  resolveEndpoint,
  type VisitorOS,
} from './data'

const ICP_LICENSE = '浙ICP备2025195764号-7'
const PSB_RECORD = '浙公网安备 33011002020894号'
const PSB_RECORD_URL = 'https://beian.mps.gov.cn/#/query/webSearch?code=33011002020894'

const router = useRouter()

const isLoggedIn = computed(() => Boolean(localStorage.getItem('auth_token')))
const os = ref<VisitorOS>('other')
const stars = ref<number | null>(null)

/** 本地开发时前端在 5173、后端在 8001，同域端点打不通，直接给后端地址 */
function guideEndpoint(): string {
  const origin = window.location.origin
  try {
    const host = new URL(origin).hostname
    if (host === 'localhost' || host === '127.0.0.1') return 'http://127.0.0.1:8001/mcp'
  } catch {
    /* origin 解析失败就退回同域拼接 */
  }
  return resolveEndpoint(origin)
}

const endpoint = ref(guideEndpoint())

// ── 令牌临时注入（仅内存）──
const injectedToken = ref('')
const tokenForConfig = computed(() => injectedToken.value.trim() || TOKEN_PLACEHOLDER)
const tokenLooksValid = computed(
  () => !injectedToken.value.trim() || injectedToken.value.trim().startsWith('sbk_'),
)
const tokenInjected = computed(() => Boolean(injectedToken.value.trim()))

const configCtx = computed(() => ({
  endpoint: endpoint.value,
  token: tokenForConfig.value,
  repoUrl: GITHUB_URL,
}))
const quickJson = computed(() => remoteConfig(endpoint.value, tokenForConfig.value))

// ── 客户端 Tab ──
const activeClientId = ref(CLIENT_GUIDES[0].id)
const activeClient = computed(
  () => CLIENT_GUIDES.find((c) => c.id === activeClientId.value) ?? CLIENT_GUIDES[0],
)
const activeSamples = computed(() => activeClient.value.samples(configCtx.value))

function selectClient(id: string) {
  activeClientId.value = id
}

// ── 复制（端点 / 仓库 / 克隆命令）──
const copiedKey = ref('')
let copyTimer: ReturnType<typeof setTimeout> | undefined

async function copyText(key: string, text: string) {
  try {
    await navigator.clipboard.writeText(text)
    copiedKey.value = key
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => {
      copiedKey.value = ''
    }, 2200)
  } catch {
    copiedKey.value = 'failed'
  }
}

function goTokenPage() {
  router.push(isLoggedIn.value ? TOKEN_PAGE : '/login')
}

const osLabel = computed(() => {
  if (os.value === 'macos') return 'macOS'
  if (os.value === 'windows') return 'Windows'
  return '当前系统'
})

onMounted(() => {
  os.value = detectOS(navigator.userAgent)
  endpoint.value = guideEndpoint()

  // Star 数只是锦上添花：拿不到（仓库未公开 / 限流 / 离线）就安静降级，不显示假数字
  const controller = new AbortController()
  const abortTimer = setTimeout(() => controller.abort(), 3500)
  fetch(`https://api.github.com/repos/${GITHUB_REPO}`, { signal: controller.signal })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      if (data && typeof data.stargazers_count === 'number') stars.value = data.stargazers_count
    })
    .catch(() => {
      /* 静默降级 */
    })
    .finally(() => clearTimeout(abortTimer))
})
</script>

<template>
  <div class="mcp-guide-page">
    <!-- 顶部导航 -->
    <header class="site-nav">
      <div class="nav-inner">
        <a class="nav-brand" href="#" @click.prevent="router.push('/landing')">
          <img src="/solo-logo.png" alt="梭罗记账" class="nav-logo" />
          <span class="nav-brand-text">
            <b>梭罗记账</b>
            <span class="nav-sub">SoloBooks</span>
          </span>
        </a>
        <nav class="nav-links">
          <a href="#concepts">MCP 与 Skills</a>
          <a href="#clients">客户端接入</a>
          <a href="#skills">技能包</a>
          <a href="#opensource">开源共建</a>
        </nav>
        <div class="nav-actions">
          <button class="btn-ghost" type="button" @click="router.push('/landing')">返回官网</button>
          <button class="btn-primary" type="button" @click="goTokenPage">
            {{ isLoggedIn ? '签发令牌' : '登录后接入' }}
          </button>
        </div>
      </div>
    </header>

    <!-- 1. Hero：精简定位 + 即拿即用端点/配置卡 -->
    <section class="hero">
      <div class="hero-glow" />
      <div class="hero-inner">
        <div class="hero-copy">
          <span class="hero-eyebrow">
            <i class="ri-plug-2-line" />
            MCP &amp; Agent Skills
          </span>
          <h1 class="hero-title">
            把账套变成<br />
            <em>懂财税的 AI 数字员工</em>
          </h1>
          <p class="hero-sub">
            原生支持 MCP 协议与 Skills 规范。连接 Claude、Cursor、WorkBuddy 等客户端，安全驱动查账、对账、结账与报税。
          </p>
          <div class="hero-cta">
            <button class="btn-primary btn-lg" type="button" @click="goTokenPage">
              <i class="ri-key-2-line" />
              前往签发 MCP 令牌
            </button>
            <a class="btn-outline btn-lg" :href="GITHUB_URL" target="_blank" rel="noreferrer">
              <i class="ri-github-fill" />
              GitHub 开源仓库
              <span v-if="stars !== null" class="star-count">★ {{ stars }}</span>
            </a>
          </div>
        </div>

        <aside class="quick-card">
          <div class="quick-head">
            <span class="quick-dot" />
            <span>快速接入</span>
            <span class="quick-meta">Streamable HTTP · {{ TOOL_TOTAL }} 个工具</span>
          </div>

          <div class="endpoint-box">
            <div class="endpoint-top">
              <span class="endpoint-label">远程 MCP 端点</span>
              <button class="copy-mini" type="button" @click="copyText('endpoint', endpoint)">
                <i :class="copiedKey === 'endpoint' ? 'ri-check-line' : 'ri-file-copy-line'" />
                {{ copiedKey === 'endpoint' ? '已复制' : '复制端点' }}
              </button>
            </div>
            <code class="endpoint-url">{{ endpoint }}</code>
          </div>

          <div class="endpoint-box quick-json-box">
            <div class="endpoint-top">
              <span class="endpoint-label">通用配置（mcpServers）</span>
              <button class="copy-mini" type="button" @click="copyText('quick-json', quickJson)">
                <i :class="copiedKey === 'quick-json' ? 'ri-check-line' : 'ri-file-copy-line'" />
                {{ copiedKey === 'quick-json' ? '已复制' : '复制 JSON' }}
              </button>
            </div>
            <pre class="quick-json"><code>{{ quickJson }}</code></pre>
          </div>

          <a class="quick-jump" href="#clients">
            <span>前往下方粘贴令牌自动填入 / 查看各客户端路径</span>
            <i class="ri-arrow-down-line" />
          </a>
        </aside>
      </div>
    </section>

    <!-- 2. 能力与安全边界 -->
    <section id="concepts" class="section">
      <div class="section-inner">
        <div class="section-head">
          <span class="section-kicker">01 / 能力与边界</span>
          <h2>MCP 取数与起草，Skills 跑通财税 SOP</h2>
          <p>所有写入仅生成待确认草稿，不直接入账。</p>
        </div>

        <div class="concept-grid">
          <article class="concept-card">
            <div class="concept-head">
              <div class="concept-icon blue"><i class="ri-plug-2-line" /></div>
              <div>
                <span class="concept-tag">MCP · 工具协议</span>
                <h3>{{ TOOL_TOTAL }} 个原子账务工具</h3>
              </div>
            </div>
            <p>
              {{ READ_TOOL_COUNT }} 个只读（查账、票流、结账体检、报税底稿）+ {{ WRITE_TOOLS.length }} 个草稿工具，单端点接入任意 MCP 客户端。
            </p>
          </article>

          <article class="concept-card">
            <div class="concept-head">
              <div class="concept-icon purple"><i class="ri-brain-line" /></div>
              <div>
                <span class="concept-tag">Skills · 技能包</span>
                <h3>标准化财税工作流</h3>
              </div>
            </div>
            <p>
              如 <code>solobooks-tax-filing</code> 报税伴侣：拉税局底账比对 → 定位漏记发票 → 补记草稿 → 预填税表。
            </p>
          </article>
        </div>

        <div class="safety-panel">
          <div class="safety-grid">
            <div v-for="rule in SAFETY_RULES" :key="rule.title" class="safety-item">
              <div class="safety-item-head">
                <i :class="rule.icon" />
                <b>{{ rule.title }}</b>
              </div>
              <p>{{ rule.detail }}</p>
            </div>
          </div>
        </div>

        <div class="tools-panel">
          <div class="tools-head">
            <h3>{{ TOOL_TOTAL }} 个原子工具（按账套隔离）</h3>
            <span>只读 {{ READ_TOOL_COUNT }} + 草稿 {{ WRITE_TOOLS.length }} · 工具参数在握手时自动同步</span>
          </div>
          <div class="tools-grid">
            <div v-for="group in READ_TOOL_GROUPS" :key="group.title" class="tool-group">
              <div class="tool-group-head">
                <i :class="group.icon" />
                <b>{{ group.title }}</b>
                <span class="tool-count">{{ group.tools.length }}</span>
              </div>
              <div class="tool-chips">
                <code v-for="name in group.tools" :key="name">{{ name }}</code>
              </div>
            </div>
            <div class="tool-group write">
              <div class="tool-group-head">
                <i class="ri-draft-line" />
                <b>写入草稿（需读写权限）</b>
                <span class="tool-count">{{ WRITE_TOOLS.length }}</span>
              </div>
              <div class="tool-chips">
                <code v-for="name in WRITE_TOOLS" :key="name">{{ name }}</code>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 3. 客户端接入 -->
    <section id="clients" class="section section-alt">
      <div class="section-inner">
        <div class="section-head">
          <span class="section-kicker">02 / 接入</span>
          <h2>主流客户端配置</h2>
          <p>五类客户端共用同一端点与令牌，粘贴后配置自动填好。</p>
        </div>

        <!-- 令牌临时注入 -->
        <div class="inject-bar">
          <div class="inject-copy">
            <b><i class="ri-key-2-line" /> 临时填入令牌（仅内存生效，刷新即清除）</b>
            <p v-if="tokenInjected" class="inject-ok">
              <i class="ri-checkbox-circle-line" />
              已替换配置中的 <code>{{ TOKEN_PLACEHOLDER }}</code>。
            </p>
            <p v-else>
              <button class="link-btn" type="button" @click="goTokenPage">
                {{ isLoggedIn ? '去令牌页复制 sbk_ 令牌 →' : '登录后签发令牌 →' }}
              </button>
            </p>
          </div>
          <div class="inject-input">
            <input
              v-model="injectedToken"
              type="password"
              autocomplete="off"
              spellcheck="false"
              :placeholder="TOKEN_PLACEHOLDER + '（可选）'"
              aria-label="临时粘贴 MCP 令牌"
            />
            <button v-if="injectedToken" class="inject-clear" type="button" @click="injectedToken = ''">
              清除
            </button>
          </div>
        </div>
        <p v-if="!tokenLooksValid" class="inject-warn">
          <i class="ri-error-warning-line" />
          令牌应以 <code>sbk_</code> 开头，请核对是否完整复制。
        </p>

        <!-- Tab -->
        <div class="client-tabs" role="tablist" aria-label="AI 客户端配置选择">
          <button
            v-for="client in CLIENT_GUIDES"
            :key="client.id"
            class="client-tab"
            :class="{ active: client.id === activeClientId }"
            type="button"
            role="tab"
            :aria-selected="client.id === activeClientId"
            @click="selectClient(client.id)"
          >
            <i :class="client.icon" />
            <span class="client-tab-text">
              <b>{{ client.name }}</b>
              <em>{{ client.tagline }}</em>
            </span>
          </button>
        </div>

        <!-- 面板 -->
        <div class="client-panel" role="tabpanel">
          <div class="panel-left">
            <div class="panel-head">
              <div>
                <h3>{{ activeClient.name }}</h3>
                <span class="panel-vendor">{{ activeClient.vendor }}</span>
              </div>
              <span class="transport-badge"><i class="ri-router-line" /> {{ activeClient.transport }}</span>
            </div>

            <ol class="step-list">
              <li v-for="(step, idx) in activeClient.steps" :key="step.title">
                <span class="step-num">{{ String(idx + 1).padStart(2, '0') }}</span>
                <div>
                  <b>{{ step.title }}</b>
                  <p>{{ step.detail }}</p>
                </div>
              </li>
            </ol>

            <div class="verify-box">
              <i class="ri-check-double-line" />
              <div>
                <b>验证方式</b>
                <p>{{ activeClient.verify }}</p>
              </div>
            </div>

            <ul class="tip-list">
              <li v-for="tip in activeClient.tips" :key="tip">
                <i class="ri-lightbulb-flash-line" />
                <span>{{ tip }}</span>
              </li>
            </ul>
          </div>

          <div class="panel-right">
            <div class="panel-right-head">
              <span><i class="ri-file-code-line" /> 配置代码</span>
              <span v-if="os !== 'other'" class="os-hint">已高亮 {{ osLabel }} 路径</span>
            </div>
            <CodeBlock
              v-for="sample in activeSamples"
              :key="sample.label"
              :sample="sample"
              :os="os"
              class="panel-code"
            />
            <p class="panel-footnote">
              <i class="ri-lock-2-line" />
              令牌等同密码，请勿提交到代码仓库或公开分享。
            </p>
          </div>
        </div>

        <!-- 排错 -->
        <div class="trouble-panel">
          <h3><i class="ri-bug-line" /> 常见排错</h3>
          <div class="trouble-grid">
            <div v-for="item in TROUBLESHOOTING" :key="item.q" class="trouble-item">
              <b>{{ item.q }}</b>
              <p>{{ item.a }}</p>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 4. Skills 技能包 -->
    <section id="skills" class="section">
      <div class="section-inner">
        <div class="section-head">
          <span class="section-kicker">03 / 技能包</span>
          <h2>安装 Skills，让 AI 按财税 SOP 办事</h2>
          <p>结合 MCP 使用，覆盖从底账比对到税表预填的完整流程。</p>
        </div>

        <div class="skill-layout">
          <div class="skill-install">
            <h3><i class="ri-install-line" /> 安装步骤</h3>
            <ol class="install-steps">
              <li v-for="(step, idx) in SKILL_INSTALL_STEPS" :key="step.title">
                <span>{{ idx + 1 }}</span>
                <div>
                  <b>{{ step.title }}</b>
                  <p>{{ step.detail }}</p>
                </div>
              </li>
            </ol>
            <div class="install-path">
              <div class="install-path-head">
                <i class="ri-folder-open-line" />
                <span>技能包路径</span>
                <button
                  class="copy-mini dark"
                  type="button"
                  @click="copyText('clone', `git clone ${GITHUB_URL}.git && cd solobooks-open/skills`)"
                >
                  <i :class="copiedKey === 'clone' ? 'ri-check-line' : 'ri-file-copy-line'" />
                  {{ copiedKey === 'clone' ? '已复制' : '复制克隆命令' }}
                </button>
              </div>
              <code>solobooks-open/skills/solobooks-tax-filing/SKILL.md</code>
            </div>
          </div>

          <div class="skill-showcase">
            <div class="skill-showcase-head">
              <span class="skill-badge">标杆技能</span>
              <h3>solobooks-tax-filing</h3>
              <p>半自动纳税申报伴侣 · 需连接 SoloBooks MCP 与浏览器 MCP</p>
            </div>
            <div class="skill-flow">
              <div v-for="item in TAX_SKILL_STEPS" :key="item.step" class="skill-flow-item">
                <span class="flow-num">{{ item.step }}</span>
                <div>
                  <b>{{ item.title }}</b>
                  <p>{{ item.detail }}</p>
                </div>
              </div>
            </div>
            <div class="skill-stop">
              <i class="ri-hand-line" />
              <span>AI 停在「提交申报」前输出核对清单，最终由老板本人点击提交。</span>
            </div>
          </div>
        </div>

        <div class="roadmap">
          <span class="roadmap-label"><i class="ri-road-map-line" /> 规划中</span>
          <div v-for="item in ROADMAP_SKILLS" :key="item.title" class="roadmap-item">
            <b>{{ item.title }}</b>
            <p>{{ item.detail }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- 5. 开源与自托管 -->
    <section id="opensource" class="section section-alt">
      <div class="section-inner">
        <div class="section-head">
          <span class="section-kicker">04 / 开源与部署</span>
          <h2>开源仓库与私有化部署</h2>
        </div>

        <div class="oss-grid">
          <article class="oss-card repo">
            <i class="ri-github-fill oss-icon" />
            <h3>{{ GITHUB_REPO }}</h3>
            <p>前后端、MCP 服务与技能包同仓开源，欢迎 Issue 与 PR。</p>
            <div class="repo-actions">
              <a class="btn-primary" :href="GITHUB_URL" target="_blank" rel="noreferrer">
                <i class="ri-star-line" /> Star 仓库
                <span v-if="stars !== null" class="star-count">{{ stars }}</span>
              </a>
              <a class="btn-outline" :href="`${GITHUB_URL}/issues`" target="_blank" rel="noreferrer">
                <i class="ri-chat-3-line" /> 提 Issue
              </a>
            </div>
          </article>

          <article class="oss-card">
            <i class="ri-server-line oss-icon" />
            <h3>Docker Compose 自托管</h3>
            <div class="oss-cmd">
              <code>docker compose -f deploy/docker-compose.yml up -d</code>
              <button
                class="copy-mini"
                type="button"
                @click="copyText('docker', 'docker compose -f deploy/docker-compose.yml up -d')"
              >
                <i :class="copiedKey === 'docker' ? 'ri-check-line' : 'ri-file-copy-line'" />
                {{ copiedKey === 'docker' ? '已复制' : '' }}
              </button>
            </div>
            <p class="oss-note">反代配置 <code>proxy_buffering off</code> 与 <code>proxy_read_timeout 600s</code>。</p>
          </article>

          <article class="oss-card">
            <i class="ri-team-line oss-icon" />
            <h3>社区共建</h3>
            <ul class="oss-list">
              <li><i class="ri-git-pull-request-line" /> 欢迎贡献各省电子税局适配、行业科目与对账规则</li>
              <li><i class="ri-file-text-line" /> 技能包 PR 请附 SKILL.md 与最小复现步骤</li>
            </ul>
          </article>
        </div>
      </div>
    </section>

    <!-- 页脚 -->
    <footer class="site-footer">
      <div class="footer-inner">
        <div class="footer-left">
          <b>梭罗记账 SoloBooks</b>
          <span>AI 连接中心 · MCP &amp; Agent Skills</span>
        </div>
        <div class="footer-links">
          <a href="#" @click.prevent="router.push('/landing')">官网首页</a>
          <button class="link-btn" type="button" @click="goTokenPage">令牌管理</button>
          <a :href="GITHUB_URL" target="_blank" rel="noreferrer">GitHub</a>
        </div>
        <div class="footer-filings">
          <span>© {{ new Date().getFullYear() }} 芥子星辰（杭州）智能科技有限公司</span>
          <a href="https://beian.miit.gov.cn" target="_blank" rel="noreferrer">{{ ICP_LICENSE }}</a>
          <a :href="PSB_RECORD_URL" target="_blank" rel="noreferrer">{{ PSB_RECORD }}</a>
        </div>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.mcp-guide-page {
  min-height: 100vh;
  background: #f8fafc;
  color: #0f172a;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Noto Sans SC', sans-serif;
  /* 长端点、长路径、代码片段都可能撑宽容器：页面级兜底，避免窄屏横向滚动 */
  overflow-x: hidden;
}

/* 单列塌陷后 grid 子项默认 min-width:auto，长内容会把整行顶宽。
   统一归零，让内部的 ellipsis / overflow-x 真正生效。 */
.hero-copy,
.quick-card,
.concept-card,
.oss-card,
.panel-left,
.panel-right,
.skill-install,
.skill-showcase {
  min-width: 0;
}

.hero-sub,
.concept-card p,
.safety-item p,
.step-list p,
.tip-list li,
.trouble-item p {
  overflow-wrap: anywhere;
}

/* ── 顶部导航（与官网同构）── */
.site-nav {
  position: sticky;
  top: 0;
  z-index: 100;
  height: 56px;
  background: rgba(255, 255, 255, 0.94);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid #e2e8f0;
}

.nav-inner {
  max-width: 1120px;
  height: 100%;
  margin: 0 auto;
  padding: 0 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.nav-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
  flex-shrink: 0;
}

.nav-logo {
  width: 30px;
  height: 30px;
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
  gap: 22px;
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
  white-space: nowrap;
  flex-shrink: 0;
  transition: color 0.18s;
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

/* ── 按钮 ── */
.btn-primary,
.btn-outline,
.btn-ghost {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  padding: 7px 14px;
  cursor: pointer;
  text-decoration: none;
  transition: background 0.18s, color 0.18s, border-color 0.18s, box-shadow 0.18s;
  border: 1px solid transparent;
  font-family: inherit;
}

.btn-primary {
  background: #1747b0;
  color: #ffffff;
  border-color: #1747b0;
  box-shadow: 0 1px 2px rgba(23, 71, 176, 0.24);
}

.btn-primary:hover {
  background: #2b66e2;
  border-color: #2b66e2;
}

.btn-outline {
  background: #ffffff;
  color: #182230;
  border-color: #dde3ea;
}

.btn-outline:hover {
  border-color: #1747b0;
  color: #1747b0;
}

.btn-ghost {
  background: transparent;
  color: #586474;
  border-color: transparent;
}

.btn-ghost:hover {
  color: #1747b0;
  background: #f1f4f8;
}

.btn-lg {
  font-size: 14px;
  padding: 11px 20px;
  border-radius: 10px;
}

.star-count {
  font-variant-numeric: tabular-nums;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 0 7px;
  font-size: 11.5px;
  line-height: 1.7;
}

.btn-outline .star-count {
  background: #f1f4f8;
  color: #586474;
}

.link-btn {
  background: none;
  border: none;
  padding: 0;
  color: #1747b0;
  font-size: inherit;
  font-family: inherit;
  cursor: pointer;
  text-decoration: none;
}

.link-btn:hover {
  text-decoration: underline;
}

/* ── Hero ── */
.hero {
  position: relative;
  overflow: hidden;
  border-bottom: 1px solid #e2e8f0;
  background:
    radial-gradient(circle at 50% -10%, rgba(23, 71, 176, 0.1) 0%, rgba(30, 127, 251, 0.03) 45%, transparent 72%),
    linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
  padding: 64px 20px 72px;
}

.hero-glow {
  position: absolute;
  top: -180px;
  left: 50%;
  transform: translateX(-50%);
  width: 1000px;
  height: 560px;
  background: radial-gradient(circle, rgba(23, 71, 176, 0.08) 0%, transparent 68%);
  pointer-events: none;
}

.hero-inner {
  position: relative;
  z-index: 1;
  max-width: 1120px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr);
  gap: 48px;
  align-items: center;
}

.hero-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: #1747b0;
  background: #edf3ff;
  border: 1px solid #d6e2fb;
  border-radius: 999px;
  padding: 4px 11px;
  margin-bottom: 18px;
}

.hero-title {
  margin: 0 0 16px;
  font-size: 40px;
  line-height: 1.24;
  font-weight: 700;
  letter-spacing: -1px;
  color: #0b1526;
}

.hero-title em {
  font-style: normal;
  background: linear-gradient(96deg, #1747b0 0%, #2b66e2 58%, #4f8bf5 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.hero-sub {
  margin: 0 0 24px;
  font-size: 14.5px;
  line-height: 1.72;
  color: #475569;
  max-width: 500px;
}

.hero-cta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

/* 速览卡（端点 + JSON 配置） */
.quick-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 18px;
  box-shadow: 0 14px 40px rgba(15, 23, 42, 0.07);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.quick-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 650;
  color: #0f172a;
}

.quick-meta {
  margin-left: auto;
  font-size: 11px;
  font-weight: 500;
  color: #636e7c;
}

.quick-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #16a34a;
  box-shadow: 0 0 0 3px rgba(22, 163, 74, 0.14);
}

.endpoint-box {
  background: #0f172a;
  border-radius: 10px;
  padding: 11px 13px;
}

.endpoint-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.endpoint-label {
  font-size: 10.5px;
  letter-spacing: 0.04em;
  color: #93c5fd;
  font-weight: 600;
}

.endpoint-url {
  display: block;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  color: #e2e8f0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.quick-json {
  margin: 0;
  overflow-x: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.55;
  color: #cbd5e1;
}

.quick-json code {
  font-family: inherit;
  white-space: pre;
}

.quick-jump {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 500;
  color: #1747b0;
  text-decoration: none;
  padding-top: 2px;
}

.quick-jump:hover {
  text-decoration: underline;
}

.copy-mini {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid #334155;
  background: #1e293b;
  color: #cbd5e1;
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 6px;
  cursor: pointer;
  flex-shrink: 0;
  font-family: inherit;
  transition: background 0.16s, color 0.16s;
}

.copy-mini:hover {
  background: #27364f;
  color: #fff;
}

.copy-mini.dark {
  border-color: #cbd5e1;
  background: #ffffff;
  color: #475569;
}

.copy-mini.dark:hover {
  border-color: #1747b0;
  color: #1747b0;
}

/* ── 通用 section ── */
.section {
  padding: 52px 20px;
  scroll-margin-top: 56px;
}

.section-alt {
  background: #ffffff;
  border-top: 1px solid #e2e8f0;
  border-bottom: 1px solid #e2e8f0;
}

.section-inner {
  max-width: 1120px;
  margin: 0 auto;
}

.section-head {
  max-width: 720px;
  margin-bottom: 24px;
}

.section-kicker {
  display: block;
  font-size: 11.5px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #1747b0;
  margin-bottom: 7px;
}

.section-head h2 {
  margin: 0 0 8px;
  font-size: 25px;
  line-height: 1.3;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: #0b1526;
}

.section-head p {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.65;
  color: #586474;
}

/* ── 概念卡 ── */
.concept-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.concept-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.concept-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.concept-icon {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  flex-shrink: 0;
}

.concept-icon.blue {
  background: #edf3ff;
  color: #1747b0;
}

.concept-icon.purple {
  background: #f1eefe;
  color: #4c3fa8;
}

.concept-tag {
  display: block;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: #636e7c;
  margin-bottom: 2px;
}

.concept-card h3 {
  margin: 0;
  font-size: 16.5px;
  font-weight: 680;
  color: #0b1526;
}

.concept-card p {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: #475569;
}

.concept-card code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  background: #f1f4f8;
  border-radius: 4px;
  padding: 1px 5px;
  color: #1747b0;
}

/* ── 安全红线（深色块）── */
.safety-panel {
  margin-top: 18px;
  background: #0f172a;
  border-radius: 14px;
  padding: 20px 22px;
  color: #e2e8f0;
}

.safety-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 18px;
}

.safety-item-head {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 6px;
}

.safety-item-head i {
  font-size: 15px;
  color: #93c5fd;
  flex-shrink: 0;
}

.safety-item-head b {
  font-size: 12.5px;
  color: #f1f5f9;
}

.safety-item p {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.65;
  color: #94a3b8;
}

/* ── 工具清单 ── */
.tools-panel {
  margin-top: 18px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 20px 22px;
}

.tools-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-bottom: 16px;
}

.tools-head h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  color: #0b1526;
}

.tools-head span {
  font-size: 12px;
  color: #636e7c;
}

.tools-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 18px 26px;
}

.tool-group-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding-bottom: 7px;
  border-bottom: 1px solid #edf0f4;
}

.tool-group-head i {
  font-size: 14px;
  color: #1747b0;
}

.tool-group-head b {
  font-size: 12.5px;
  color: #182230;
}

.tool-count {
  margin-left: auto;
  font-size: 10.5px;
  color: #636e7c;
  background: #f1f4f8;
  border-radius: 999px;
  padding: 0 6px;
  font-variant-numeric: tabular-nums;
}

.tool-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 6px;
  margin-top: 10px;
}

.tool-chips code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10.5px;
  line-height: 1.7;
  color: #1747b0;
  background: #f6f8fb;
  border: 1px solid #e8edf4;
  border-radius: 5px;
  padding: 1px 6px;
}

.tool-group.write .tool-group-head i {
  color: #a05c10;
}

.tool-group.write .tool-group-head b {
  color: #7c4708;
}

.tool-group.write .tool-chips code {
  color: #7c4708;
  background: #fdf7ee;
  border-color: #f2e4cf;
}

/* 五组工具排成 3 + 2 时，最后一行右侧会空出一格。
   让「写入草稿」横跨两列，第二行铺满，视觉上不再像没做完。 */
.tool-group.write {
  grid-column: span 2;
}

/* ── 令牌注入 ── */
.inject-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  background: #ffffff;
  border: 1px solid #d6e2fb;
  border-radius: 12px;
  padding: 14px 18px;
  margin-bottom: 20px;
}

.inject-copy b {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #0b1526;
}

.inject-copy b i {
  color: #1747b0;
}

.inject-copy p {
  margin: 4px 0 0;
  font-size: 11.5px;
  color: #586474;
  line-height: 1.6;
}

.inject-ok {
  color: #0f7354 !important;
  display: flex;
  align-items: center;
  gap: 4px;
}

.inject-copy code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  background: #f1f4f8;
  border-radius: 4px;
  padding: 1px 4px;
}

.inject-input {
  position: relative;
  flex-shrink: 0;
  width: 320px;
}

.inject-input input {
  width: 100%;
  border: 1px solid #dde3ea;
  border-radius: 8px;
  padding: 8px 62px 8px 11px;
  font-size: 12.5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: #182230;
  background: #ffffff;
  outline: none;
  transition: border-color 0.16s, box-shadow 0.16s;
}

.inject-input input:focus {
  border-color: #1747b0;
  box-shadow: 0 0 0 3px rgba(23, 71, 176, 0.1);
}

.inject-input input::placeholder {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #94a3b8;
}

.inject-clear {
  position: absolute;
  right: 7px;
  top: 50%;
  transform: translateY(-50%);
  border: none;
  background: #f1f4f8;
  color: #586474;
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 5px;
  cursor: pointer;
  font-family: inherit;
}

.inject-clear:hover {
  background: #e2e8f0;
}

.inject-warn {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: -12px 0 18px;
  font-size: 11.5px;
  color: #a05c10;
}

/* ── 客户端 Tab（单行横向滚动 + aria-selected 指示）── */
.client-tabs {
  display: flex;
  flex-wrap: nowrap;
  overflow-x: auto;
  scrollbar-width: none;
  gap: 8px;
  margin-bottom: 18px;
}

.client-tabs::-webkit-scrollbar {
  display: none;
}

.client-tab {
  position: relative;
  display: flex;
  align-items: center;
  gap: 9px;
  border: 1px solid #e2e8f0;
  background: #ffffff;
  border-radius: 10px;
  padding: 9px 14px;
  min-height: 54px;
  flex: 1 0 auto;
  min-width: 170px;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  transition: border-color 0.18s, background 0.18s;
}

.client-tab > i {
  font-size: 17px;
  color: #586474;
  flex-shrink: 0;
}

.client-tab-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.client-tab-text b {
  font-size: 12.5px;
  color: #182230;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.client-tab-text em {
  font-style: normal;
  font-size: 10.5px;
  color: #94a3b8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.client-tab:hover {
  border-color: #b9cdf5;
}

.client-tab[aria-selected='true'] {
  border-color: #1747b0;
  background: #f5f8ff;
}

.client-tab[aria-selected='true']::after {
  content: '';
  position: absolute;
  inset: auto 10px 0;
  border-bottom: 2.5px solid #1747b0;
}

.client-tab.active > i,
.client-tab.active b {
  color: #1747b0;
}

.client-tab.active em {
  color: #6b8ec9;
}

/* ── 客户端面板 ── */
.client-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.05fr);
  gap: 34px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 26px;
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.panel-head h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 680;
  color: #0b1526;
}

.panel-vendor {
  font-size: 11px;
  color: #636e7c;
}

.transport-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10.5px;
  color: #1747b0;
  background: #edf3ff;
  border-radius: 999px;
  padding: 3px 9px;
  line-height: 1.6;
  max-width: 250px;
  flex-shrink: 0;
}

.step-list {
  margin: 4px 0 18px;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.step-list li {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.step-num {
  font-size: 10.5px;
  font-weight: 700;
  color: #1747b0;
  background: #edf3ff;
  border-radius: 5px;
  padding: 2px 5px;
  flex-shrink: 0;
  margin-top: 1px;
  font-variant-numeric: tabular-nums;
}

.step-list b {
  font-size: 12.5px;
  color: #182230;
}

.step-list p {
  margin: 3px 0 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: #586474;
}

.verify-box {
  display: flex;
  gap: 9px;
  background: #f0faf5;
  border: 1px solid #d3ecdf;
  border-radius: 10px;
  padding: 11px 13px;
  margin-bottom: 14px;
}

.verify-box i {
  color: #0f7354;
  font-size: 15px;
  margin-top: 1px;
}

.verify-box b {
  font-size: 12px;
  color: var(--solo-income);
}

.verify-box p {
  margin: 2px 0 0;
  font-size: 11.5px;
  line-height: 1.65;
  color: #2f6d5a;
}

.tip-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tip-list li {
  display: flex;
  gap: 7px;
  font-size: 11.5px;
  line-height: 1.65;
  color: #586474;
}

.tip-list i {
  color: #a05c10;
  font-size: 13px;
  margin-top: 1px;
  flex-shrink: 0;
}

.panel-right {
  min-width: 0;
}

.panel-right-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.panel-right-head > span:first-child {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 650;
  color: #182230;
}

.panel-right-head i {
  color: #1747b0;
}

.os-hint {
  font-size: 10.5px;
  color: #636e7c;
  background: #f1f4f8;
  border-radius: 999px;
  padding: 2px 8px;
}

.panel-code {
  margin-bottom: 12px;
}

.panel-footnote {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  margin: 4px 0 0;
  font-size: 11px;
  color: #636e7c;
  line-height: 1.6;
}

/* ── 排错 ── */
.trouble-panel {
  margin-top: 22px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 22px 24px;
}

.trouble-panel > h3 {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 16px;
  font-size: 14px;
  font-weight: 650;
  color: #0b1526;
}

.trouble-panel > h3 i {
  color: #a05c10;
}

.trouble-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px 28px;
}

.trouble-item b {
  font-size: 12px;
  color: #182230;
  line-height: 1.6;
}

.trouble-item p {
  margin: 4px 0 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: #586474;
}

/* ── Skills ── */
.skill-layout {
  display: grid;
  grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.15fr);
  gap: 26px;
  align-items: start;
}

.skill-install {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 24px;
}

.skill-install > h3 {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0 0 18px;
  font-size: 14.5px;
  font-weight: 650;
  color: #0b1526;
}

.skill-install > h3 i {
  color: #1747b0;
}

.install-steps {
  margin: 0 0 18px;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.install-steps li {
  display: flex;
  gap: 10px;
}

.install-steps li > span {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #edf3ff;
  color: #1747b0;
  font-size: 11px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 1px;
}

.install-steps b {
  font-size: 12.5px;
  color: #182230;
}

.install-steps p {
  margin: 3px 0 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: #586474;
}

.install-path {
  background: #f6f8fb;
  border: 1px dashed #dde3ea;
  border-radius: 10px;
  padding: 11px 12px;
}

.install-path-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #636e7c;
  margin-bottom: 7px;
}

.install-path-head i {
  color: #1747b0;
}

.install-path-head .copy-mini {
  margin-left: auto;
}

.install-path > code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11.5px;
  color: #1747b0;
  overflow-wrap: anywhere;
}

.skill-showcase {
  background: #0f172a;
  border-radius: 14px;
  padding: 24px;
  color: #e2e8f0;
}

.skill-badge {
  display: inline-block;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: #4ade80;
  border: 1px solid rgba(74, 222, 128, 0.35);
  background: rgba(74, 222, 128, 0.1);
  border-radius: 999px;
  padding: 2px 8px;
  margin-bottom: 10px;
}

.skill-showcase-head h3 {
  margin: 0 0 3px;
  font-size: 17px;
  font-weight: 650;
  color: #ffffff;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.skill-showcase-head p {
  margin: 0 0 16px;
  font-size: 12px;
  color: #94a3b8;
}

.skill-flow {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding-left: 2px;
}

.skill-flow-item {
  display: flex;
  gap: 11px;
  position: relative;
}

.skill-flow-item:not(:last-child)::before {
  content: '';
  position: absolute;
  left: 11px;
  top: 24px;
  bottom: -14px;
  width: 1px;
  background: #334155;
}

.flow-num {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #1e293b;
  border: 1px solid #334155;
  color: #93c5fd;
  font-size: 10.5px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.skill-flow-item b {
  font-size: 12.5px;
  color: #f1f5f9;
}

.skill-flow-item p {
  margin: 3px 0 0;
  font-size: 11.5px;
  line-height: 1.72;
  color: #94a3b8;
}

.skill-stop {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid #1e293b;
  font-size: 11.5px;
  line-height: 1.7;
  color: #cbd5e1;
}

.skill-stop i {
  color: #fcd34d;
  font-size: 14px;
  margin-top: 1px;
}

.roadmap {
  display: flex;
  align-items: center;
  gap: 24px;
  margin-top: 24px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 16px 22px;
}

.roadmap-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  font-weight: 650;
  color: #636e7c;
  flex-shrink: 0;
}

.roadmap-label i {
  color: #1747b0;
}

.roadmap-item {
  flex: 1;
  min-width: 0;
  border-left: 1px solid #edf0f4;
  padding-left: 22px;
}

.roadmap-item b {
  font-size: 12.5px;
  color: #182230;
}

.roadmap-item p {
  margin: 3px 0 0;
  font-size: 11.5px;
  color: #586474;
  line-height: 1.6;
}

/* ── 开源 ── */
.oss-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
}

.oss-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 22px;
  display: flex;
  flex-direction: column;
}

.oss-icon {
  font-size: 20px;
  color: #1747b0;
  margin-bottom: 12px;
}

.oss-card h3 {
  margin: 0 0 7px;
  font-size: 14.5px;
  font-weight: 650;
  color: #0b1526;
  word-break: break-all;
}

.oss-card > p {
  margin: 0 0 14px;
  font-size: 12px;
  line-height: 1.72;
  color: #586474;
  flex: 1;
}

.oss-card code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  background: #f1f4f8;
  border-radius: 4px;
  padding: 1px 4px;
  color: #1747b0;
}

.repo-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.oss-cmd {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #0f172a;
  border-radius: 8px;
  padding: 9px 10px;
  margin-bottom: 10px;
}

.oss-cmd code {
  flex: 1;
  min-width: 0;
  background: none;
  padding: 0;
  font-size: 10.5px;
  color: #e2e8f0;
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.6;
}

.oss-note {
  margin: 0 !important;
  font-size: 11px !important;
  color: #636e7c;
}

.oss-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.oss-list li {
  display: flex;
  gap: 6px;
  font-size: 11.5px;
  line-height: 1.65;
  color: #586474;
}

.oss-list i {
  color: #1747b0;
  font-size: 13px;
  margin-top: 1px;
  flex-shrink: 0;
}

/* ── 页脚 ── */
.site-footer {
  border-top: 1px solid #e2e8f0;
  background: #ffffff;
  /* 留出右下角悬浮「公测中」入口的空间，滚动到底部时不遮挡备案号 */
  padding: 28px 20px 72px;
}

.footer-inner {
  max-width: 1120px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  flex-wrap: wrap;
}

.footer-left {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.footer-left b {
  font-size: 13px;
  color: #0f172a;
}

.footer-left span {
  font-size: 11px;
  color: #636e7c;
}

.footer-links {
  display: flex;
  gap: 18px;
  align-items: center;
}

.footer-links a,
.footer-links .link-btn {
  font-size: 12px;
  color: #586474;
  text-decoration: none;
}

.footer-links a:hover,
.footer-links .link-btn:hover {
  color: #1747b0;
}

.footer-filings {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 10.5px;
  color: #94a3b8;
}

.footer-filings a {
  color: #94a3b8;
  text-decoration: none;
}

.footer-filings a:hover {
  color: #586474;
}

/* ── 响应式 ── */
@media (max-width: 1000px) {
  .hero-inner {
    grid-template-columns: 1fr;
    gap: 32px;
  }

  .hero-title {
    font-size: 33px;
  }

  .concept-grid {
    grid-template-columns: 1fr;
    gap: 14px;
  }

  .safety-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .client-panel,
  .skill-layout {
    grid-template-columns: 1fr;
    gap: 24px;
  }

  .oss-grid {
    grid-template-columns: 1fr;
  }

  .trouble-grid {
    grid-template-columns: 1fr;
  }

  .tool-group.write {
    grid-column: span 1;
  }

  .roadmap {
    flex-direction: column;
    align-items: stretch;
    gap: 14px;
  }

  .roadmap-item {
    border-left: none;
    border-top: 1px solid #edf0f4;
    padding-left: 0;
    padding-top: 12px;
  }
}

@media (max-width: 720px) {
  .nav-links {
    display: none;
  }

  .hero {
    padding: 40px 16px 48px;
  }

  .hero-title {
    font-size: 28px;
  }

  .section {
    padding: 44px 16px;
  }

  .inject-bar {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }

  .inject-input {
    width: 100%;
  }

  .safety-grid {
    grid-template-columns: 1fr;
  }

  .client-panel,
  .tools-panel,
  .trouble-panel,
  .skill-install,
  .skill-showcase {
    padding: 18px;
  }

  .panel-head {
    flex-direction: column;
    gap: 8px;
  }

  .footer-inner {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
