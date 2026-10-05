<script setup lang="ts">
import { isNearBottom } from '~/lib/chat'

const STARTERS = ['How is my portfolio doing?', 'What moved most today?', 'Which of my positions is weakest?']

const { items, state, notice, model, streaming, loadStatus, send, stop, clear, confirmProposal, dismissProposal } =
  useAssistant()

const list = ref<HTMLElement | null>(null)
/** Follow new text only while the reader is at the bottom; scrolling up lets go. */
let following = true

function onScroll() {
  if (list.value) following = isNearBottom(list.value)
}

async function scrollToEnd() {
  await nextTick()
  if (list.value) list.value.scrollTop = list.value.scrollHeight
}

// Re-run on every streamed delta: the signature changes when text, activity or cards change.
watch(
  () =>
    items.value.map((i) => (i.role === 'assistant' ? `${i.content.length}|${i.activity}|${i.proposals.length}|${i.error}` : '')).join(','),
  () => {
    if (following) void scrollToEnd()
  },
  { flush: 'post' },
)

async function ask(text: string) {
  following = true // sending is an explicit "take me to the end"
  const pending = send(text)
  await scrollToEnd()
  await pending
}

onMounted(() => {
  if (state.value !== 'ready') void loadStatus()
  void scrollToEnd()
})
</script>

<template>
  <UiPanel title="Assistant">
    <template #actions>
      <span v-if="state === 'ready' && model" class="num truncate text-[11px] text-faint">{{ model }}</span>
      <button
        v-if="items.length"
        type="button"
        class="rounded border border-line px-2 py-0.5 text-[11px] font-medium text-muted hover:bg-raised hover:text-fg"
        @click="clear"
      >
        Clear
      </button>
    </template>

    <PanelNotice v-if="state === 'loading'" title="Loading assistant…" />
    <PanelNotice
      v-else-if="state === 'error'"
      tone="warn"
      title="Assistant unavailable"
      detail="The assistant service is not responding. Retrying automatically."
    />
    <template v-else>
      <PanelNotice v-if="state === 'unavailable'" tone="warn" title="Assistant not configured" :detail="notice" />

      <div
        v-else
        ref="list"
        class="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3"
        role="log"
        aria-live="polite"
        aria-label="Conversation"
        tabindex="0"
        @scroll.passive="onScroll"
      >
        <div v-if="!items.length" class="my-auto flex flex-col items-start gap-2">
          <p class="text-xs text-muted">Ask about your portfolio, prices or a trade idea. Trades only happen when you confirm them.</p>
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="starter in STARTERS"
              :key="starter"
              type="button"
              class="rounded-full border border-line px-2.5 py-1 text-xs text-muted hover:border-accent hover:text-fg"
              @click="ask(starter)"
            >
              {{ starter }}
            </button>
          </div>
        </div>
        <ChatMessage
          v-for="message in items"
          :key="message.id"
          :message="message"
          @confirm="(id: string) => confirmProposal(message.id, id)"
          @dismiss="(id: string) => dismissProposal(message.id, id)"
        />
      </div>

      <ChatInput :disabled="state !== 'ready'" :streaming="streaming" @send="ask" @stop="stop" />
    </template>
  </UiPanel>
</template>
