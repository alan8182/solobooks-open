/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import EmployeeModal from '@/pages/mine/salary/components/EmployeeModal.vue'

vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }),
}))

function mountModal() {
  mount(EmployeeModal, {
    attachTo: document.body,
    props: { show: true, employee: null, departmentOptions: [] },
  })
  return document.body.textContent || ''
}

async function expandAdvanced() {
  document.querySelector<HTMLButtonElement>('.advanced-toggle-btn')!.click()
  await new Promise((resolve) => setTimeout(resolve, 0))
  return document.body.textContent || ''
}

describe('员工社保基数输入', () => {
  it('不再给写死的「最低基数 4000」，改为提示按当地标准填写', () => {
    // 最低缴费基数各城市不同（杭州 2026 年已不是 4000），写死在预设里会把人带错。
    const text = mountModal()

    expect(text).not.toContain('最低基数 4000')
    expect(text).toContain('最低缴费基数各地不同，按当地社保局公布的标准填写。')
    // 仍然保留两个不依赖地域的快捷项
    expect(text).toContain('按全额工资')
    expect(text).toContain('不交社保 (0)')

    document.body.innerHTML = ''
  })
})

describe('入职日期', () => {
  it('放在常规字段里，不再藏在选填项', () => {
    // 入职日期决定个税累计减除费用的起算月，年中入职不填会把预扣税估高。
    const text = mountModal()

    expect(text).toContain('入职日期')
    expect(text).toContain('影响个税累计减除费用的起算月份；不填按首个工资月计算')
    expect(text).toContain('更多选填项（个税专项附加 / 业主标记）')
    expect(text).not.toContain('更多选填项（个税专项附加 / 入职日期 / 业主标记）')
    // 选填项折叠时，专项附加的说明不该出现（说明它确实还在折叠区里）
    expect(text).not.toContain('员工在「个人所得税 App」自己填报')

    document.body.innerHTML = ''
  })
})

describe('专项附加说明', () => {
  it('写给老板看：员工在个税 App 自己填，这里只用于估算', async () => {
    mountModal()
    const text = await expandAdvanced()

    expect(text).toContain('就是「专项附加扣除」，员工在「个人所得税 App」自己填报')
    expect(text).toContain('只用来估算要代扣多少工资个税')
    expect(text).toContain('实际申报以税务系统自动带出的为准')
    // 扣除金额会随政策和地区变化，不在表单里写死
    expect(text).not.toContain('子女教育 2000')
    expect(text).not.toContain('赡养老人 3000')

    document.body.innerHTML = ''
  })
})
