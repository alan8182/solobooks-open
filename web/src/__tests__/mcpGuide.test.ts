/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 *
 * AI 连接中心（/ai-connect）内容层测试。
 *
 * 这里守的是「页面上的技术事实不能和代码现状脱节」：
 * 工具总数与白名单一致、每类客户端的配置都能生成完整代码、系统识别不出错。
 */

import { describe, expect, it } from 'vitest'
import {
  CLIENT_GUIDES,
  READ_TOOL_COUNT,
  READ_TOOL_GROUPS,
  TOKEN_PLACEHOLDER,
  TOOL_TOTAL,
  WRITE_TOOLS,
  detectOS,
  remoteConfig,
  resolveEndpoint,
} from '@/pages/mcp-guide/data'

const ctx = {
  endpoint: 'https://solobooks.cn/mcp',
  token: 'sbk_test_token_value',
  repoUrl: 'https://github.com/alan8182/solobooks-open',
}

describe('resolveEndpoint', () => {
  it('拼出同域 /mcp 端点', () => {
    expect(resolveEndpoint('https://solobooks.cn')).toBe('https://solobooks.cn/mcp')
  })

  it('吃掉 origin 尾部多余的斜杠', () => {
    expect(resolveEndpoint('https://solobooks.cn///')).toBe('https://solobooks.cn/mcp')
  })
})

describe('remoteConfig', () => {
  it('写入端点与 Bearer 令牌', () => {
    const json = remoteConfig(ctx.endpoint, ctx.token)
    expect(json).toContain('"url": "https://solobooks.cn/mcp"')
    expect(json).toContain('"Authorization": "Bearer sbk_test_token_value"')
  })

  it('未注入令牌时保留占位符，不编造令牌', () => {
    const json = remoteConfig(ctx.endpoint, TOKEN_PLACEHOLDER)
    expect(json).toContain(TOKEN_PLACEHOLDER)
    expect(json).not.toContain('sbk_')
  })

  it('是合法 JSON', () => {
    expect(() => JSON.parse(remoteConfig(ctx.endpoint, ctx.token))).not.toThrow()
  })
})

describe('工具清单与后端白名单一致', () => {
  it('总数 = 只读 + 草稿 = 24', () => {
    expect(READ_TOOL_COUNT + WRITE_TOOLS.length).toBe(TOOL_TOTAL)
    expect(TOOL_TOTAL).toBe(24)
  })

  it('写入工具全部是 propose_ 前缀（对应后端 MCP_WRITE_TOOLS）', () => {
    expect(WRITE_TOOLS.every((name) => name.startsWith('propose_'))).toBe(true)
  })

  it('24 个工具名互不重复', () => {
    const all = [...READ_TOOL_GROUPS.flatMap((g) => g.tools), ...WRITE_TOOLS]
    expect(all).toHaveLength(TOOL_TOTAL)
    expect(new Set(all).size).toBe(all.length)
  })
})

describe('客户端矩阵', () => {
  it('至少覆盖 5 类客户端且 id 唯一', () => {
    expect(CLIENT_GUIDES.length).toBeGreaterThanOrEqual(5)
    const ids = CLIENT_GUIDES.map((c) => c.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it.each(CLIENT_GUIDES.map((c) => [c.id, c] as const))('%s 的配置代码完整可复制', (_id, client) => {
    const samples = client.samples(ctx)
    expect(samples.length).toBeGreaterThan(0)
    for (const sample of samples) {
      expect(sample.code.length).toBeGreaterThan(0)
      // 模板拼接漏变量时最容易出现这两个词
      expect(sample.code).not.toContain('undefined')
      expect(sample.code).not.toContain('null')
    }
    // 远程配置类客户端必须把端点带进代码块
    expect(samples.some((s) => s.code.includes(ctx.endpoint))).toBe(true)
  })

  it('每个客户端都有步骤、验证方式与提示', () => {
    for (const client of CLIENT_GUIDES) {
      expect(client.steps.length).toBeGreaterThanOrEqual(3)
      expect(client.verify.length).toBeGreaterThan(0)
      expect(client.tips.length).toBeGreaterThan(0)
    }
  })
})

describe('detectOS', () => {
  it('识别 macOS', () => {
    expect(
      detectOS('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'),
    ).toBe('macos')
  })

  it('识别 Windows', () => {
    expect(detectOS('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')).toBe('windows')
  })

  it('其他系统不误判（避免高亮错路径）', () => {
    expect(detectOS('Mozilla/5.0 (X11; Linux x86_64)')).toBe('other')
  })
})
