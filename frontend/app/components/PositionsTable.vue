<script setup lang="ts">
import { formatMoney, formatPercent, formatPrice, formatQuantity, formatSignedMoney, trend } from '~/lib/format'

const { live, portfolioState } = usePortfolio()
const { selected, select } = useWatchlist()

const TONE = { up: 'text-up', down: 'text-down', flat: 'text-muted' } as const
const rows = computed(() =>
  (live.value?.positions ?? []).map((position) => ({
    position,
    tone: TONE[trend(position.unrealized_pnl)],
  })),
)
const totalTone = computed(() => TONE[trend(live.value?.unrealized_pnl)])
</script>

<template>
  <UiPanel title="Positions">
    <template #actions>
      <span v-if="live && rows.length" class="num text-xs text-muted">
        <span class="mr-1">Value</span><span class="font-medium text-fg">{{ formatMoney(live.positions_value) }}</span>
        <span class="ml-2 mr-1">Unrealised</span>
        <span class="font-medium" :class="totalTone">{{ formatSignedMoney(live.unrealized_pnl) }}</span>
      </span>
    </template>

    <PanelNotice
      v-if="portfolioState === 'unavailable'"
      tone="warn"
      title="Positions unavailable"
      detail="The portfolio service is not responding. Retrying automatically."
    />
    <PanelNotice v-else-if="portfolioState === 'loading'" title="Loading positions…" />
    <PanelNotice
      v-else-if="rows.length === 0"
      title="No open positions"
      detail="Buy something from the trade ticket and it will show up here."
    />
    <div v-else class="overflow-auto">
      <table class="data-table">
        <thead>
          <tr>
            <th scope="col">Ticker</th>
            <th scope="col">Qty</th>
            <th scope="col">Avg cost</th>
            <th scope="col">Price</th>
            <th scope="col">Value</th>
            <th scope="col">Unrl. P&amp;L</th>
            <th scope="col">%</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="{ position, tone } in rows"
            :key="position.ticker"
            tabindex="0"
            class="cursor-pointer hover:bg-raised"
            :class="position.ticker === selected && 'bg-raised shadow-[inset_2px_0_0_var(--accent)]'"
            @click="select(position.ticker)"
            @keydown.enter="select(position.ticker)"
          >
            <td class="font-semibold">{{ position.ticker }}</td>
            <td>{{ formatQuantity(position.quantity) }}</td>
            <td>{{ formatPrice(position.avg_cost) }}</td>
            <td class="font-medium">{{ formatPrice(position.price) }}</td>
            <td>{{ formatMoney(position.market_value) }}</td>
            <td :class="tone">{{ formatSignedMoney(position.unrealized_pnl) }}</td>
            <td :class="tone">{{ formatPercent(position.unrealized_pnl_percent) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </UiPanel>
</template>
