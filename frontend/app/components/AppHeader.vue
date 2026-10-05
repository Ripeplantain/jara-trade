<script setup lang="ts">
import { formatMoney, formatPercent, formatSignedMoney, formatTime, trend } from '~/lib/format'

const { connection } = usePrices()
const { status, unreachable } = useMarketStatus()
const { live, portfolioState } = usePortfolio()

const MODE_LABELS = { simulated: 'Simulated', snapshot: 'Delayed 15 min', eod: 'End of day' } as const

const source = computed(() => {
  const s = status.value
  if (!s) return { label: unreachable.value ? 'Source offline' : 'Source …', warn: unreachable.value, title: '' }
  const warn = s.consecutive_failures > 0 || unreachable.value
  const parts: string[] = [`Source: ${s.source}`]
  if (s.consecutive_failures > 0) parts.push(`${s.consecutive_failures} failed update(s) in a row`)
  if (s.last_error) parts.push(`Last error: ${s.last_error}`)
  if (s.last_success) parts.push(`Last update ${formatTime(s.last_success)}`)
  if (unreachable.value) parts.push('Status endpoint not responding')
  return { label: MODE_LABELS[s.mode] ?? s.mode, warn, title: parts.join(' · ') }
})

const link = computed(() =>
  connection.value === 'live'
    ? { label: 'Live', dot: 'bg-up', text: 'text-up', pulse: false }
    : connection.value === 'reconnecting'
      ? { label: 'Reconnecting', dot: 'bg-warn', text: 'text-warn', pulse: true }
      : { label: 'Connecting', dot: 'bg-faint', text: 'text-muted', pulse: true },
)

const TONE = { up: 'text-up', down: 'text-down', flat: 'text-fg' } as const
const pnlTone = computed(() => TONE[trend(live.value?.total_pnl)])

const theme = ref<'dark' | 'light'>('dark')
onMounted(() => {
  theme.value = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'
})
function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  if (theme.value === 'light') document.documentElement.setAttribute('data-theme', 'light')
  else document.documentElement.removeAttribute('data-theme')
  try {
    localStorage.setItem('jara-theme', theme.value)
  } catch {
    // Private mode: the choice just lasts for this page view.
  }
}
</script>

<template>
  <header class="border-b border-line bg-panel">
    <div class="flex flex-wrap items-center gap-x-6 gap-y-2 px-3 py-2 sm:px-4">
      <div class="flex items-center gap-3">
        <h1 class="text-[15px] font-semibold tracking-tight">
          Jara<span class="text-accent">Trade</span>
        </h1>
        <span
          class="rounded border px-1.5 py-0.5 text-[11px] font-medium"
          :class="source.warn ? 'border-warn/50 bg-warn/10 text-warn' : 'border-line bg-raised text-muted'"
          :title="source.title"
        >
          <span v-if="source.warn" aria-hidden="true">⚠ </span>{{ source.label }}
        </span>
        <span class="flex items-center gap-1.5 text-[11px] font-medium" :class="link.text" role="status">
          <span class="size-1.5 rounded-full" :class="[link.dot, link.pulse && 'pulse-dot']" />
          {{ link.label }}
        </span>
      </div>

      <dl class="order-last flex w-full items-baseline gap-x-6 gap-y-1 sm:order-none sm:ml-auto sm:w-auto">
        <template v-if="live">
          <div>
            <dt class="label">Total value</dt>
            <dd class="num text-[15px] font-semibold">{{ formatMoney(live.total_value) }}</dd>
          </div>
          <div>
            <dt class="label">Cash</dt>
            <dd class="num text-[15px] font-semibold">{{ formatMoney(live.cash) }}</dd>
          </div>
          <div>
            <dt class="label">Total P&amp;L</dt>
            <dd class="num text-[15px] font-semibold" :class="pnlTone">
              {{ formatSignedMoney(live.total_pnl) }}
              <span class="text-xs font-medium">{{ formatPercent(live.total_pnl_percent) }}</span>
            </dd>
          </div>
        </template>
        <p v-else class="text-xs text-faint">
          {{ portfolioState === 'unavailable' ? 'Portfolio unavailable' : 'Loading portfolio…' }}
        </p>
      </dl>

      <button
        type="button"
        class="ml-auto rounded border border-line px-2 py-1 text-[11px] text-muted hover:bg-raised hover:text-fg sm:ml-0"
        :aria-label="`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`"
        @click="toggleTheme"
      >
        {{ theme === 'dark' ? 'Light' : 'Dark' }}
      </button>
    </div>
  </header>
</template>
