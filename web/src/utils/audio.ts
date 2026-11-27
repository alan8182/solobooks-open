// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.

/** 将浏览器录制的任意音频（如双声道 48kHz webm/opus）重采样为 16kHz 单声道 PCM WAV，适配 DashScope ASR。 */
export async function blobToWav16kMono(blob: Blob): Promise<Blob> {
  const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!AudioCtx || typeof OfflineAudioContext === 'undefined') {
    return blob
  }
  const ctx = new AudioCtx()
  try {
    const arrayBuffer = await blob.arrayBuffer()
    const decoded = await ctx.decodeAudioData(arrayBuffer)
    const targetSampleRate = 16000
    const frameCount = Math.max(1, Math.ceil(decoded.duration * targetSampleRate))
    const offline = new OfflineAudioContext(1, frameCount, targetSampleRate)
    const source = offline.createBufferSource()
    source.buffer = decoded
    source.connect(offline.destination)
    source.start(0)
    const rendered = await offline.startRendering()
    return encodePcm16Wav(rendered.getChannelData(0), targetSampleRate)
  } finally {
    await ctx.close().catch(() => {})
  }
}

/** 将单声道浮点采样数组编码为 16-bit PCM WAV Blob。 */
export function encodePcm16Wav(samples: Float32Array, sampleRate = 16000): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const writeStr = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i))
  }
  writeStr(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  writeStr(8, 'WAVE')
  writeStr(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeStr(36, 'data')
  view.setUint32(40, samples.length * 2, true)

  let offset = 44
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true)
    offset += 2
  }
  return new Blob([buffer], { type: 'audio/wav' })
}
