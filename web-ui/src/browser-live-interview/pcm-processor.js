class InterviewPcmProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0]?.[0]
    if (channel?.length) {
      const samples = new Float32Array(channel)
      this.port.postMessage({ samples: samples.buffer }, [samples.buffer])
    }
    return true
  }
}

registerProcessor('interview-pcm-processor', InterviewPcmProcessor)
