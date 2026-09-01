export type AudioRole = 'interviewer' | 'candidate'

export function floatToPcm16(input: Float32Array): Int16Array {
  const output = new Int16Array(input.length)
  for (let index = 0; index < input.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, input[index] ?? 0))
    output[index] = sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767)
  }
  return output
}

function resample(input: Float32Array, sourceRate: number, targetRate: number): Float32Array {
  if (sourceRate === targetRate) return input
  const length = Math.max(1, Math.floor(input.length * targetRate / sourceRate))
  const output = new Float32Array(length)
  const ratio = sourceRate / targetRate
  for (let index = 0; index < length; index += 1) {
    const sourcePosition = index * ratio
    const left = Math.floor(sourcePosition)
    const right = Math.min(input.length - 1, left + 1)
    const fraction = sourcePosition - left
    output[index] = (input[left] ?? 0) * (1 - fraction) + (input[right] ?? 0) * fraction
  }
  return output
}

export class PcmFramer {
  private pending = new Float32Array(0)

  constructor(
    private readonly sourceRate: number,
    private readonly targetRate: number,
    private readonly frameSamples: number,
    private readonly onFrame: (frame: Int16Array) => void,
  ) {}

  push(input: Float32Array): void {
    const converted = resample(input, this.sourceRate, this.targetRate)
    const combined = new Float32Array(this.pending.length + converted.length)
    combined.set(this.pending)
    combined.set(converted, this.pending.length)
    let offset = 0
    while (combined.length - offset >= this.frameSamples) {
      this.onFrame(floatToPcm16(combined.slice(offset, offset + this.frameSamples)))
      offset += this.frameSamples
    }
    this.pending = combined.slice(offset)
  }
}

export class ChannelSequencer {
  private readonly values: Record<AudioRole, number> = { interviewer: 0, candidate: 0 }

  next(role: AudioRole): number {
    const current = this.values[role]
    this.values[role] += 1
    return current
  }
}

export function rms(input: Float32Array): number {
  if (!input.length) return 0
  let sum = 0
  for (const sample of input) sum += sample * sample
  return Math.min(1, Math.sqrt(sum / input.length) * 3)
}
