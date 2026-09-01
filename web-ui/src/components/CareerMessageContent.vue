<script setup>
import { computed } from 'vue'

const props = defineProps({
  content: { type: String, default: '' }
})

function cleanInline(value = '') {
  return value
    .replace(/<!--([\s\S]*?)-->/g, '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/~~([^~]+)~~/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/(^|\s)[*_]([^*_]+)[*_](?=\s|$)/g, '$1$2')
    .trim()
}

function tableCells(line) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cleanInline(cell))
}

function isTableDivider(line = '') {
  return /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(line.trim())
}

function parseMessage(markdown = '') {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n')
  const blocks = []
  let index = 0
  let paragraph = []

  const flushParagraph = () => {
    const text = cleanInline(paragraph.join(' '))
    if (text) blocks.push({ type: 'paragraph', text })
    paragraph = []
  }

  while (index < lines.length) {
    const rawLine = lines[index]
    const line = rawLine.trim()

    if (!line) {
      flushParagraph()
      index += 1
      continue
    }

    const fence = line.match(/^```\s*([^\s]*)/)
    if (fence) {
      flushParagraph()
      const code = []
      index += 1
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        code.push(lines[index])
        index += 1
      }
      if (index < lines.length) index += 1
      blocks.push({ type: 'code', language: fence[1], text: code.join('\n') })
      continue
    }

    if (line.includes('|') && isTableDivider(lines[index + 1])) {
      flushParagraph()
      const headers = tableCells(line)
      const rows = []
      index += 2
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        rows.push(tableCells(lines[index]))
        index += 1
      }
      blocks.push({ type: 'table', headers, rows })
      continue
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      flushParagraph()
      blocks.push({ type: 'heading', level: heading[1].length, text: cleanInline(heading[2]) })
      index += 1
      continue
    }

    if (/^(---|\*\*\*|___)$/.test(line)) {
      flushParagraph()
      blocks.push({ type: 'divider' })
      index += 1
      continue
    }

    const listItem = line.match(/^([-*+]|\d+[.)])\s+(.+)$/)
    if (listItem) {
      flushParagraph()
      const ordered = /^\d/.test(listItem[1])
      const items = []
      while (index < lines.length) {
        const nextItem = lines[index].trim().match(/^([-*+]|\d+[.)])\s+(.+)$/)
        if (!nextItem || /^\d/.test(nextItem[1]) !== ordered) break
        items.push(cleanInline(nextItem[2]))
        index += 1
      }
      blocks.push({ type: 'list', ordered, items })
      continue
    }

    if (line.startsWith('>')) {
      flushParagraph()
      const quote = []
      while (index < lines.length && lines[index].trim().startsWith('>')) {
        quote.push(lines[index].trim().replace(/^>\s?/, ''))
        index += 1
      }
      blocks.push({ type: 'quote', text: cleanInline(quote.join(' ')) })
      continue
    }

    paragraph.push(line)
    index += 1
  }

  flushParagraph()
  return blocks.length ? blocks : [{ type: 'paragraph', text: '暂无回复内容。' }]
}

const blocks = computed(() => parseMessage(props.content))
</script>

<template>
  <div class="message-content">
    <template v-for="(block, blockIndex) in blocks" :key="`${block.type}-${blockIndex}`">
      <hr v-if="block.type === 'divider'" />
      <component
        :is="`h${Math.min(block.level + 2, 5)}`"
        v-else-if="block.type === 'heading'"
      >{{ block.text }}</component>
      <ol v-else-if="block.type === 'list' && block.ordered">
        <li v-for="(item, itemIndex) in block.items" :key="itemIndex">{{ item }}</li>
      </ol>
      <ul v-else-if="block.type === 'list'">
        <li v-for="(item, itemIndex) in block.items" :key="itemIndex">{{ item }}</li>
      </ul>
      <blockquote v-else-if="block.type === 'quote'">{{ block.text }}</blockquote>
      <div v-else-if="block.type === 'table'" class="message-table-wrap">
        <table>
          <thead><tr><th v-for="(cell, cellIndex) in block.headers" :key="cellIndex">{{ cell }}</th></tr></thead>
          <tbody><tr v-for="(row, rowIndex) in block.rows" :key="rowIndex"><td v-for="(cell, cellIndex) in row" :key="cellIndex">{{ cell }}</td></tr></tbody>
        </table>
      </div>
      <pre v-else-if="block.type === 'code'"><code>{{ block.text }}</code></pre>
      <p v-else>{{ block.text }}</p>
    </template>
  </div>
</template>

<style scoped>
.message-content{display:grid;gap:10px;min-width:0;color:var(--ui-text);font-size:14px;line-height:1.72}.message-content :is(p,h3,h4,h5,ul,ol,blockquote,pre){margin:0}.message-content :is(h3,h4,h5){color:var(--ui-text);font-family:var(--ui-font-display);font-weight:760;line-height:1.42}.message-content h3{font-size:16px}.message-content h4{font-size:15px}.message-content h5{font-size:14px}.message-content :is(ul,ol){display:grid;gap:6px;padding-left:20px}.message-content li::marker{color:var(--ui-accent)}.message-content hr{width:100%;margin:2px 0;border:0;border-top:1px solid var(--ui-line)}.message-content blockquote{border-left:3px solid var(--ui-accent-mid);background:var(--ui-surface-soft);color:var(--ui-text-secondary);padding:9px 11px}.message-content pre{max-width:100%;overflow:auto;border:1px solid var(--ui-line);border-radius:8px;background:#17223b;color:#eaf3ff;padding:11px;font:12px/1.6 var(--ui-font-utility);white-space:pre}.message-table-wrap{max-width:100%;overflow-x:auto;border:1px solid var(--ui-line);border-radius:9px}.message-content table{width:100%;border-collapse:collapse;background:var(--ui-surface);font-size:12px;line-height:1.5}.message-content :is(th,td){min-width:92px;border-bottom:1px solid var(--ui-line);padding:8px 9px;text-align:left;vertical-align:top}.message-content th{background:var(--ui-surface-soft);color:var(--ui-text-secondary);font-weight:800}.message-content tbody tr:last-child td{border-bottom:0}
</style>
