import { PcmFramer, rms, type AudioRole } from './pcm'
import { bridge } from '../bridge'

export interface CaptureOptions {
  microphoneDeviceId?: string
  onFrame: (role: AudioRole, frame: Int16Array) => void
  onLevel: (role: AudioRole, level: number) => void
}

export interface CaptureController {
  stop(): Promise<void>
}

interface Pipeline {
  context: AudioContext
  stream: MediaStream
}

async function createPipeline(
  stream: MediaStream,
  role: AudioRole,
  options: CaptureOptions,
): Promise<Pipeline> {
  const context = new AudioContext()
  // 使用相对地址同时兼容 Vite 开发服务和 Electron 的 file:// 生产页面。
  await context.audioWorklet.addModule(new URL('./pcm-worklet.js', window.location.href).toString())
  const source = context.createMediaStreamSource(stream)
  const processor = new AudioWorkletNode(context, 'pcm-capture-processor')
  const silentGain = context.createGain()
  silentGain.gain.value = 0
  const framer = new PcmFramer(context.sampleRate, 24_000, 2_400, (frame) => options.onFrame(role, frame))
  processor.port.onmessage = (event: MessageEvent<Float32Array>) => {
    framer.push(event.data)
    options.onLevel(role, rms(event.data))
  }
  source.connect(processor).connect(silentGain).connect(context.destination)
  await context.resume()
  return { context, stream }
}

export async function listMicrophones(): Promise<MediaDeviceInfo[]> {
  const temporary = await navigator.mediaDevices.getUserMedia({ audio: true })
  temporary.getTracks().forEach((track) => track.stop())
  return (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === 'audioinput')
}

export async function startCapture(options: CaptureOptions): Promise<CaptureController> {
  if (bridge.platform !== 'win32') throw new Error('实时面试助手仅支持 Windows 10/11')
  const display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true })
  const systemTracks = display.getAudioTracks()
  display.getVideoTracks().forEach((track) => track.stop())
  if (!systemTracks.length) {
    display.getTracks().forEach((track) => track.stop())
    throw new Error('未获取到 Windows 系统音频，请确认已允许屏幕与系统声音采集')
  }
  const systemStream = new MediaStream(systemTracks)
  const microphone = await navigator.mediaDevices.getUserMedia({
    audio: options.microphoneDeviceId
      ? { deviceId: { exact: options.microphoneDeviceId }, echoCancellation: true, noiseSuppression: true }
      : { echoCancellation: true, noiseSuppression: true },
  })
  const pipelines: Pipeline[] = []
  try {
    pipelines.push(await createPipeline(systemStream, 'interviewer', options))
    pipelines.push(await createPipeline(microphone, 'candidate', options))
  } catch (error) {
    systemStream.getTracks().forEach((track) => track.stop())
    microphone.getTracks().forEach((track) => track.stop())
    await Promise.all(pipelines.map((item) => item.context.close()))
    throw error
  }
  let stopped = false
  return {
    async stop() {
      if (stopped) return
      stopped = true
      for (const item of pipelines) item.stream.getTracks().forEach((track) => track.stop())
      await Promise.all(pipelines.map((item) => item.context.close()))
    },
  }
}
