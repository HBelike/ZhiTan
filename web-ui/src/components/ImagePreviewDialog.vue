<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

defineProps({
  source: { type: String, required: true },
  alt: { type: String, default: '图片预览' }
})

const emit = defineEmits(['close'])
const closeButton = ref(null)
let previousFocus = null

function handleKeydown(event) {
  if (event.key !== 'Escape') return
  event.preventDefault()
  emit('close')
}

onMounted(() => {
  previousFocus = document.activeElement
  window.addEventListener('keydown', handleKeydown)
  void nextTick(() => closeButton.value?.focus())
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown)
  if (previousFocus?.isConnected) previousFocus.focus()
})
</script>

<template>
  <Teleport to="body">
    <section
      class="image-preview-dialog"
      role="dialog"
      aria-modal="true"
      aria-label="图片预览"
      @click.self="emit('close')"
    >
      <header class="image-preview-toolbar">
        <div>
          <span>图片预览</span>
          <strong>{{ alt }}</strong>
        </div>
        <nav aria-label="图片操作">
          <a class="image-preview-download" :href="source" download>下载原图</a>
          <button ref="closeButton" type="button" class="image-preview-close" @click="emit('close')">
            <span aria-hidden="true">←</span>
            关闭预览
          </button>
        </nav>
      </header>

      <div class="image-preview-stage" @click.self="emit('close')">
        <img :src="source" :alt="alt" />
      </div>
    </section>
  </Teleport>
</template>
