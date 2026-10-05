<script setup lang="ts">
const props = defineProps<{ disabled: boolean; streaming: boolean }>()
const emit = defineEmits<{ send: [text: string]; stop: [] }>()

const text = ref('')
const rows = computed(() => Math.min(5, Math.max(1, text.value.split('\n').length)))
const canSend = computed(() => !props.disabled && !props.streaming && text.value.trim() !== '')

function submit() {
  if (!canSend.value) return
  emit('send', text.value)
  text.value = ''
}

function onKeydown(event: KeyboardEvent) {
  // Enter sends, Shift+Enter is a newline; Enter confirming an IME composition is left alone.
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  submit()
}
</script>

<template>
  <form class="flex items-end gap-2 border-t border-line p-2" @submit.prevent="submit">
    <label for="assistant-input" class="sr-only">Message the assistant</label>
    <textarea
      id="assistant-input"
      v-model="text"
      :rows="rows"
      :disabled="disabled"
      placeholder="Ask about your portfolio…"
      autocomplete="off"
      class="min-h-8 flex-1 resize-none rounded border border-line bg-bg px-2.5 py-1.5 text-base placeholder:text-faint disabled:cursor-not-allowed disabled:opacity-50 sm:text-[13px]"
      @keydown="onKeydown"
    />
    <button
      v-if="streaming"
      type="button"
      class="h-8 shrink-0 rounded border border-line px-3 text-xs font-semibold hover:bg-raised"
      @click="emit('stop')"
    >
      Stop
    </button>
    <button
      v-else
      type="submit"
      :disabled="!canSend"
      class="h-8 shrink-0 rounded bg-accent px-3 text-xs font-semibold text-bg disabled:cursor-not-allowed disabled:opacity-40"
    >
      Send
    </button>
  </form>
</template>
