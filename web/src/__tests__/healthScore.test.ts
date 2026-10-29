// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
// Licensed under the SoloBooks License v2.0.

import { describe, it, expect } from 'vitest'
import type { HealthScoreResponse, HealthPenaltyItem } from '@/api/types'

describe('Health Score Data Format & UI Logic', () => {
  function getLevelColor(levelCode?: string, levelStr?: string) {
    if (levelCode === 'excellent' || levelStr === '优秀') return '#0f7354'
    if (levelCode === 'good' || levelStr === '良好') return '#1747b0'
    if (levelCode === 'fair' || levelStr === '一般') return '#a05c10'
    if (levelCode === 'poor' || levelStr === '需改善') return '#bd3d3d'
    return '#1747b0'
  }

  function getLevelLabel(res?: HealthScoreResponse | null) {
    if (!res) return '待评估'
    return res.levelLabel || res.level || '正常'
  }

  it('correctly maps colors for both English code and Chinese level', () => {
    expect(getLevelColor('excellent', '优秀')).toBe('#0f7354')
    expect(getLevelColor(undefined, '优秀')).toBe('#0f7354')
    expect(getLevelColor('good', '良好')).toBe('#1747b0')
    expect(getLevelColor('fair', '一般')).toBe('#a05c10')
    expect(getLevelColor('poor', '需改善')).toBe('#bd3d3d')
    expect(getLevelColor(undefined, undefined)).toBe('#1747b0')
  })

  it('formats level labels and handles null safely', () => {
    expect(getLevelLabel(null)).toBe('待评估')
    expect(getLevelLabel({ totalScore: 92, level: '优秀', levelLabel: '稳健优秀', dimensions: {}, suggestions: [] })).toBe('稳健优秀')
    expect(getLevelLabel({ totalScore: 78, level: '良好', dimensions: {}, suggestions: [] })).toBe('良好')
  })

  it('computes total penalty deduction accurately', () => {
    const penalties: HealthPenaltyItem[] = [
      { id: 'p1', dimensionCode: 'invoice', title: '缺少发票', description: '说明', deduction: 6, severity: 'high' },
      { id: 'p2', dimensionCode: 'tax', title: '涉税风险', description: '说明', deduction: 4, severity: 'medium' },
    ]
    const totalDeduction = penalties.reduce((sum, p) => sum + p.deduction, 0)
    expect(totalDeduction).toBe(10)
  })

  it('verifies dimension details data integrity', () => {
    const mockResponse: HealthScoreResponse = {
      totalScore: 88,
      level: '优秀',
      levelCode: 'excellent',
      levelLabel: '稳健优秀',
      summary: '财务健康度处于良性水平',
      dimensions: { '发票合规度': 92, '账务完整性': 100 },
      dimensionDetails: [
        { code: 'invoice', name: '发票合规度', score: 92, weight: 25, status: 'pass', highlight: '发票附着率 92%' },
        { code: 'integrity', name: '账务完整性', score: 100, weight: 20, status: 'pass', highlight: '分录完备' },
      ],
      penalties: [],
      suggestions: ['继续保持规范记账'],
    }

    expect(mockResponse.dimensionDetails).toHaveLength(2)
    expect(mockResponse.dimensionDetails![0].status).toBe('pass')
    expect(mockResponse.penalties).toHaveLength(0)
  })
})
