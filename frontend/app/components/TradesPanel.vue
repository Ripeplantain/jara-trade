<script setup lang="ts">
import { errorMessage } from '~/lib/api'
import { formatDateTime, formatMoney, formatPrice, formatQuantity, formatSignedMoney, trend } from '~/lib/format'

const { trades, tradesState, portfolioState, live, reset } = usePortfolio()
const { refresh: refreshWatchlist } = useWatchlist()

const TONE = { up: 'text-up', down: 'text-down', flat: 'text-muted' } as const

const confirming = ref(false)
const busy = ref(false)
const error = ref('')
const done = ref(false)

async function confirmReset() {
  busy.value = true
  error.value = ''
  try {
    await reset()
    void refreshWatchlist()
    confirming.value = false
    done.value = true
    setTimeout(() => (done.value = false), 4000)
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <UiPanel title="Recent trades">
    <template #actions>
      <span v-if="done" class="text-xs text-up" role="status">Portfolio reset</span>
      <button
        v-if="portfolioState === 'ready' && !confirming"
        type="button"
        class="rounded border border-line px-2 py-0.5 text-[11px] font-medium text-muted hover:border-down hover:text-down"
        @click="confirming = true; error = ''; done = false"
      >
        Reset portfolio
      </button>
    </template>

    <div v-if="confirming" class="border-b border-line bg-down/10 px-3 py-2.5" role="alertdialog" aria-label="Confirm reset">
      <p class="text-xs">
        Reset the portfolio? All positions and trade history are deleted and cash goes back to
        <span class="num font-semibold">{{ formatMoney(live?.starting_cash) }}</span>. This cannot be undone.
      </p>
      <p v-if="error" class="mt-1.5 text-xs text-down" role="alert">{{ error }}</p>
      <div class="mt-2 flex gap-2">
        <button
          type="button"
          class="rounded bg-down px-2.5 py-1 text-xs font-semibold text-bg disabled:opacity-50"
          :disabled="busy"
          @click="confirmReset"
        >
          {{ busy ? 'Resetting…' : 'Yes, reset' }}
        </button>
        <button
          type="button"
          class="rounded border border-line px-2.5 py-1 text-xs font-medium hover:bg-raised"
          :disabled="busy"
          @click="confirming = false"
        >
          Cancel
        </button>
      </div>
    </div>

    <PanelNotice
      v-if="tradesState === 'unavailable'"
      tone="warn"
      title="Trade history unavailable"
      detail="The trades service is not responding. Retrying automatically."
    />
    <PanelNotice v-else-if="tradesState === 'loading'" title="Loading trades…" />
    <PanelNotice v-else-if="trades.length === 0" title="No trades yet" detail="Your fills will be listed here, newest first." />
    <div v-else class="max-h-80 overflow-auto">
      <table class="data-table">
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Side</th>
            <th scope="col">Ticker</th>
            <th scope="col">Qty</th>
            <th scope="col">Price</th>
            <th scope="col">Total</th>
            <th scope="col">Realised</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="trade in trades" :key="trade.id">
            <td class="text-muted">{{ formatDateTime(trade.timestamp) }}</td>
            <td class="font-semibold uppercase" :class="trade.side === 'buy' ? 'text-up' : 'text-down'">{{ trade.side }}</td>
            <td class="font-semibold">{{ trade.ticker }}</td>
            <td>{{ formatQuantity(trade.quantity) }}</td>
            <td>{{ formatPrice(trade.price) }}</td>
            <td>{{ formatMoney(trade.total) }}</td>
            <td :class="TONE[trend(trade.realized_pnl)]">
              {{ trade.realized_pnl === null ? '—' : formatSignedMoney(trade.realized_pnl) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </UiPanel>
</template>
