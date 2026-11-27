/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { NFormItem, NModal, NSelect } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import { reactive } from 'vue'
import Companies from '@/pages/mine/companies.vue'

const harness = vi.hoisted(() => ({
  route: null as any,
  replace: vi.fn(),
  push: vi.fn(),
  auth: null as any,
  company: null as any,
  warning: vi.fn(),
  success: vi.fn(),
  dialogWarning: vi.fn(),
  deleteCompany: vi.fn(),
  requestStepUp: vi.fn(),
  recognizeLicense: vi.fn(),
  sendVerificationCode: vi.fn(),
  taxRows: [] as Array<Record<string, unknown>>,
}))

vi.mock('vue-router', () => ({
  useRoute: () => harness.route,
  useRouter: () => ({ replace: harness.replace, push: harness.push }),
}))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => harness.auth }))
vi.mock('@/stores/company', () => ({ useCompanyStore: () => harness.company }))
vi.mock('@/api/auth', () => ({
  updateCompany: vi.fn(),
  deleteCompany: (...args: unknown[]) => harness.deleteCompany(...args),
  recognizeCompanyLicense: (...args: unknown[]) => harness.recognizeLicense(...args),
  requestStepUp: (...args: unknown[]) => harness.requestStepUp(...args),
}))
vi.mock('@/utils/verificationCode', () => ({
  sendVerificationCode: (...args: unknown[]) => harness.sendVerificationCode(...args),
}))
vi.mock('@/api/tax-registration', () => ({
  listTaxRegistrations: async () => ({ items: harness.taxRows }),
}))
vi.mock('@/api/industryTemplates', () => ({
  getIndustryTemplates: async () => ({ items: [] }),
}))
vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => ({
    warning: harness.warning,
    success: harness.success,
    error: vi.fn(),
    info: vi.fn(),
  }),
  useDialog: () => ({
    warning: harness.dialogWarning,
    error: vi.fn(),
    success: vi.fn(),
  }),
}))

function makeRoute(query: Record<string, string>) {
  return reactive({
    path: '/mine/companies',
    query: { ...query } as Record<string, string>,
  })
}

async function mountPage(
  query: Record<string, string>,
  companies: Array<Record<string, unknown>> = [{ company_id: 'c_demo', name: '演示账套', isDemo: true }],
) {
  harness.route = makeRoute(query)
  harness.auth = {
    companies,
    user: { companies },
    currentCompanyId: 'c_demo',
    switchCompanyContext: vi.fn(),
  }
  harness.company = { setCompanies: vi.fn(), create: vi.fn() }
  harness.auth.fetchProfile = vi.fn(async () => undefined)
  const wrapper = mount(Companies, { global: { stubs: { teleport: true } } })
  await flushPromises()
  return wrapper
}

function createModal(wrapper: ReturnType<typeof mount>) {
  return wrapper
    .findAllComponents(NModal)
    .find((modal) => modal.props('title') === '新增主体')
}

async function fillAndSubmit(
  wrapper: Awaited<ReturnType<typeof mountPage>>,
  opts: { name: string; taxId?: string; date: string; taxPlaceholder: string },
) {
  await wrapper.find('input[placeholder="请输入公司全称"]').setValue(opts.name)
  if (opts.taxId) {
    await wrapper.find(`input[placeholder="${opts.taxPlaceholder}"]`).setValue(opts.taxId)
  }
  const dateInput = wrapper.find('input[placeholder="选择账套启用日期"]')
  await dateInput.setValue(opts.date)
  await dateInput.trigger('blur')
  // 注册地是新建主体的必填项（决定跳哪个省的电子税务局）
  const regionItem = wrapper.findAllComponents(NFormItem).find((item) => item.text().includes('注册地'))
  expect(regionItem, '新建主体表单缺注册地').toBeTruthy()
  regionItem!.findComponent(NSelect).vm.$emit('update:value', '浙江省')
  await flushPromises()

  const submit = wrapper.findAll('button').find((b) => b.text() === '确认创建')
  expect(submit).toBeTruthy()
  await submit!.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  harness.replace.mockClear()
  harness.push.mockClear()
  harness.dialogWarning.mockClear()
  harness.warning.mockClear()
  harness.deleteCompany.mockReset()
  harness.requestStepUp.mockReset()
  harness.requestStepUp.mockResolvedValue({ stepUpToken: 'su-token', expiresIn: 300 })
  harness.sendVerificationCode.mockReset()
  harness.sendVerificationCode.mockResolvedValue({ ok: true, bizId: 'biz-1' })
  harness.taxRows = []
  harness.recognizeLicense.mockReset()
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('新增主体深链', () => {
  it('?create=migration 直接打开新增主体并预设代账转入', async () => {
    const wrapper = await mountPage({ create: 'migration' })

    const modal = createModal(wrapper)
    expect(modal).toBeTruthy()
    expect(modal!.props('show')).toBe(true)
    // 两个建账场景平铺成卡片，不再藏在下拉里
    expect(modal!.findAll('.onboarding-option')).toHaveLength(2)
    expect(modal!.find('.onboarding-option.active')!.text()).toContain('已记账公司')
    // 读一次即清参数，刷新不再反复弹窗
    expect(harness.replace).toHaveBeenCalledWith({ path: '/mine/companies', query: {} })
  })

  it('?create=new 预设新公司从零建账，税号仍标示必填', async () => {
    const wrapper = await mountPage({ create: 'new' })

    const modal = createModal(wrapper)
    expect(modal!.props('show')).toBe(true)
    expect(modal!.find('.onboarding-option.active')!.text()).toContain('新成立公司')
    expect(modal!.findAll('button.onboarding-option')).toHaveLength(2)
    // 税号是报税必填项，标签上的星号不能少
    const taxItem = modal!.findAllComponents(NFormItem).find((item) => item.text().includes('纳税人识别号'))
    expect(taxItem!.props('required')).toBe(true)
  })

  it('点击卡片可以切换建账情况', async () => {
    const wrapper = await mountPage({ create: 'new' })
    const modal = createModal(wrapper)

    await modal!.findAll('.onboarding-option')[1].trigger('click')

    expect(modal!.find('.onboarding-option.active')!.text()).toContain('已记账公司')
    expect(modal!.findAll('.onboarding-option')[0].classes()).not.toContain('active')
  })

  it('非法 create 值不打开弹窗', async () => {
    const wrapper = await mountPage({ create: 'onboarding' })

    expect(createModal(wrapper)?.props('show')).toBe(false)
    expect(harness.replace).not.toHaveBeenCalled()
  })
})

describe('新成立公司启用日期默认带入成立日期', () => {
  type Wrapper = Awaited<ReturnType<typeof mountPage>>

  async function setDate(modal: ReturnType<typeof createModal>, placeholder: string, value: string) {
    const input = modal!.find(`input[placeholder="${placeholder}"]`)
    expect(input.exists()).toBe(true)
    await input.setValue(value)
    await input.trigger('blur')
    await flushPromises()
  }

  async function openCreate(): Promise<Wrapper> {
    return mountPage({ create: 'new' })
  }

  it('填写成立日期后启用日期自动带入，并提示来源', async () => {
    const wrapper = await openCreate()
    const modal = createModal(wrapper)!

    await setDate(modal, '选择公司成立日期（选填）', '2026-03-15')

    const startInput = modal.find('input[placeholder="选择账套启用日期"]')
    expect((startInput.element as HTMLInputElement).value).toBe('2026-03-15')
    expect(modal.text()).toContain('已按公司成立日期带入')
  })

  it('成立日期变化时，仍为自动值的启用日期跟着更新', async () => {
    const wrapper = await openCreate()
    const modal = createModal(wrapper)!

    await setDate(modal, '选择公司成立日期（选填）', '2026-03-15')
    await setDate(modal, '选择公司成立日期（选填）', '2026-04-01')

    const startInput = modal.find('input[placeholder="选择账套启用日期"]')
    expect((startInput.element as HTMLInputElement).value).toBe('2026-04-01')
  })

  it('启用日期原样确认自动值时，联动不会被断开', async () => {
    const wrapper = await openCreate()
    const modal = createModal(wrapper)!

    await setDate(modal, '选择公司成立日期（选填）', '2026-03-15')
    await setDate(modal, '选择账套启用日期', '2026-03-15')
    await setDate(modal, '选择公司成立日期（选填）', '2026-04-01')

    const startInput = modal.find('input[placeholder="选择账套启用日期"]')
    expect((startInput.element as HTMLInputElement).value).toBe('2026-04-01')
  })

  it('用户手改启用日期后，不再被成立日期覆盖', async () => {
    const wrapper = await openCreate()
    const modal = createModal(wrapper)!

    await setDate(modal, '选择账套启用日期', '2026-06-01')
    await setDate(modal, '选择公司成立日期（选填）', '2026-03-15')

    const startInput = modal.find('input[placeholder="选择账套启用日期"]')
    expect((startInput.element as HTMLInputElement).value).toBe('2026-06-01')
    expect(modal.text()).not.toContain('已按公司成立日期带入')
  })

  it('已记账公司不自动带入', async () => {
    const wrapper = await mountPage({ create: 'migration' })
    const modal = createModal(wrapper)!

    await setDate(modal, '选择公司成立日期（选填）', '2026-03-15')

    const startInput = modal.find('input[placeholder="选择账套启用日期"]')
    expect((startInput.element as HTMLInputElement).value).toBe('')
  })
})

describe('营业执照识别预填', () => {
  const OCR_RESULT = {
    name: '杭州芥子星辰科技有限公司',
    taxId: '91330110MA2KEHHR0T',
    taxIdValid: true,
    entityType: '有限责任公司(自然人独资)',
    incorporationDate: '2019-05-20',
    address: '浙江省杭州市余杭区文一西路 1500 号',
    businessScope: '技术服务',
    legalRepresentative: '张三',
    confidence: 0.92,
    warnings: [],
  }

  async function upload(wrapper: Awaited<ReturnType<typeof mountPage>>, name = 'license.png') {
    const input = wrapper.find('input.license-ocr-input')
    expect(input.exists(), '新建主体缺营业执照上传入口').toBe(true)
    const file = new File([new Uint8Array([1, 2, 3])], name, { type: 'image/png' })
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushPromises()
  }

  it('识别后预填空白项，成立日期带入启用日期并提示核对', async () => {
    harness.recognizeLicense.mockResolvedValue(OCR_RESULT)
    const wrapper = await mountPage({ create: 'new' })
    const modal = createModal(wrapper)!

    await upload(wrapper)

    expect((wrapper.find('input[placeholder="请输入公司全称"]').element as HTMLInputElement).value)
      .toBe('杭州芥子星辰科技有限公司')
    expect((wrapper.find('input[placeholder="15/17/18/20 位字母数字"]').element as HTMLInputElement).value)
      .toBe('91330110MA2KEHHR0T')
    // 新成立公司：启用日期跟随识别出的成立日期
    expect((wrapper.find('input[placeholder="选择账套启用日期"]').element as HTMLInputElement).value)
      .toBe('2019-05-20')
    // 注册地从住所推断，必须在状态行里请用户核对
    const status = modal.find('.license-ocr-status')
    expect(status.text()).toContain('已填入 5 项：公司名称 · 纳税人识别号')
    expect(status.text()).toContain('推断注册地为「浙江省」')
    // 值要真的进到下拉里（filterable NSelect 曾出现「值写了、框里空白」）
    const regionItem = modal.findAllComponents(NFormItem).find((item) => item.text().includes('注册地'))!
    expect(regionItem.findComponent(NSelect).props('value')).toBe('浙江省')
  })

  it('用户已填的公司名不被识别结果覆盖', async () => {
    harness.recognizeLicense.mockResolvedValue(OCR_RESULT)
    const wrapper = await mountPage({ create: 'new' })
    const nameInput = wrapper.find('input[placeholder="请输入公司全称"]')
    await nameInput.setValue('我自己填的公司')

    await upload(wrapper)

    expect((nameInput.element as HTMLInputElement).value).toBe('我自己填的公司')
    expect(wrapper.find('.license-ocr-status').text()).toContain('保留了你已填的 1 项：公司名称')
  })

  it('识别失败只提示原因，新建主体照常可手填', async () => {
    harness.recognizeLicense.mockRejectedValue({
      response: { status: 422, data: { detail: '没识别出营业执照信息，请换一张更清晰的照片，或直接手动填写' } },
    })
    const wrapper = await mountPage({ create: 'new' })

    await upload(wrapper)

    const status = wrapper.find('.license-ocr-status')
    expect(status.classes()).toContain('is-error')
    expect(status.text()).toContain('没识别出营业执照信息')
    // 上传框仍可用，不阻断手填与创建
    expect(wrapper.find('button').exists()).toBe(true)
    expect((wrapper.find('input[placeholder="请输入公司全称"]').element as HTMLInputElement).value).toBe('')
  })
})

describe('建账引导创建主体', () => {
  it('代账转入：创建后切到新主体并落到期初建账工作台', async () => {
    const wrapper = await mountPage({ create: 'migration' })
    harness.company.create.mockResolvedValue({ company_id: 'c_new', name: '测试公司' })

    await fillAndSubmit(wrapper, {
      name: '测试公司',
      taxId: '91330110MA2KEHHR0T',
      date: '2026-11-01',
      taxPlaceholder: '15/17/18/20 位字母数字',
    })

    expect(harness.company.create).toHaveBeenCalledWith(
      expect.objectContaining({ onboardingType: 'migration', name: '测试公司' }),
    )
    expect(harness.auth.switchCompanyContext).toHaveBeenCalledWith('c_new')
    expect(harness.push).toHaveBeenCalledWith('/mine/opening')
    expect(harness.dialogWarning).not.toHaveBeenCalled()
  })

  it('新公司：税号留空直接拦下，不创建也不切主体', async () => {
    const wrapper = await mountPage({ create: 'new' })

    await fillAndSubmit(wrapper, {
      name: '新公司',
      date: '2026-11-01',
      taxPlaceholder: '15/17/18/20 位字母数字',
    })

    expect(harness.company.create).not.toHaveBeenCalled()
    expect(harness.warning).toHaveBeenCalledWith('请输入纳税人识别号')
    expect(harness.auth.switchCompanyContext).not.toHaveBeenCalled()
    expect(harness.push).not.toHaveBeenCalled()
  })

  it('新公司：填全税号后创建、切换并进入月度结账', async () => {
    const wrapper = await mountPage({ create: 'new' })
    harness.company.create.mockResolvedValue({ company_id: 'c_new', name: '新公司' })

    await fillAndSubmit(wrapper, {
      name: '新公司',
      taxId: '91330110MA2KEHHR0T',
      date: '2026-11-01',
      taxPlaceholder: '15/17/18/20 位字母数字',
    })

    expect(harness.company.create).toHaveBeenCalledWith(
      expect.objectContaining({ onboardingType: 'new', taxId: '91330110MA2KEHHR0T', province: '浙江省', city: '' }),
    )
    expect(harness.auth.switchCompanyContext).toHaveBeenCalledWith('c_new')
    // 新成立公司建账完成后落点优先前往税费种认定
    expect(harness.push).toHaveBeenCalledWith('/tax-registration')
    expect(harness.warning).not.toHaveBeenCalled()
  })
})

describe('主体管理页直接创建主体后的下一步行动指引', () => {
  it('创建成功后展示下一步行动卡，点击税费种认定跳转到 /tax-registration', async () => {
    const wrapper = await mountPage({})
    const newBtn = wrapper.findAll('button').find((b) => b.text().includes('新增主体'))
    expect(newBtn).toBeDefined()
    await newBtn!.trigger('click')

    harness.company.create.mockResolvedValue({ company_id: 'c_auto', name: '自动测试主体' })

    await fillAndSubmit(wrapper, {
      name: '自动测试主体',
      taxId: '91330110MA2KEHHR0T',
      date: '2026-11-01',
      taxPlaceholder: '15/17/18/20 位字母数字',
    })

    // 弹窗未直接消失，而是呈现下一步行动卡
    expect(wrapper.text()).toContain('主体创建成功')
    expect(wrapper.text()).toContain('同步电子税务局税费种认定')
    expect(wrapper.text()).toContain('推荐第一步')

    // 点击前往税费种认定卡片
    const taxActionCard = wrapper.findAll('.next-step-card').find((card) => card.text().includes('税费种认定'))
    expect(taxActionCard).toBeDefined()
    await taxActionCard!.trigger('click')

    expect(harness.push).toHaveBeenCalledWith('/tax-registration')
  })
})

describe('删除主体后列表即时更新', () => {
  const twoCompanies = () => [
    { company_id: 'c_demo', name: '演示账套', isDemo: true },
    { company_id: 'c_old', name: '待删除公司', isDemo: false },
  ]

  // 卡片渲染的是 onMounted 那一份拷贝，必须在挂载时就给到两份主体
  function mountWithTwoCompanies() {
    return mountPage({}, twoCompanies())
  }

  /** 走完「风险确认 → 输入名称 → 最终确认」，点的是「待删除公司」那张卡。 */
  type Wrapper = Awaited<ReturnType<typeof mountPage>>

  async function clickButton(wrapper: Wrapper, text: string) {
    const button = wrapper.findAll('button').find((btn) => btn.text() === text)
    expect(button).toBeTruthy()
    await button!.trigger('click')
    await flushPromises()
  }

  async function openDeleteConfirm(wrapper: Wrapper) {
    const card = wrapper.findAll('.company-card').find((node) => node.text().includes('待删除公司'))
    expect(card).toBeTruthy()
    await card!.find('button[aria-label="删除主体"]').trigger('click')
    await flushPromises()

    await clickButton(wrapper, '继续')
    await wrapper.find('input[placeholder="请输入完整主体名称"]').setValue('待删除公司')
    await flushPromises()
    await clickButton(wrapper, '下一步')
    // 永久删除必须完成短信二次认证
    await wrapper.find('input[placeholder="账号绑定手机号"]').setValue('13900001111')
    await clickButton(wrapper, '发送验证码')
    await wrapper.find('input[placeholder="6 位短信验证码"]').setValue('123456')
    await flushPromises()
    await clickButton(wrapper, '永久删除')
  }

  function stubProfileWithoutDeleted() {
    harness.auth.fetchProfile = vi.fn(async () => {
      const remaining = [{ company_id: 'c_demo', name: '演示账套', isDemo: true }]
      harness.auth.companies = remaining
      harness.auth.user.companies = remaining
    })
  }

  it('删除成功后卡片立即消失，不需要整页刷新', async () => {
    const wrapper = await mountWithTwoCompanies()
    expect(wrapper.text()).toContain('待删除公司')
    stubProfileWithoutDeleted()

    await openDeleteConfirm(wrapper)

    expect(harness.requestStepUp).toHaveBeenCalledWith({
      action: 'delete_company',
      companyId: 'c_old',
      phone: '13900001111',
      code: '123456',
      bizId: 'biz-1',
    })
    expect(harness.deleteCompany).toHaveBeenCalledWith('c_old', '待删除公司', 'su-token')
    expect(harness.company.setCompanies).toHaveBeenLastCalledWith([
      expect.objectContaining({ company_id: 'c_demo' }),
    ])
    // 根因回归：本地列表没重取时仍会渲染已删除的卡片，要整页刷新才消失
    expect(wrapper.text()).not.toContain('待删除公司')
    expect(wrapper.text()).toContain('演示账套')
  })

  it('删除后同步共享队列，其他页面不会读到已删主体', async () => {
    const wrapper = await mountWithTwoCompanies()
    stubProfileWithoutDeleted()

    await openDeleteConfirm(wrapper)

    // 挂载时同步过一次，删除后必须再同步一次（最后一条是删除后的结果）
    const lastCall = harness.company.setCompanies.mock.calls.at(-1)?.[0]
    expect(lastCall).toEqual([expect.objectContaining({ company_id: 'c_demo' })])
    expect(lastCall.some((co: { company_id: string }) => co.company_id === 'c_old')).toBe(false)
  })
})

describe('编辑主体：税费种认定接管申报周期', () => {
  const company = [{ company_id: 'c_edit', name: '芥子星辰', startDate: '2026-05-18' }]

  async function openEdit() {
    const wrapper = await mountPage({ edit: 'c_edit' }, company)
    const modal = wrapper
      .findAllComponents(NModal)
      .find((item) => item.props('title') === '编辑主体')
    expect(modal).toBeTruthy()
    return { modal: modal!, wrapper }
  }

  async function openEditWithoutSync() {
    return (await openEdit()).modal
  }

  it('认定表已同步时整块隐藏增值税申报周期，不再解释由谁接管', async () => {
    harness.taxRows = [
      { status: 'active', returnGroup: 'vat', filingCycle: 'quarter' },
      { status: 'active', returnGroup: 'business_income_iit_prepay', filingCycle: 'month' },
    ]
    const { modal } = await openEdit()

    // 只读一句「由认定表决定」没有动作价值：周期去税费种认定页看/改
    expect(modal.text()).not.toContain('增值税申报周期')
    expect(modal.text()).not.toContain('这里不再提供单独设置')
    expect(modal.text()).not.toContain('当前为 季报')
    // 必须是 NGi 一起消失：只删 NFormItem 会在两列网格里空出一个死角
    const cells = Array.from(modal.find('.n-grid').element.children)
    expect(cells.length).toBeGreaterThan(0)
    // 只删 NFormItem 会留下一个空白格子，两列布局里就是一块看不见的死角
    const cellTexts = cells.map((cell) => cell.textContent?.trim() ?? '')
    expect(cellTexts.filter((text) => text === '')).toHaveLength(0)
    // 隐藏后后续字段顶上：主体类型右边就是注册地，而不是空一格
    expect(cellTexts[2]).toContain('主体类型')
    expect(cellTexts[3]).toContain('注册地')
  })

  it('认定表未同步时仍保留下拉作为兜底', async () => {
    const modal = await openEditWithoutSync()

    const vatItem = modal.findAllComponents(NFormItem).find((item) => item.text().includes('增值税申报周期'))
    expect(vatItem!.find('.n-select').exists()).toBe(true)
    expect(vatItem!.text()).toContain('按主管税务机关核定的纳税期限填写')
  })
})
