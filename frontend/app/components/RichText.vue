<script setup lang="ts">
/** Assistant text as text nodes only: no v-html, so nothing the model writes becomes markup. */
import { parseRichText, type Inline } from '~/lib/richtext'

const props = defineProps<{ text: string }>()
const blocks = computed(() => parseRichText(props.text))

function key(inline: Inline, index: number): string {
  return `${index}-${inline.kind}`
}
</script>

<template>
  <div class="flex flex-col gap-1.5 break-words">
    <template v-for="(block, b) in blocks" :key="b">
      <p v-if="block.kind === 'p'">
        <template v-for="(part, i) in block.inline" :key="key(part, i)">
          <br v-if="part.kind === 'br'">
          <strong v-else-if="part.kind === 'bold'" class="font-semibold">{{ part.text }}</strong>
          <code v-else-if="part.kind === 'code'" class="num rounded-sm bg-raised px-1 py-px font-mono text-[12px]">{{ part.text }}</code>
          <template v-else>{{ part.text }}</template>
        </template>
      </p>
      <component :is="block.kind" v-else class="flex flex-col gap-0.5 pl-4" :class="block.kind === 'ul' ? 'list-disc' : 'list-decimal'">
        <li v-for="(item, n) in block.items" :key="n">
          <template v-for="(part, i) in item" :key="key(part, i)">
            <strong v-if="part.kind === 'bold'" class="font-semibold">{{ part.text }}</strong>
            <code v-else-if="part.kind === 'code'" class="num rounded-sm bg-raised px-1 py-px font-mono text-[12px]">{{ part.text }}</code>
            <template v-else-if="part.kind === 'text'">{{ part.text }}</template>
          </template>
        </li>
      </component>
    </template>
  </div>
</template>
