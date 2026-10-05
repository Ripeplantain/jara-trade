<script setup lang="ts">
import type { ChatItem } from '~/lib/chat'

defineProps<{ message: ChatItem }>()
const emit = defineEmits<{ confirm: [proposalId: string]; dismiss: [proposalId: string] }>()
</script>

<template>
  <div v-if="message.role === 'user'" class="flex justify-end">
    <p class="max-w-[85%] whitespace-pre-wrap break-words rounded-md bg-raised px-2.5 py-1.5">{{ message.content }}</p>
  </div>

  <div v-else class="flex max-w-full flex-col gap-2">
    <RichText v-if="message.content" :text="message.content" />
    <p v-else-if="message.streaming && !message.activity && !message.proposals.length" class="text-xs text-faint">Thinking…</p>

    <p v-if="message.activity" class="text-xs italic text-muted" role="status">{{ message.activity }}</p>

    <TradeProposalCard
      v-for="card in message.proposals"
      :key="card.id"
      :item="card"
      @confirm="emit('confirm', card.id)"
      @dismiss="emit('dismiss', card.id)"
    />

    <p v-if="message.stopped" class="text-xs text-faint">Stopped</p>
    <p v-if="message.error" class="rounded border border-down/40 bg-down/10 px-2.5 py-1.5 text-xs text-down" role="alert">
      {{ message.error }}
    </p>
  </div>
</template>
