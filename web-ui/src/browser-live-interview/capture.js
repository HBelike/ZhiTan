import { PcmFrameAccumulator, resampleToPcm16, TARGET_SAMPLE_RATE } from './pcm-framer.js'

const PROCESSOR_NAME = 'interview-pcm-processor'

export function detectBrowserInterviewSupport({
  userAgent = navigator.userAgent,
  mediaDevices = navigator.mediaDevices,
  AudioContextImpl = window.AudioContext || window.webkitAudioContext,
  WebSocketImpl = window.WebSocket
} = {}) {
  const isMobileBrowser = /(Android|Mobile|CriOS|FxiOS|EdgiOS)/i.test(userAgent)
  const isDesktopChromium = /Chrome\/\d+/i.test(userAgent) && !/OPR\//i.test(userAgent)
  const isDesktopFirefox = /Firefox\/\d+/i.test(userAgent)
  const isSupportedDesktopBrowser = !isMobileBrowser && (isDesktopChromium || isDesktopFirefox)
  const missing = []
  if (!isSupportedDesktopBrowser) missing.push('请使用桌面版 Chrome、Edge 或 Firefox')
  if (typeof mediaDevices?.getDisplayMedia !== 'function') missing.push('浏览器不支持电脑音频共享')
  if (typeof mediaDevices?.getUserMedia !== 'function') missing.push('浏览器不支持麦克风授权')
  // Chrome 的 audioWorklet 是实例 getter；从 prototype 读取会抛出 Illegal invocation。
  // 此处只判断 AudioContext 构造器，实际 AudioWorklet 能力在用户点击开始后由实例验证。
  if (typeof AudioContextImpl !== 'function') missing.push('浏览器不支持 Web Audio')
  if (typeof WebSocketImpl !== 'function') missing.push('浏览器不支持实时连接')
  if (globalThis.isSecureContext === false && globalThis.location?.hostname !== 'localhost') {
    missing.push('页面必须通过 HTTPS 打开')
  }
  return { supported: missing.length === 0, missing }
}

export class BrowserAudioCapture {
  constructor({
    mediaDevices = navigator.mediaDevices,
    AudioContextImpl = window.AudioContext || window.webkitAudioContext,
    processorUrl = new URL('./pcm-processor.js', import.meta.url)
  } = {}) {
    this.mediaDevices = mediaDevices
    this.AudioContextImpl = AudioContextImpl
    this.processorUrl = processorUrl
    this.context = null
    this.resources = []
    this.streams = []
    this.sequences = { interviewer: 0, candidate: 0 }
    this.framers = {
      interviewer: new PcmFrameAccumulator(),
      candidate: new PcmFrameAccumulator()
    }
    this.stoppedTracks = new WeakSet()
    this.onFrame = null
    this.onEnded = null
    this.stopping = false
  }

  async start({ candidateEnabled = false, onFrame, onEnded }) {
    // getDisplayMedia 必须直接发生在点击事件的瞬时用户授权中，不能先等待异步清理。
    const displayRequest = this.mediaDevices.getDisplayMedia({
      video: { displaySurface: 'monitor' },
      audio: true,
      preferCurrentTab: false,
      selfBrowserSurface: 'exclude',
      surfaceSwitching: 'include',
      monitorTypeSurfaces: 'include',
      systemAudio: 'include'
    })
    await this.stop()
    this.stopping = false
    this.onFrame = onFrame
    this.onEnded = onEnded
    this.sequences = { interviewer: 0, candidate: 0 }
    this.framers.interviewer.clear()
    this.framers.candidate.clear()

    const displayStream = await displayRequest
    this.streams.push(displayStream)
    for (const videoTrack of displayStream.getVideoTracks()) this._stopTrack(videoTrack)
    const interviewerTrack = displayStream.getAudioTracks()[0]
    if (!interviewerTrack) {
      this._stopStream(displayStream)
      throw new Error('没有检测到共享电脑声音。请在浏览器授权窗口选择带声音的共享来源，并在可用时开启音频共享；如果浏览器没有提供音频选项，请改用 Chrome 或 Edge。')
    }
    await this._connectTrack(interviewerTrack, 'interviewer')

    let activeCandidate = false
    let warning = ''
    if (candidateEnabled) {
      try {
        const microphoneStream = await this.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
          video: false
        })
        this.streams.push(microphoneStream)
        const candidateTrack = microphoneStream.getAudioTracks()[0]
        if (!candidateTrack) throw new Error('麦克风没有返回音频轨道')
        await this._connectTrack(candidateTrack, 'candidate')
        activeCandidate = true
      } catch {
        warning = '麦克风不可用，已降级为仅理解面试官问题'
      }
    }
    return { candidateEnabled: activeCandidate, warning }
  }

  async _ensureContext() {
    if (this.context) return this.context
    this.context = new this.AudioContextImpl({ latencyHint: 'interactive' })
    await this.context.audioWorklet.addModule(this.processorUrl)
    if (this.context.state === 'suspended') await this.context.resume()
    return this.context
  }

  async _connectTrack(track, channel) {
    const context = await this._ensureContext()
    const stream = new MediaStream([track])
    const source = context.createMediaStreamSource(stream)
    const node = new AudioWorkletNode(context, PROCESSOR_NAME)
    const gain = context.createGain()
    gain.gain.value = 0
    source.connect(node)
    node.connect(gain)
    gain.connect(context.destination)
    node.port.onmessage = ({ data }) => {
      if (this.stopping || !data?.samples) return
      const pcm = resampleToPcm16(new Float32Array(data.samples), context.sampleRate, TARGET_SAMPLE_RATE)
      if (!pcm.length) return
      for (const frame of this.framers[channel].append(pcm)) {
        const sequence = this.sequences[channel]
        this.sequences[channel] += 1
        this.onFrame?.({ channel, sequence, pcm: frame })
      }
    }
    const ended = () => {
      if (!this.stopping) this.onEnded?.({ channel, reason: 'track_ended' })
    }
    track.addEventListener('ended', ended, { once: true })
    this.resources.push({ track, ended, source, node, gain })
  }

  async stop() {
    this.stopping = true
    for (const resource of this.resources.splice(0)) {
      resource.track.removeEventListener?.('ended', resource.ended)
      resource.source.disconnect?.()
      resource.node.disconnect?.()
      resource.gain.disconnect?.()
      resource.node.port.onmessage = null
    }
    for (const stream of this.streams.splice(0)) this._stopStream(stream)
    const context = this.context
    this.context = null
    if (context && context.state !== 'closed') await context.close()
    this.onFrame = null
    this.onEnded = null
  }

  _stopStream(stream) {
    for (const track of stream.getTracks()) this._stopTrack(track)
  }

  _stopTrack(track) {
    if (this.stoppedTracks.has(track)) return
    this.stoppedTracks.add(track)
    if (track.readyState !== 'ended') track.stop()
  }
}
