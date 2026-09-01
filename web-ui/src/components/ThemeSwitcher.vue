<script setup>
defineProps({
  modelValue: {
    type: String,
    default: 'blue'
  },
  compact: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:modelValue'])

const themes = [
  { value: 'blue', label: '蓝白', color: '#0064dd' },
  { value: 'green', label: '浅绿', color: '#89a93e' }
]
</script>

<template>
  <div class="theme-switcher" :class="{ compact }" role="radiogroup" aria-label="界面主题">
    <button
      v-for="item in themes"
      :key="item.value"
      type="button"
      class="theme-option"
      :class="{ active: modelValue === item.value }"
      role="radio"
      :aria-checked="modelValue === item.value"
      :title="`切换为${item.label}主题`"
      @click="emit('update:modelValue', item.value)"
    >
      <i :style="{ backgroundColor: item.color }" aria-hidden="true"></i>
      <span>{{ item.label }}</span>
    </button>
  </div>
</template>
