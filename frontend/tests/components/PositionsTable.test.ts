// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PositionsTable from '~/components/PositionsTable.vue'
import { createFakeApi, portfolioOf, position, quote, resetAppState } from '../helpers'

const api = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('~/composables/useApi', () => ({ useApi: () => api.current }))

beforeEach(() => {
  api.current = createFakeApi()
  resetAppState()
})

function ready(...positions: ReturnType<typeof position>[]) {
  const state = usePortfolio()
  state.portfolio.value = portfolioOf(5_000, positions)
  state.portfolioState.value = 'ready'
}

describe('PositionsTable', () => {
  it('shows a loading notice, then the empty state', async () => {
    const wrapper = mount(PositionsTable)
    expect(wrapper.text()).toContain('Loading positions…')
    expect(wrapper.find('table').exists()).toBe(false)

    ready()
    await flushPromises()
    expect(wrapper.text()).toContain('No open positions')
    expect(wrapper.find('table').exists()).toBe(false)
  })

  it('says so when the portfolio service is unavailable', () => {
    usePortfolio().portfolioState.value = 'unavailable'
    const wrapper = mount(PositionsTable)
    expect(wrapper.text()).toContain('Positions unavailable')
    expect(wrapper.find('table').exists()).toBe(false)
  })

  it('renders one formatted row per position', () => {
    ready(position('AAPL', 10, 100), position('MSFT', 2.5, 400.5))
    const rows = mount(PositionsTable).findAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.findAll('td').map((td) => td.text())).toEqual([
      'AAPL', '10', '100.00', '100.00', '$1,000.00', '$0.00', '0.00%',
    ])
    expect(rows[1]!.findAll('td').map((td) => td.text())).toEqual([
      'MSFT', '2.5', '400.50', '400.50', '$1,001.25', '$0.00', '0.00%',
    ])
  })

  it('re-marks positions and totals when a streamed price arrives', async () => {
    ready(position('AAPL', 10, 100), position('MSFT', 2, 400))
    const wrapper = mount(PositionsTable)

    usePrices().prices.value = { AAPL: quote('AAPL', 110), MSFT: quote('MSFT', 390) }
    await flushPromises()

    const [aapl, msft] = wrapper.findAll('tbody tr').map((row) => row.findAll('td'))
    expect(aapl!.slice(3).map((td) => td.text())).toEqual(['110.00', '$1,100.00', '+$100.00', '+10.00%'])
    expect(aapl![5]!.classes()).toContain('text-up')
    expect(msft!.slice(3).map((td) => td.text())).toEqual(['390.00', '$780.00', '−$20.00', '−2.50%'])
    expect(msft![5]!.classes()).toContain('text-down')

    const header = wrapper.find('header').text()
    expect(header).toContain('$1,880.00')
    expect(header).toContain('+$80.00')
  })

  it('keeps the backend price for a position the stream has no quote for', async () => {
    ready(position('AAPL', 10, 100, 105))
    const wrapper = mount(PositionsTable)
    usePrices().prices.value = { MSFT: quote('MSFT', 1) }
    await flushPromises()
    expect(wrapper.findAll('tbody td').map((td) => td.text())).toEqual([
      'AAPL', '10', '100.00', '105.00', '$1,050.00', '+$50.00', '+5.00%',
    ])
  })

  it('selects the ticker of a clicked row and marks it', async () => {
    ready(position('AAPL', 10, 100), position('MSFT', 2, 400))
    const wrapper = mount(PositionsTable)
    const rows = wrapper.findAll('tbody tr')

    await rows[1]!.trigger('click')
    expect(useWatchlist().selected.value).toBe('MSFT')
    expect(rows[1]!.classes()).toContain('bg-raised')
    expect(rows[0]!.classes()).not.toContain('bg-raised')

    await rows[0]!.trigger('keydown', { key: 'Enter' })
    expect(useWatchlist().selected.value).toBe('AAPL')
  })
})
