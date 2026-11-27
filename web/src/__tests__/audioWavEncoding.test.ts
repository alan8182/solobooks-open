// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.
import { describe, expect, it } from 'vitest'
import { encodePcm16Wav } from '@/utils/audio'

describe('encodePcm16Wav', () => {
  it('encodes 16kHz mono PCM16 WAV header and clamped samples', async () => {
    const samples = new Float32Array([0, 0.5, -0.5, 1.5, -1.5])
    const blob = encodePcm16Wav(samples, 16000)
    expect(blob.type).toBe('audio/wav')
    expect(blob.size).toBe(44 + samples.length * 2)

    const buf = await blob.arrayBuffer()
    const view = new DataView(buf)
    const tag = (offset: number) =>
      String.fromCharCode(view.getUint8(offset), view.getUint8(offset + 1), view.getUint8(offset + 2), view.getUint8(offset + 3))

    expect(tag(0)).toBe('RIFF')
    expect(tag(8)).toBe('WAVE')
    expect(tag(12)).toBe('fmt ')
    expect(view.getUint16(20, true)).toBe(1) // PCM
    expect(view.getUint16(22, true)).toBe(1) // mono
    expect(view.getUint32(24, true)).toBe(16000)
    expect(view.getUint16(34, true)).toBe(16)
    expect(tag(36)).toBe('data')
    expect(view.getInt16(44, true)).toBe(0)
    expect(view.getInt16(50, true)).toBe(0x7fff)
    expect(view.getInt16(52, true)).toBe(-0x8000)
  })
})
