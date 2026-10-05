// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TradesPanel from '~/components/TradesPanel.vue'
import { ApiError } from '~/lib/api'
import { createFakeApi, fill, portfolioOf, position, resetAppState, type FakeApi } from '../helpers'

const api = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('~/composables/useApi', () => ({ useApi: () => api.current }))

let fake: FakeApi

beforeEach(() => {
  fake = createFakeApi()
  fake.watchlist.mockResolvedValue({ tickers: ['AAPL'] })
  api.current = fake
  resetAppState()
})

function panel() {
  const state = usePortfolio()
  state.portfolio.value = portfolioOf(8_000, [position('AAPL', 10, 190)])
  state.portfolioState.value = 'ready'
  state.trades.value = [
    { ...fill(2, 'AAPL', 'sell', 2.5, 200), realized_pnl: -25.5 },
    fill(1, 'AAPL', 'buy', 12.5, 190),
  ]
  state.tradesState.value = 'ready'
  const wrapper = mount(TradesPanel)
  const button = (label: string) => wrapper.findAll('button').find((b) => b.text() === label)
  return { wrapper, button }
}

describe('TradesPanel', () => {
  it('shows loading, unavailable and empty states', async () => {
    const wrapper = mount(TradesPanel)
    expect(wrapper.text()).toContain('Loading trades…')
    usePortfolio().tradesState.value = 'unavailable'
    await flushPromises()
    expect(wrapper.text()).toContain('Trade history unavailable')
    usePortfolio().tradesState.value = 'ready'
    await flushPromises()
    expect(wrapper.text()).toContain('No trades yet')
    // No portfolio yet, so nothing to reset.
    expect(wrapper.findAll('button')).toHaveLength(0)
  })

  it('lists fills in the order given, with realised P&L only on sells', () => {
    const { wrapper } = panel()
    const rows = wrapper.findAll('tbody tr').map((row) => row.findAll('td').map((td) => td.text()).slice(1))
    expect(rows).toEqual([
      ['sell', 'AAPL', '2.5', '200.00', '$500.00', '−$25.50'],
      ['buy', 'AAPL', '12.5', '190.00', '$2,375.00', '—'],
    ])
    const realised = wrapper.findAll('tbody tr').map((row) => row.findAll('td')[6]!.classes())
    expect(realised[0]).toContain('text-down')
    expect(realised[1]).toContain('text-muted')
  })

  it('asks before resetting and does nothing on cancel', async () => {
    const { wrapper, button } = panel()
    expect(wrapper.find('[role="alertdialog"]').exists()).toBe(false)

    await button('Reset portfolio')!.trigger('click')
    expect(wrapper.find('[role="alertdialog"]').text()).toContain('$10,000.00')
    expect(button('Reset portfolio')).toBeUndefined()

    await button('Cancel')!.trigger('click')
    expect(wrapper.find('[role="alertdialog"]').exists()).toBe(false)
    expect(fake.resetPortfolio).not.toHaveBeenCalled()
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
  })

  it('resets, reloads trades and the watchlist, and confirms', async () => {
    const { wrapper, button } = panel()
    fake.resetPortfolio.mockResolvedValue(portfolioOf(10_000))
    fake.trades.mockResolvedValue({ trades: [] })

    await button('Reset portfolio')!.trigger('click')
    await button('Yes, reset')!.trigger('click')
    await flushPromises()

    expect(fake.resetPortfolio).toHaveBeenCalledTimes(1)
    expect(fake.watchlist).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[role="alertdialog"]').exists()).toBe(false)
    expect(wrapper.find('[role="status"]').text()).toBe('Portfolio reset')
    expect(wrapper.text()).toContain('No trades yet')
    expect(usePortfolio().portfolio.value).toMatchObject({ cash: 10_000, positions: [] })
  })

  it('keeps the dialog open with the reason when the reset fails', async () => {
    const { wrapper, button } = panel()
    fake.resetPortfolio.mockRejectedValue(new ApiError(0, 'Cannot reach the server'))

    await button('Reset portfolio')!.trigger('click')
    await button('Yes, reset')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="alertdialog"] [role="alert"]').text()).toBe('Cannot reach the server')
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    expect(usePortfolio().portfolio.value!.cash).toBe(8_000)
    expect(button('Yes, reset')!.element.hasAttribute('disabled')).toBe(false)
  })
})
