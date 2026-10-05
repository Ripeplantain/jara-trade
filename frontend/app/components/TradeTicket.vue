<script setup lang="ts">
import { errorMessage } from '~/lib/api'
import { formatMoney, formatPrice, formatQuantity } from '~/lib/format'
import { estimateTotal, maxAffordable } from '~/lib/pnl'
import type { TradeSide } from '~/types/api'

const props = defineProps<{ ticker: string | null }>()

const { prices } = usePrices()
const { live, portfolioState, placeTrade } = usePortfolio()

const side = ref<TradeSide>('buy')
const quantityText = ref('')
const busy = ref(false)
const error = ref('')
const confirmation = ref('')

const price = computed(() => (props.ticker ? prices.value[props.ticker]?.price : undefined))
const cash = computed(() => live.value?.cash ?? 0)
const held = computed(() => live.value?.positions.find((p) => p.ticker === props.ticker)?.quantity ?? 0)

const quantity = computed(() => {
  const n = Number(quantityText.value)
  return quantityText.value !== '' && Number.isFinite(n) && n > 0 ? n : null
})
const estimate = computed(() => (quantity.value ? estimateTotal(quantity.value, price.value) : null))

/** A heads-up only; the backend makes the final call at its own price. */
const warning = computed(() => {
  if (!quantity.value || !live.value) return ''
  if (side.value === 'buy' && estimate.value !== null && estimate.value > cash.value) {
    return 'Estimated cost is more than your cash.'
  }
  if (side.value === 'sell' && quantity.value > held.value) {
    return held.value > 0 ? `You hold ${formatQuantity(held.value)} shares.` : `You hold no ${props.ticker}.`
  }
  return ''
})

const unavailable = computed(() => portfolioState.value === 'unavailable')
const canSubmit = computed(() => !!props.ticker && !!quantity.value && !busy.value && !unavailable.value)

function clearMessages() {
  error.value = ''
  confirmation.value = ''
}

function setSide(next: TradeSide) {
  side.value = next
  clearMessages()
}

function fillMax() {
  const max = side.value === 'buy' ? maxAffordable(cash.value, price.value) : held.value
  quantityText.value = max > 0 ? String(max) : ''
  clearMessages()
}

watch(() => props.ticker, () => {
  quantityText.value = ''
  clearMessages()
})

async function submit() {
  if (!canSubmit.value || !props.ticker || !quantity.value) return
  busy.value = true
  clearMessages()
  try {
    const trade = await placeTrade({ ticker: props.ticker, side: side.value, quantity: quantity.value })
    confirmation.value = `${trade.side === 'buy' ? 'Bought' : 'Sold'} ${formatQuantity(trade.quantity)} ${trade.ticker} at ${formatMoney(trade.price)} — ${formatMoney(trade.total)} total`
    quantityText.value = ''
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <UiPanel title="Trade">
    <template #actions>
      <span v-if="ticker" class="num text-xs text-muted">
        <span class="font-semibold text-fg">{{ ticker }}</span> {{ formatPrice(price) }}
      </span>
    </template>

    <PanelNotice
      v-if="unavailable"
      tone="warn"
      title="Trading unavailable"
      detail="The portfolio service is not responding. Retrying automatically."
    />
    <form v-else class="flex flex-col gap-3 p-3" @submit.prevent="submit">
      <div class="grid grid-cols-2 gap-1 rounded border border-line bg-bg p-0.5" role="group" aria-label="Order side">
        <button
          type="button"
          class="rounded-sm py-1.5 text-xs font-semibold"
          :class="side === 'buy' ? 'bg-up text-bg' : 'text-muted hover:text-fg'"
          :aria-pressed="side === 'buy'"
          @click="setSide('buy')"
        >
          Buy
        </button>
        <button
          type="button"
          class="rounded-sm py-1.5 text-xs font-semibold"
          :class="side === 'sell' ? 'bg-down text-bg' : 'text-muted hover:text-fg'"
          :aria-pressed="side === 'sell'"
          @click="setSide('sell')"
        >
          Sell
        </button>
      </div>

      <div>
        <div class="mb-1 flex items-center justify-between">
          <label for="trade-quantity" class="label">Quantity</label>
          <button
            type="button"
            class="text-[11px] font-medium text-accent hover:underline disabled:text-faint disabled:no-underline"
            :disabled="!ticker || (side === 'buy' ? maxAffordable(cash, price) <= 0 : held <= 0)"
            @click="fillMax"
          >
            {{ side === 'buy' ? 'Max affordable' : 'Sell all' }}
          </button>
        </div>
        <input
          id="trade-quantity"
          v-model="quantityText"
          type="number"
          inputmode="decimal"
          min="0"
          step="any"
          placeholder="0"
          autocomplete="off"
          class="num h-9 w-full rounded border border-line bg-bg px-2.5 text-right text-base font-medium placeholder:text-faint"
          @input="clearMessages"
        >
      </div>

      <dl class="num grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
        <dt class="text-muted">{{ side === 'buy' ? 'Estimated cost' : 'Estimated proceeds' }}</dt>
        <dd class="text-right text-sm font-semibold">{{ formatMoney(estimate) }}</dd>
        <dt class="text-muted">Cash available</dt>
        <dd class="text-right">{{ live ? formatMoney(cash) : '—' }}</dd>
        <dt class="text-muted">Shares held</dt>
        <dd class="text-right">{{ live ? formatQuantity(held) : '—' }}</dd>
      </dl>

      <p v-if="warning" class="text-xs text-warn">{{ warning }}</p>

      <button
        type="submit"
        :disabled="!canSubmit"
        class="h-9 rounded text-sm font-semibold text-bg disabled:cursor-not-allowed disabled:opacity-40"
        :class="side === 'buy' ? 'bg-up' : 'bg-down'"
      >
        {{ busy ? 'Submitting…' : ticker ? `${side === 'buy' ? 'Buy' : 'Sell'} ${ticker}` : 'Select a ticker' }}
      </button>

      <p v-if="error" class="rounded border border-down/40 bg-down/10 px-2.5 py-1.5 text-xs text-down" role="alert">
        {{ error }}
      </p>
      <p v-else-if="confirmation" class="num rounded border border-up/40 bg-up/10 px-2.5 py-1.5 text-xs text-up" role="status">
        {{ confirmation }}
      </p>
    </form>
  </UiPanel>
</template>
