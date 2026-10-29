// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import {
  SoloStatusBadge,
  SoloButton,
  SoloEmpty,
  SoloSkeleton,
  SoloTable,
} from '@/components/ui'

describe('SoloDesignSystem UI Components', () => {
  describe('SoloStatusBadge', () => {
    it('renders with default neutral tone and dot', () => {
      const wrapper = mount(SoloStatusBadge, {
        slots: { default: '待处理' },
      })
      expect(wrapper.text()).toContain('待处理')
      expect(wrapper.classes()).toContain('badge-neutral')
      expect(wrapper.find('.badge-dot').exists()).toBe(true)
    })

    it('renders success tone without dot when dot=false', () => {
      const wrapper = mount(SoloStatusBadge, {
        props: { tone: 'success', dot: false },
        slots: { default: '已对账' },
      })
      expect(wrapper.classes()).toContain('badge-success')
      expect(wrapper.find('.badge-dot').exists()).toBe(false)
    })
  })

  describe('SoloButton', () => {
    it('emits click event when not disabled and not loading', async () => {
      const wrapper = mount(SoloButton, {
        props: { variant: 'primary' },
        slots: { default: '确定' },
      })
      await wrapper.trigger('click')
      expect(wrapper.emitted('click')).toHaveLength(1)
      expect(wrapper.classes()).toContain('btn-primary')
    })

    it('does not emit click when disabled or loading', async () => {
      const wrapper = mount(SoloButton, {
        props: { disabled: true },
        slots: { default: '禁用' },
      })
      await wrapper.trigger('click')
      expect(wrapper.emitted('click')).toBeUndefined()
    })
  })

  describe('SoloEmpty', () => {
    it('renders title, description and action slot', () => {
      const wrapper = mount(SoloEmpty, {
        props: {
          title: '暂无发票',
          description: '点击下方按钮上传',
        },
        slots: {
          action: '<button id="test-btn">立即上传</button>',
        },
      })
      expect(wrapper.text()).toContain('暂无发票')
      expect(wrapper.text()).toContain('点击下方按钮上传')
      expect(wrapper.find('#test-btn').exists()).toBe(true)
    })

    it('默认是 guide 型：带图标块和标题', () => {
      const wrapper = mount(SoloEmpty)
      expect(wrapper.classes()).toContain('is-guide')
      expect(wrapper.find('.empty-icon-wrap').exists()).toBe(true)
      expect(wrapper.text()).toContain('暂无数据')
    })

    it('hint 型：不渲染大图标块，也不让默认标题多说一层', () => {
      const wrapper = mount(SoloEmpty, {
        props: { variant: 'hint', description: '该期间没有发票记录' },
      })
      expect(wrapper.classes()).toContain('is-hint')
      expect(wrapper.find('.empty-icon-wrap').exists()).toBe(false)
      // 「暂无数据」是默认占位标题，hint 型下不该冒出来跟说明抢行
      expect(wrapper.find('.empty-title').exists()).toBe(false)
      expect(wrapper.text()).toContain('该期间没有发票记录')
    })

    it('hint 型给了明确标题时仍然显示', () => {
      const wrapper = mount(SoloEmpty, {
        props: { variant: 'hint', title: '本日无凭证', description: '换个月份看看' },
      })
      expect(wrapper.text()).toContain('本日无凭证')
      expect(wrapper.text()).toContain('换个月份看看')
    })
  })

  describe('SoloSkeleton', () => {
    it('renders stat variant', () => {
      const wrapper = mount(SoloSkeleton, {
        props: { variant: 'stat' },
      })
      expect(wrapper.find('.skeleton-stat').exists()).toBe(true)
    })

    it('renders table variant with specified rows', () => {
      const wrapper = mount(SoloSkeleton, {
        props: { variant: 'table', rows: 4 },
      })
      expect(wrapper.findAll('.skeleton-table-row')).toHaveLength(4)
    })
  })

  describe('SoloTable', () => {
    const columns = [
      { title: '名称', key: 'name' },
      { title: '金额', key: 'amount' },
    ]

    it('renders data rows properly', () => {
      const data = [
        { id: '1', name: '收入1', amount: '100.00' },
        { id: '2', name: '收入2', amount: '200.00' },
      ]
      const wrapper = mount(SoloTable, {
        props: { columns, data },
      })
      expect(wrapper.text()).toContain('收入1')
      expect(wrapper.text()).toContain('收入2')
      expect(wrapper.text()).toContain('名称')
    })

    it('renders empty state when data is empty', () => {
      const wrapper = mount(SoloTable, {
        props: {
          columns,
          data: [],
          emptyTitle: '暂无记录',
          emptyDescription: '请先导入数据',
        },
        slots: {
          'empty-action': '<button id="import-btn">导入</button>',
        },
      })
      expect(wrapper.text()).toContain('暂无记录')
      expect(wrapper.text()).toContain('请先导入数据')
      expect(wrapper.find('#import-btn').exists()).toBe(true)
    })
  })
})
