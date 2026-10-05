<script setup lang="ts">
import { errorMessage } from '~/lib/api'
import { formatPercent, formatPrice, formatSignedPrice, trend } from '~/lib/format'

const { prices, flashes, histories, hasPrices, connection } = usePrices()
const { tickers, editable, selected, add, remove, select } = useWatchlist()

const SPARK_POINTS = 60 // five minutes at the history's five-second spacing
const TONE = { up: 'text-up', down: 'text-down', flat: 'text-muted' } as const

const rows = computed(() =>
  tickers.value.map((ticker) => {
    const update = prices.value[ticker]
    const flash = flashes[ticker]
    return {
      ticker,
      update,
      spark: (histories.value[ticker] ?? []).slice(-SPARK_POINTS),
      flashClass: flash ? `flash-${flash.direction}-${flash.count % 2 ? 'a' : 'b'}` : '',
      tickTone: TONE[trend(update?.change)],
      dayTone: TONE[trend(update?.day_change)],
    }
  }),
)

const draft = ref('')
const busy = ref(false)
const error = ref('')

async function submit() {
  const symbol = draft.value.trim().toUpperCase()
  if (!symbol || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await add(symbol)
    draft.value = ''
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}

async function removeRow(ticker: string) {
  error.value = ''
  try {
    await remove(ticker)
  } catch (e) {
    error.value = errorMessage(e)
  }
}
</script>

<template>
  <UiPanel title="Watchlist">
    <template #actions>
      <form class="flex items-center gap-1.5" @submit.prevent="submit">
        <input
          v-model="draft"
          type="text"
          placeholder="Add ticker"
          aria-label="Ticker to add"
          maxlength="9"
          autocomplete="off"
          spellcheck="false"
          :disabled="!editable"
          class="h-6 w-24 rounded border border-line bg-bg px-2 text-xs uppercase placeholder:normal-case placeholder:text-faint disabled:opacity-50"
          @input="error = ''"
        >
        <button
          type="submit"
          :disabled="!editable || busy || !draft.trim()"
          class="h-6 rounded border border-line bg-raised px-2 text-xs font-medium hover:border-accent hover:text-accent disabled:opacity-40 disabled:hover:border-line disabled:hover:text-fg"
        >
          Add
        </button>
      </form>
    </template>

    <p v-if="error" class="border-b border-line bg-down/10 px-3 py-1.5 text-xs text-down" role="alert">
      {{ error }}
    </p>
    <p v-else-if="!editable && hasPrices" class="border-b border-line px-3 py-1.5 text-xs text-faint">
      Watchlist service unavailable — showing every streamed ticker; add and remove are off.
    </p>

    <PanelNotice
      v-if="rows.length === 0"
      :title="hasPrices || editable ? 'No tickers yet' : connection === 'reconnecting' ? 'Price stream unavailable' : 'Waiting for prices…'"
      :detail="hasPrices || editable ? 'Add a symbol above to start watching it.' : 'Is the backend running on port 8000?'"
      :tone="!hasPrices && connection === 'reconnecting' ? 'warn' : 'muted'"
    />
    <div v-else class="overflow-auto">
      <table class="data-table">
        <thead>
          <tr>
            <th scope="col">Ticker</th>
            <th scope="col">Price</th>
            <th scope="col">Tick</th>
            <th scope="col">Day</th>
            <th scope="col">Day %</th>
            <th scope="col" class="hidden sm:table-cell">5 min</th>
            <th v-if="editable" scope="col"><span class="sr-only">Remove</span></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.ticker"
            tabindex="0"
            :aria-current="row.ticker === selected ? 'true' : undefined"
            class="cursor-pointer hover:bg-raised"
            :class="[row.flashClass, row.ticker === selected && 'bg-raised shadow-[inset_2px_0_0_var(--accent)]']"
            @click="select(row.ticker)"
            @keydown.enter.self="select(row.ticker)"
            @keydown.space.self.prevent="select(row.ticker)"
          >
            <td class="font-semibold">{{ row.ticker }}</td>
            <td class="font-medium">{{ formatPrice(row.update?.price) }}</td>
            <td :class="row.tickTone">{{ formatSignedPrice(row.update?.change) }}</td>
            <td :class="row.dayTone">{{ formatSignedPrice(row.update?.day_change) }}</td>
            <td :class="row.dayTone">{{ formatPercent(row.update?.day_change_percent) }}</td>
            <td class="hidden py-0! sm:table-cell"><SparkLine :points="row.spark" /></td>
            <td v-if="editable" class="w-8 px-1!">
              <button
                type="button"
                class="size-5 rounded text-faint hover:bg-down/15 hover:text-down"
                :aria-label="`Remove ${row.ticker} from watchlist`"
                @click.stop="removeRow(row.ticker)"
              >
                ×
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </UiPanel>
</template>
