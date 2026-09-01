const DEFAULT_MEDIA_SETTINGS = Object.freeze({
  image_generation_enabled: true,
  video_generation_enabled: false,
  audio_generation_enabled: false
})

export function mediaSettingsFromConfig(config = {}) {
  return {
    image_generation_enabled: typeof config.image_generation_enabled === 'boolean'
      ? config.image_generation_enabled
      : DEFAULT_MEDIA_SETTINGS.image_generation_enabled,
    video_generation_enabled: typeof config.video_generation_enabled === 'boolean'
      ? config.video_generation_enabled
      : DEFAULT_MEDIA_SETTINGS.video_generation_enabled,
    audio_generation_enabled: typeof config.audio_generation_enabled === 'boolean'
      ? config.audio_generation_enabled
      : DEFAULT_MEDIA_SETTINGS.audio_generation_enabled
  }
}

export function mediaSettingsForPayload(form) {
  return {
    image_generation_enabled: Boolean(form.image_generation_enabled),
    video_generation_enabled: Boolean(form.video_generation_enabled),
    audio_generation_enabled: Boolean(form.audio_generation_enabled)
  }
}
