<script setup lang="ts">
const { prices, start: startPrices, close: closePrices } = usePrices()
const { start: startStatus } = useMarketStatus()
const { refresh: refreshPortfolio } = usePortfolio()
const { tickers, selected, refresh: refreshWatchlist } = useWatchlist()

onMounted(() => {
  startPrices()
  startStatus()
  refreshWatchlist()
  refreshPortfolio()
})
onBeforeUnmount(closePrices)

// Keep a valid selection: the first ticker to begin with, and again if the
// selected one is removed from the watchlist and is no longer priced.
watchEffect(() => {
  const current = selected.value
  const stillThere = current !== null && (tickers.value.includes(current) || current in prices.value)
  if (!stillThere) selected.value = tickers.value[0] ?? null
})
</script>

<template>
  <div class="flex min-h-dvh flex-col">
    <AppHeader />
    <main class="flex flex-1 flex-col gap-2 p-2 sm:gap-3 sm:p-3">
      <div class="dashboard-top">
        <WatchlistPanel class="[grid-area:watch]" />
        <PriceChart class="[grid-area:chart]" :ticker="selected" />
        <TradeTicket class="[grid-area:ticket]" :ticker="selected" />
        <!-- Sized by its min-height, never by the conversation: the panel is absolutely
             positioned inside, so a long chat scrolls instead of stretching the row. -->
        <div class="assistant-slot relative min-h-[20rem] [grid-area:assistant]">
          <AssistantPanel class="absolute inset-0" />
        </div>
      </div>
      <div class="grid grid-cols-1 items-start gap-2 sm:gap-3 min-[1200px]:grid-cols-2">
        <PositionsTable />
        <TradesPanel />
      </div>
    </main>
  </div>
</template>

<style>
.dashboard-top {
  display: grid;
  gap: 0.5rem;
  align-items: start;
  grid-template-columns: minmax(0, 1fr);
  grid-template-areas: "watch" "chart" "ticket" "assistant";
}

@media (min-width: 640px) {
  .dashboard-top {
    gap: 0.75rem;
  }
}

/* Wide layouts: the assistant takes whatever height the neighbouring column leaves. */
@media (min-width: 960px) {
  .assistant-slot {
    align-self: stretch;
  }
}

@media (min-width: 960px) {
  .dashboard-top {
    grid-template-columns: minmax(470px, 5fr) minmax(0, 6fr);
    grid-template-areas:
      "watch chart"
      "watch ticket"
      "watch assistant";
  }
}

@media (min-width: 1360px) {
  .dashboard-top {
    grid-template-columns: minmax(470px, 4fr) minmax(0, 6fr) 300px;
    grid-template-areas:
      "watch chart ticket"
      "watch chart assistant";
  }
}
</style>
