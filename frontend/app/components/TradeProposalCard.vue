<script setup lang="ts">
import { formatMoney, formatPrice, formatQuantity } from '~/lib/format'
import type { ProposalItem } from '~/lib/chat'

const props = defineProps<{ item: ProposalItem }>()
const emit = defineEmits<{ confirm: []; dismiss: [] }>()

const p = computed(() => props.item.proposal)
const verb = computed(() => (p.value.side === 'buy' ? 'Buy' : 'Sell'))
const trade = computed(() => props.item.trade)
</script>

<template>
  <article
    class="rounded border bg-bg"
    :class="item.status === 'dismissed' ? 'border-line opacity-60' : p.side === 'buy' ? 'border-up/40' : 'border-down/40'"
    :aria-label="`Proposed trade: ${verb} ${p.ticker}`"
  >
    <header class="flex items-center justify-between gap-2 border-b border-line px-2.5 py-1.5">
      <p class="num text-xs font-semibold">
        <span class="uppercase" :class="p.side === 'buy' ? 'text-up' : 'text-down'">{{ p.side }}</span>
        {{ formatQuantity(p.quantity) }}
        {{ p.ticker }}
      </p>
      <span class="label">Proposed trade</span>
    </header>

    <div class="flex flex-col gap-2 px-2.5 py-2">
      <dl class="num grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-xs">
        <dt class="text-muted">Est. price</dt>
        <dd class="text-right">{{ formatPrice(p.estimated_price) }}</dd>
        <dt class="text-muted">{{ p.side === 'buy' ? 'Est. cost' : 'Est. proceeds' }}</dt>
        <dd class="text-right font-semibold">{{ formatMoney(p.estimated_total) }}</dd>
      </dl>

      <p v-if="p.rationale" class="text-xs text-muted">{{ p.rationale }}</p>

      <div v-if="item.status === 'pending' || item.status === 'confirming'" class="flex gap-2">
        <button
          type="button"
          class="h-7 rounded px-3 text-xs font-semibold text-bg disabled:cursor-not-allowed disabled:opacity-50"
          :class="p.side === 'buy' ? 'bg-up' : 'bg-down'"
          :disabled="item.status === 'confirming'"
          @click="emit('confirm')"
        >
          {{ item.status === 'confirming' ? 'Placing…' : `Confirm ${verb.toLowerCase()}` }}
        </button>
        <button
          type="button"
          class="h-7 rounded border border-line px-3 text-xs font-medium text-muted hover:bg-raised hover:text-fg disabled:opacity-50"
          :disabled="item.status === 'confirming'"
          @click="emit('dismiss')"
        >
          Dismiss
        </button>
      </div>

      <p
        v-else-if="item.status === 'filled' && trade"
        class="num rounded border border-up/40 bg-up/10 px-2 py-1 text-xs text-up"
        role="status"
      >
        Filled: {{ trade.side === 'buy' ? 'bought' : 'sold' }} {{ formatQuantity(trade.quantity) }} {{ trade.ticker }}
        at {{ formatMoney(trade.price) }}, {{ formatMoney(trade.total) }} total
      </p>
      <p
        v-else-if="item.status === 'failed'"
        class="rounded border border-down/40 bg-down/10 px-2 py-1 text-xs text-down"
        role="alert"
      >
        Not placed: {{ item.error }}
      </p>
      <p v-else-if="item.status === 'dismissed'" class="text-xs text-faint">Dismissed</p>
    </div>
  </article>
</template>
