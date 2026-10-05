import type { MarketStatus } from '~/types/api'

const REFRESH_MS = 60_000
const RETRY_MS = 5_000

const status = ref<MarketStatus | null>(null)
const unreachable = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null
let started = false

/** Data-source health for the header badge: refreshed every minute, sooner while it is failing. */
export function useMarketStatus() {
  async function refresh() {
    if (timer) clearTimeout(timer)
    try {
      status.value = await useApi().status()
      unreachable.value = false
    } catch {
      unreachable.value = true
    }
    timer = setTimeout(refresh, unreachable.value ? RETRY_MS : REFRESH_MS)
  }

  function start() {
    if (started || !import.meta.client) return
    started = true
    void refresh()
  }

  return { status, unreachable, refresh, start }
}
