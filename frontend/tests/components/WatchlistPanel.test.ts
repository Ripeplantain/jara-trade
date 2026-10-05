// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WatchlistPanel from '~/components/WatchlistPanel.vue'
import { ApiError } from '~/lib/api'
import { createFakeApi, quote, resetAppState, type FakeApi } from '../helpers'

const api = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('~/composables/useApi', () => ({ useApi: () => api.current }))

let fake: FakeApi

beforeEach(() => {
  fake = createFakeApi()
  api.current = fake
  resetAppState()
})

const PRICES = {
  AAPL: quote('AAPL', 190.5, { change: 0.5, prev_close: 188, day_change: 2.5, day_change_percent: 1.3298 }),
  MSFT: quote('MSFT', 419, { change: -1, prev_close: 425, day_change: -6, day_change_percent: -1.4118 }),
  TSLA: quote('TSLA', 250),
}

function stream(prices = PRICES) {
  const state = usePrices()
  state.prices.value = prices
  state.hasPrices.value = true
  state.connection.value = 'live'
}

/** A panel whose watchlist endpoint answered with `tickers`. */
async function loaded(tickers: string[]) {
  fake.watchlist.mockResolvedValue({ tickers })
  await useWatchlist().refresh()
  stream()
  return mount(WatchlistPanel)
}

const cells = (wrapper: ReturnType<typeof mount>) =>
  wrapper.findAll('tbody tr').map((row) => row.findAll('td').map((td) => td.text()))
const tickersIn = (wrapper: ReturnType<typeof mount>) => cells(wrapper).map((row) => row[0])

describe('WatchlistPanel', () => {
  it('waits for prices, then says when the stream is down', async () => {
    const wrapper = mount(WatchlistPanel)
    expect(wrapper.text()).toContain('Waiting for prices…')
    expect(wrapper.find('table').exists()).toBe(false)

    usePrices().connection.value = 'reconnecting'
    await flushPromises()
    expect(wrapper.text()).toContain('Price stream unavailable')
    expect(wrapper.find('p.text-warn').exists()).toBe(true)
  })

  it('lists the saved watchlist in its own order with formatted quotes', async () => {
    const wrapper = await loaded(['TSLA', 'AAPL', 'MSFT'])
    expect(cells(wrapper).map((row) => row.slice(0, 5))).toEqual([
      ['TSLA', '250.00', '0.00', '—', '—'], // no previous close yet
      ['AAPL', '190.50', '+0.50', '+2.50', '+1.33%'],
      ['MSFT', '419.00', '−1.00', '−6.00', '−1.41%'],
    ])
  })

  it('colours tick and day changes by direction', async () => {
    const wrapper = await loaded(['TSLA', 'AAPL', 'MSFT'])
    const tones = wrapper.findAll('tbody tr').map((row) => {
      const tds = row.findAll('td')
      return [1, 2, 3, 4].map((i) => tds[i]!.classes().find((c) => ['text-up', 'text-down', 'text-muted'].includes(c)))
    })
    expect(tones).toEqual([
      [undefined, 'text-muted', 'text-muted', 'text-muted'],
      [undefined, 'text-up', 'text-up', 'text-up'],
      [undefined, 'text-down', 'text-down', 'text-down'],
    ])
  })

  it('shows dashes for a watched ticker the stream has not priced yet', async () => {
    const wrapper = await loaded(['ZZZZ', 'AAPL'])
    expect(cells(wrapper)[0]!.slice(0, 5)).toEqual(['ZZZZ', '—', '—', '—', '—'])
  })

  it('shows the empty state for an empty saved watchlist even while prices stream', async () => {
    const wrapper = await loaded([])
    expect(wrapper.text()).toContain('No tickers yet')
    expect(wrapper.find('table').exists()).toBe(false)
  })

  it('falls back to every streamed ticker, read-only, when the watchlist service is down', () => {
    stream()
    const wrapper = mount(WatchlistPanel)
    expect(tickersIn(wrapper)).toEqual(['AAPL', 'MSFT', 'TSLA'])
    expect(wrapper.text()).toContain('Watchlist service unavailable')
    expect(wrapper.find('input').element.disabled).toBe(true)
    expect(wrapper.find('button[type="submit"]').element.hasAttribute('disabled')).toBe(true)
    expect(wrapper.findAll('button[aria-label^="Remove"]')).toHaveLength(0)
  })

  it('adds a trimmed, upper-cased ticker, selects it and clears the box', async () => {
    const wrapper = await loaded(['AAPL'])
    fake.addToWatchlist.mockResolvedValue({ tickers: ['AAPL', 'MSFT'] })

    await wrapper.find('input').setValue('  msft ')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(fake.addToWatchlist).toHaveBeenCalledExactlyOnceWith('MSFT')
    expect(tickersIn(wrapper)).toEqual(['AAPL', 'MSFT'])
    expect(wrapper.find('input').element.value).toBe('')
    expect(useWatchlist().selected.value).toBe('MSFT')
    expect(wrapper.findAll('tbody tr')[1]!.attributes('aria-current')).toBe('true')
  })

  it('does not submit an empty or blank ticker', async () => {
    const wrapper = await loaded(['AAPL'])
    const add = wrapper.find<HTMLButtonElement>('button[type="submit"]')
    expect(add.element.disabled).toBe(true)
    await wrapper.find('input').setValue('   ')
    expect(add.element.disabled).toBe(true)
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(fake.addToWatchlist).not.toHaveBeenCalled()
  })

  it("shows the backend's message for a rejected ticker and keeps the draft", async () => {
    const wrapper = await loaded(['AAPL'])
    fake.addToWatchlist.mockRejectedValue(new ApiError(422, "Invalid ticker symbol: 'BAD;'"))

    await wrapper.find('input').setValue('bad;')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').text()).toBe("Invalid ticker symbol: 'BAD;'")
    expect(wrapper.find('input').element.value).toBe('bad;')
    expect(tickersIn(wrapper)).toEqual(['AAPL'])
    expect(useWatchlist().selected.value).toBeNull()
    expect(wrapper.find<HTMLButtonElement>('button[type="submit"]').element.disabled).toBe(false)

    await wrapper.find('input').setValue('bad')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('sends one request while an add is in flight', async () => {
    const wrapper = await loaded(['AAPL'])
    let resolve!: (value: { tickers: string[] }) => void
    fake.addToWatchlist.mockReturnValue(new Promise((r) => (resolve = r)))

    await wrapper.find('input').setValue('msft')
    await wrapper.find('form').trigger('submit')
    await wrapper.find('form').trigger('submit')
    expect(fake.addToWatchlist).toHaveBeenCalledTimes(1)
    expect(wrapper.find<HTMLButtonElement>('button[type="submit"]').element.disabled).toBe(true)

    resolve({ tickers: ['AAPL', 'MSFT'] })
    await flushPromises()
    expect(tickersIn(wrapper)).toEqual(['AAPL', 'MSFT'])
  })

  it('removes a row without selecting it', async () => {
    const wrapper = await loaded(['AAPL', 'TSLA'])
    fake.removeFromWatchlist.mockResolvedValue({ tickers: ['AAPL'] })

    await wrapper.find('button[aria-label="Remove TSLA from watchlist"]').trigger('click')
    await flushPromises()

    expect(fake.removeFromWatchlist).toHaveBeenCalledExactlyOnceWith('TSLA')
    expect(tickersIn(wrapper)).toEqual(['AAPL'])
    expect(useWatchlist().selected.value).toBeNull()
  })

  it('keeps the row and explains when a removal fails', async () => {
    const wrapper = await loaded(['AAPL', 'TSLA'])
    fake.removeFromWatchlist.mockRejectedValue(new ApiError(404, 'TSLA is not on the watchlist'))

    await wrapper.find('button[aria-label="Remove TSLA from watchlist"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').text()).toBe('TSLA is not on the watchlist')
    expect(tickersIn(wrapper)).toEqual(['AAPL', 'TSLA'])
  })

  it('selects a row by click or keyboard and marks only that row', async () => {
    const wrapper = await loaded(['AAPL', 'MSFT'])
    const rows = wrapper.findAll('tbody tr')

    await rows[1]!.trigger('click')
    expect(useWatchlist().selected.value).toBe('MSFT')
    expect(rows.map((row) => row.attributes('aria-current'))).toEqual([undefined, 'true'])

    await rows[0]!.trigger('keydown', { key: 'Enter' })
    expect(useWatchlist().selected.value).toBe('AAPL')
    expect(rows.map((row) => row.attributes('aria-current'))).toEqual(['true', undefined])
  })

  it('alternates the flash class so a repeat move restarts the animation', async () => {
    const wrapper = await loaded(['AAPL', 'MSFT'])
    const { flashes } = usePrices()
    const row = () => wrapper.findAll('tbody tr')[0]!.classes().filter((c) => c.startsWith('flash-'))
    expect(row()).toEqual([])

    flashes.AAPL = { direction: 'up', count: 1 }
    await flushPromises()
    expect(row()).toEqual(['flash-up-a'])

    flashes.AAPL = { direction: 'up', count: 2 }
    await flushPromises()
    expect(row()).toEqual(['flash-up-b'])

    flashes.AAPL = { direction: 'down', count: 3 }
    await flushPromises()
    expect(row()).toEqual(['flash-down-a'])
    expect(wrapper.findAll('tbody tr')[1]!.classes().some((c) => c.startsWith('flash-'))).toBe(false)
  })

  it('draws a sparkline only once a ticker has two history points', async () => {
    const wrapper = await loaded(['AAPL', 'MSFT', 'TSLA'])
    usePrices().histories.value = {
      AAPL: [{ timestamp: 0, price: 189 }, { timestamp: 5, price: 190.5 }],
      MSFT: [{ timestamp: 0, price: 420 }, { timestamp: 5, price: 419 }],
      TSLA: [{ timestamp: 0, price: 250 }],
    }
    await flushPromises()
    const svgs = wrapper.findAll('tbody svg')
    expect(svgs.map((svg) => svg.find('path').exists())).toEqual([true, true, false])
    expect(svgs[0]!.classes()).toContain('text-up')
    expect(svgs[1]!.classes()).toContain('text-down')
    expect(svgs[0]!.find('path').attributes('d')).toMatch(/^M0\.0,\d+\.\d+L88\.0,\d+\.\d+$/)
  })
})
