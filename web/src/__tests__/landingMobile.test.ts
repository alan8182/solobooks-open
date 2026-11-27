/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import LandingPage from '@/pages/landing/index.vue'

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

describe('LandingPage（官网首页）移动端适配与交互', () => {
  it('正确渲染移动端汉堡切换按钮，并可展开/折叠抽屉导航', async () => {
    const wrapper = mount(LandingPage, { attachTo: document.body })
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
    const wrapper = mount(LandingPage, { attachTo: document.body })
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

  it('首屏不再重复四支柱：Hero 只保留双 CTA 与 AI 连接入口', () => {
    const wrapper = mount(LandingPage, { attachTo: document.body })
    wrappers.push(wrapper)

    // 原有 4 张 Bento 卡与下一屏「四个底气」重复，2026-11 精简时移除
    expect(wrapper.find('.hero-proof-bento').exists()).toBe(false)
    expect(wrapper.findAll('.proof-card').length).toBe(0)

    const hero = wrapper.find('.hero')
    expect(hero.find('.hero-cta').exists()).toBe(true)
    expect(hero.text()).toContain('免费开启干净账本')
    expect(hero.text()).toContain('90秒自查财税风险')

    // AI 连接入口保留，指向 /ai-connect
    const aiLink = hero.find('.hero-ai-link')
    expect(aiLink.exists()).toBe(true)
    expect(aiLink.text()).toContain('MCP & Skills')
  })

  it('顶部导航收敛为 5 项，低频入口不再占用导航', () => {
    const wrapper = mount(LandingPage, { attachTo: document.body })
    wrappers.push(wrapper)

    const navLinks = wrapper.findAll('.nav-links a')
    expect(navLinks.length).toBe(5)
    expect(navLinks.map((link) => link.text())).toEqual(['核心优势', '功能展台', '方案对比', '定价', '常见问题'])
    expect(navLinks.map((link) => link.attributes('href'))).toEqual([
      '#pillars',
      '#showcase',
      '#compare',
      '#pricing',
      '#faq',
    ])
  })

  it('精简核心叙事文案准确落地：无葫芦话与废话套话', () => {
    const wrapper = mount(LandingPage, { attachTo: document.body })
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
    const wrapper = mount(LandingPage, { attachTo: document.body })
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
    const wrapper = mount(LandingPage, { attachTo: document.body })
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

  it('开源社区与商业定价板块：完整渲染 3 档云端分层卡片及开源社区版 Docker 一键启动命令', () => {
    const wrapper = mount(LandingPage, { attachTo: document.body })
    wrappers.push(wrapper)

    const pricingSection = wrapper.find('#pricing')
    expect(pricingSection.exists()).toBe(true)
    expect(pricingSection.text()).toContain('你可以把数据留在自己电脑里，也可以交给我们全自动打理')

    const cards = pricingSection.findAll('.pricing-card')
    expect(cards.length).toBe(3)

    // 云端免费试用版（首月20万、次月起月10万Token）
    expect(pricingSection.text()).toContain('云端免费试用版')
    expect(pricingSection.text()).toContain('首月 20 万 Token')
    expect(pricingSection.text()).toContain('10 万')

    // 云端标准版 598（每年 1000 万 Token + 最多 3 个账套 + 1对1旧账导入 + 专属服务群）
    expect(pricingSection.text()).toContain('云端标准版')
    expect(pricingSection.text()).toContain('598')
    // 文案压缩后保留全部事实（额度 / 账套 / 旧账导入 / 研发辅助账），只删修饰语
    expect(pricingSection.text()).toContain('每年 1000 万 Token')
    expect(pricingSection.text()).toContain('最多 3 账套')
    expect(pricingSection.text()).toContain('1对1 历史旧账导入')
    expect(pricingSection.text()).toContain('研发费用辅助账')

    // 人机双签版 2980（中级注册财务会计师每季度审账 + 季报 + 30分钟视频问答 + 日常问答）
    expect(pricingSection.text()).toContain('人机双签版')
    expect(pricingSection.text()).toContain('2,980')
    expect(pricingSection.text()).toContain('每季度审账')
    expect(pricingSection.text()).toContain('季度财务分析报告')
    expect(pricingSection.text()).toContain('30 分钟视频问答')

    // 开源社区版 Docker callout
    const dockerCallout = pricingSection.find('.docker-callout')
    expect(dockerCallout.exists()).toBe(true)
    expect(dockerCallout.text()).toContain('开源社区版')
    expect(dockerCallout.text()).toContain('docker run -d -p 8000:8000')
  })

  it('Docker 一键复制命令正常触发并更新反馈文案', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    })

    const wrapper = mount(LandingPage, { attachTo: document.body })
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

  it('创始人手记板块：保留自建缘由与背书，去掉与方案对比重复的「三难」', () => {
    const wrapper = mount(LandingPage, { attachTo: document.body })
    wrappers.push(wrapper)

    const storySection = wrapper.find('#story')
    expect(storySection.exists()).toBe(true)
    expect(storySection.text()).toContain('原本，这只是我们写给自己用的工具')
    expect(storySection.text()).toContain('政府科技园区')
    expect(storySection.text()).toContain('官方研发补助支持')
    expect(storySection.text()).toContain('资深 CPA 联合打磨')

    // 「找代账糊涂 / 自记账繁琐 / 招会计太贵」与 #compare 的专职会计、传统代账两列重复，已删除
    expect(storySection.findAll('.pain-box').length).toBe(0)
    expect(storySection.text()).not.toContain('找代账糊涂')
  })

  it('去重：场景区无自造指标、安全卡不重复标签、迁移与底部无重复承诺', () => {
    const wrapper = mount(LandingPage, { attachTo: document.body })
    wrappers.push(wrapper)

    // 98 分健康度 / 10 秒导入这类无口径指标已移除
    expect(wrapper.findAll('.p-metrics').length).toBe(0)
    expect(wrapper.find('#personas').findAll('.persona-item').length).toBe(4)

    // 安全卡底部的 points 只是 desc 同义改写，已移除
    expect(wrapper.findAll('.evidence-points').length).toBe(0)
    expect(wrapper.find('#trust').findAll('.evidence-card').length).toBe(4)

    // 迁移区 promises 与卡片标签重复、底部 CTA 的开放共测条与手记重复，均已移除
    expect(wrapper.find('.migrate-promises').exists()).toBe(false)
    expect(wrapper.find('.cta-co-build-bar').exists()).toBe(false)
  })

  it('展台 Tab 01：呈现分录三层识别机制（硬规则+AI推导+专属记忆）', () => {
    const wrapper = mount(LandingPage, { attachTo: document.body })
    wrappers.push(wrapper)

    expect(wrapper.text()).toContain('① 硬规则直解')
    expect(wrapper.text()).toContain('② AI 语义推导')
    expect(wrapper.text()).toContain('③ 专属记忆沉淀')
  })

  it('价值观融入：体现芥子星辰消灭机械劳动主张、人机拍板边界与母品牌入口', () => {
    const wrapper = mount(LandingPage, { attachTo: document.body })
    wrappers.push(wrapper)

    const storySection = wrapper.find('#story')
    expect(storySection.text()).toContain('AI 替代的从来不是人，而是消耗生命的机械劳动')
    expect(storySection.text()).toContain('芥子星辰')

    const trustSection = wrapper.find('#trust')
    expect(trustSection.text()).toContain('拍板权与方向盘永远属于你')

    const footer = wrapper.find('.site-footer')
    expect(footer.text()).toContain('芥子星辰')
    expect(footer.text()).toContain('签小抄 VisaNote')
  })
})
