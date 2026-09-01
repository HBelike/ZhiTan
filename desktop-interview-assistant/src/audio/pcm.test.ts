import { describe, expect, it } from 'vitest'
import { ChannelSequencer, PcmFramer, floatToPcm16 } from './pcm'

describe('PCM 音频处理', () => {
  it('把浮点边界正确转换为 PCM16', () => {
    expect(Array.from(floatToPcm16(new Float32Array([-1, 0, 1])))).toEqual([-32768, 0, 32767])
  })

  it('把 48kHz 音频重采样为 24kHz 并按目标帧输出', () => {
    const frames: Int16Array[] = []
    const framer = new PcmFramer(48_000, 24_000, 4, (frame) => frames.push(frame))
    framer.push(new Float32Array([0, 0.2, 0.4, 0.6, 0.8, 1, 0.8, 0.6]))
    expect(frames).toHaveLength(1)
    expect(frames[0]).toHaveLength(4)
  })

  it('两个角色分别维护递增序号', () => {
    const sequencer = new ChannelSequencer()
    expect(sequencer.next('interviewer')).toBe(0)
    expect(sequencer.next('interviewer')).toBe(1)
    expect(sequencer.next('candidate')).toBe(0)
  })
})
