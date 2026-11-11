/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import LandingPageV2 from '@/pages/landing/v2.vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}))

const wrappers: ReturnType<typeof mount>[] = []

beforeEach(() => {
  window.scrollTo = vi.fn()
})

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  document.body.innerHTML = ''
})

describe('LandingPageV2 (精简清爽版首页) 移动端适配与交互', () => {
  it('正确渲染移动端汉堡切换按钮，并可展开/折叠抽屉导航', async () => {
    const wrapper = mount(LandingPageV2, { attachTo: document.body })
    wrappers.push(wrapper)

    const menuBtn = wrapper.find('.mobile-menu-btn')
    expect(menuBtn.exists()).toBe(true)

    // 默认抽屉菜单处于折叠状态
    expect(wrapper.find('.mobile-drawer').exists()).toBe(false)

    // 点击打开抽屉菜单
    await menuBtn.trigger('click')
    await nextTick()

    expect(wrapper.find('.mobile-drawer').exists()).toBe(true)
    const links = wrapper.findAll('.mobile-drawer-links a')
    expect(links.length).toBeGreaterThanOrEqual(6)
    expect(wrapper.text()).toContain('核心优势')
    expect(wrapper.text()).toContain('功能展台')
    expect(wrapper.text()).toContain('AI 连接 (MCP & Skills)')

    // 再次点击按钮或遮罩可关闭
    await menuBtn.trigger('click')
    await nextTick()
    expect(wrapper.find('.mobile-drawer').exists()).toBe(false)
  })

  it('点击抽屉导航项触发锚点导航并自动收起抽屉', async () => {
    const wrapper = mount(LandingPageV2, { attachTo: document.body })
    wrappers.push(wrapper)

    // 展开抽屉
    await wrapper.find('.mobile-menu-btn').trigger('click')
    await nextTick()
    expect(wrapper.find('.mobile-drawer').exists()).toBe(true)

    // 点击某一个锚点链接
    const firstLink = wrapper.find('.mobile-drawer-links a')
    await firstLink.trigger('click')
    await nextTick()

    // 抽屉应自动收拢
    expect(wrapper.find('.mobile-drawer').exists()).toBe(false)
  })

  it('首屏 Bento 看板包含 4 大精简指标卡片', () => {
    const wrapper = mount(LandingPageV2, { attachTo: document.body })
    wrappers.push(wrapper)

    const bentoCards = wrapper.findAll('.hero-proof-bento .proof-card')
    expect(bentoCards.length).toBe(4)
    expect(wrapper.find('.hero-proof-bento').text()).toContain('点一点 · 问一问')
    expect(wrapper.find('.hero-proof-bento').text()).toContain('公私合规隔离')
    expect(wrapper.find('.hero-proof-bento').text()).toContain('小企业准则')
    expect(wrapper.find('.hero-proof-bento').text()).toContain('MCP & Skills')
  })

  it('精简核心叙事文案准确落地：无葫芦话与废话套话', () => {
    const wrapper = mount(LandingPageV2, { attachTo: document.body })
    wrappers.push(wrapper)

    // 顶部胶囊
    expect(wrapper.find('.hero-kicker-pill').text()).toContain('AI 财务官')

    // 四大支柱精炼标题
    expect(wrapper.text()).toContain('四个底气，告别外包糊涂账')

    // 对比表精炼标题
    expect(wrapper.text()).toContain('为什么不用传统代账？')

    // 账本迁移精炼标题
    expect(wrapper.text()).toContain('前任代账账套，一键导入洗干净')
  })

  it('展台 Tab 切换：智能做账、财税咨询与协审看板按需渲染', async () => {
    const wrapper = mount(LandingPageV2, { attachTo: document.body })
    wrappers.push(wrapper)

    const tabs = wrapper.findAll('.showcase-tab-btn')
    expect(tabs.length).toBe(5)

    // 默认 Tab 01：建议气泡与点一点采纳
    expect(wrapper.find('.agent-advice-bubble').exists()).toBe(true)
    expect(wrapper.text()).toContain('点一点采纳')

    // Tab 02：财税咨询对话
    await tabs[1].trigger('click')
    await nextTick()
    expect(wrapper.find('.advisor-dialog-body').exists()).toBe(true)
    expect(wrapper.text()).toContain('合规解决路径')

    // Tab 04：资产信用与协审
    await tabs[3].trigger('click')
    await nextTick()
    expect(wrapper.find('.audit-share-banner').exists()).toBe(true)
    expect(wrapper.text()).toContain('Solo Audit Share')
  })

  it('数据安全与合规板块：渲染 4 大企业级安全防护卡片', () => {
    const wrapper = mount(LandingPageV2, { attachTo: document.body })
    wrappers.push(wrapper)

    const trustSection = wrapper.find('#trust')
    expect(trustSection.exists()).toBe(true)
    expect(trustSection.text()).toContain('企业级数据安全')

    const cards = trustSection.findAll('.evidence-card')
    expect(cards.length).toBe(4)
    expect(trustSection.text()).toContain('仅本人可访问')
    expect(trustSection.text()).toContain('AI 不用企业数据训练')
    expect(trustSection.text()).toContain('全链路双向加密')
    expect(trustSection.text()).toContain('数据自主，绝不锁客')
  })

  it('开源社区与商业定价板块：完整渲染 3 档分层卡片及 Docker 一键启动命令', () => {
    const wrapper = mount(LandingPageV2, { attachTo: document.body })
    wrappers.push(wrapper)

    const pricingSection = wrapper.find('#pricing')
    expect(pricingSection.exists()).toBe(true)
    expect(pricingSection.text()).toContain('你可以把数据留在自己电脑里，也可以交给我们全自动打理')

    const cards = pricingSection.findAll('.pricing-card')
    expect(cards.length).toBe(3)

    // 开源社区版
    expect(pricingSection.text()).toContain('开源社区版')
    expect(pricingSection.text()).toContain('永久免费')

    // 商业云端版 598
    expect(pricingSection.text()).toContain('商业云端版')
    expect(pricingSection.text()).toContain('598')
    expect(pricingSection.text()).toContain('开箱即用 · 每日仅 ¥1.6')

    // 人机双签护航版 1980
    expect(pricingSection.text()).toContain('人机双签护航版')
    expect(pricingSection.text()).toContain('1,980')
    expect(pricingSection.text()).toContain('持牌财税机构联审')

    // Docker callout
    const dockerCallout = pricingSection.find('.docker-callout')
    expect(dockerCallout.exists()).toBe(true)
    expect(dockerCallout.text()).toContain('docker run -d -p 8000:8000')
  })

  it('Docker 一键复制命令正常触发并更新反馈文案', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    })

    const wrapper = mount(LandingPageV2, { attachTo: document.body })
    wrappers.push(wrapper)

    const copyBtn = wrapper.find('.docker-copy-btn')
    expect(copyBtn.exists()).toBe(true)
    expect(copyBtn.text()).toContain('复制')

    await copyBtn.trigger('click')
    await nextTick()

    expect(writeTextMock).toHaveBeenCalledWith(
      expect.stringContaining('docker run -d -p 8000:8000 --name solobooks jiezixingchen/solobooks:latest'),
    )
    expect(copyBtn.text()).toContain('已复制')
  })

  it('创始人手记板块：真实呈现政府园区入驻、研发补助背景与小微三难切身经历', () => {
    const wrapper = mount(LandingPageV2, { attachTo: document.body })
    wrappers.push(wrapper)

    const storySection = wrapper.find('#story')
    expect(storySection.exists()).toBe(true)
    expect(storySection.text()).toContain('原本，这只是我们写给自己用的工具')
    expect(storySection.text()).toContain('政府科技园区')
    expect(storySection.text()).toContain('官方研发补助支持')
    expect(storySection.text()).toContain('找代账糊涂、自记账繁琐、招会计太贵')
    expect(storySection.text()).toContain('资深 CPA 联合打磨')
    expect(storySection.text()).toContain('多行业创业者测试中')
  })

  it('展台 Tab 01：呈现分录三层识别机制（硬规则+AI推导+专属记忆）', () => {
    const wrapper = mount(LandingPageV2, { attachTo: document.body })
    wrappers.push(wrapper)

    expect(wrapper.text()).toContain('① 硬规则直解')
    expect(wrapper.text()).toContain('② AI 语义推导')
    expect(wrapper.text()).toContain('③ 专属记忆沉淀')
  })
})
