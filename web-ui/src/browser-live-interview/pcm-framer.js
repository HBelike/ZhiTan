const TARGET_SAMPLE_RATE = 24_000
const DEFAULT_FRAME_SAMPLES = 2_400

export function floatToPcm16(samples) {
  const pcm = new Int16Array(samples.length)
  for (let index = 0; index < samples.length; index += 1) {
    const value = Math.max(-1, Math.min(1, samples[index]))
    pcm[index] = value < 0 ? Math.round(value * 32_768) : Math.round(value * 32_767)
  }
  return pcm
}

export function resampleToPcm16(samples, inputSampleRate, outputSampleRate = TARGET_SAMPLE_RATE) {
  if (!(samples instanceof Float32Array)) samples = new Float32Array(samples)
  if (!Number.isFinite(inputSampleRate) || inputSampleRate <= 0) {
    throw new TypeError('输入采样率必须大于 0')
  }
  if (!Number.isFinite(outputSampleRate) || outputSampleRate <= 0) {
    throw new TypeError('输出采样率必须大于 0')
  }
  if (!samples.length) return new Int16Array()
  if (inputSampleRate === outputSampleRate) return floatToPcm16(samples)

  const outputLength = Math.max(1, Math.floor(samples.length * outputSampleRate / inputSampleRate))
  const resampled = new Float32Array(outputLength)
  const ratio = inputSampleRate / outputSampleRate

  if (ratio >= 1) {
    for (let outputIndex = 0; outputIndex < outputLength; outputIndex += 1) {
      const start = Math.floor(outputIndex * ratio)
      const end = Math.min(samples.length, Math.max(start + 1, Math.floor((outputIndex + 1) * ratio)))
      let sum = 0
      for (let inputIndex = start; inputIndex < end; inputIndex += 1) sum += samples[inputIndex]
      resampled[outputIndex] = sum / Math.max(1, end - start)
    }
  } else {
    for (let outputIndex = 0; outputIndex < outputLength; outputIndex += 1) {
      const position = outputIndex * ratio
      const left = Math.floor(position)
      const right = Math.min(samples.length - 1, left + 1)
      const weight = position - left
      resampled[outputIndex] = samples[left] * (1 - weight) + samples[right] * weight
    }
  }
  return floatToPcm16(resampled)
}

export function pcm16ToBase64(pcm) {
  const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength)
  let binary = ''
  const chunkSize = 0x8000
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize))
  }
  if (typeof globalThis.btoa === 'function') return globalThis.btoa(binary)
  return globalThis.Buffer.from(bytes).toString('base64')
}

export class PcmFrameAccumulator {
  constructor(frameSamples = DEFAULT_FRAME_SAMPLES) {
    if (!Number.isInteger(frameSamples) || frameSamples <= 0) {
      throw new TypeError('PCM 分帧长度必须是正整数')
    }
    this.frameSamples = frameSamples
    this.pending = new Int16Array()
  }

  append(pcm) {
    if (!(pcm instanceof Int16Array)) pcm = new Int16Array(pcm)
    if (!pcm.length) return []
    const combined = new Int16Array(this.pending.length + pcm.length)
    combined.set(this.pending)
    combined.set(pcm, this.pending.length)
    const frames = []
    let offset = 0
    while (combined.length - offset >= this.frameSamples) {
      frames.push(combined.slice(offset, offset + this.frameSamples))
      offset += this.frameSamples
    }
    this.pending = combined.slice(offset)
    return frames
  }

  clear() {
    this.pending = new Int16Array()
  }
}

export { DEFAULT_FRAME_SAMPLES, TARGET_SAMPLE_RATE }
