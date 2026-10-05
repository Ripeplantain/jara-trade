// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TradeTicket from '~/components/TradeTicket.vue'
import { ApiError } from '~/lib/api'
import type { TradeResponse } from '~/types/api'
import { createFakeApi, fill, portfolioOf, position, quote, resetAppState, type FakeApi } from '../helpers'

const api = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('~/composables/useApi', () => ({ useApi: () => api.current }))

let fake: FakeApi

beforeEach(() => {
  fake = createFakeApi()
  fake.trades.mockResolvedValue({ trades: [] })
  api.current = fake
  resetAppState()
})

/** $5,000 cash, 10 AAPL held, AAPL at 190 and MSFT at 420. */
function ticket(ticker: string | null = 'AAPL') {
  const state = usePortfolio()
  state.portfolio.value = portfolioOf(5_000, [position('AAPL', 10, 180, 190)])
  state.portfolioState.value = 'ready'
  usePrices().prices.value = { AAPL: quote('AAPL', 190), MSFT: quote('MSFT', 420) }

  const wrapper = mount(TradeTicket, { props: { ticker } })
  const parts = {
    wrapper,
    input: () => wrapper.find<HTMLInputElement>('#trade-quantity'),
    submit: () => wrapper.find<HTMLButtonElement>('button[type="submit"]'),
    side: (label: 'Buy' | 'Sell') => wrapper.findAll('[role="group"] button').find((b) => b.text() === label)!,
    max: () => wrapper.findAll('button').find((b) => ['Max affordable', 'Sell all'].includes(b.text()))!,
    /** The <dd> next to a <dt> label. */
    figure: (label: string) => {
      const dt = wrapper.findAll('dt').find((el) => el.text() === label)
      return dt ? (dt.element.nextElementSibling?.textContent ?? '').trim() : null
    },
    alert: () => wrapper.find('[role="alert"]'),
    status: () => wrapper.find('[role="status"]'),
    warning: () => wrapper.find('p.text-warn'),
    send: async () => {
      await wrapper.find('form').trigger('submit')
      await flushPromises()
    },
  }
  return parts
}

function filled(side: 'buy' | 'sell', quantity: number, cash: number, held: number): TradeResponse {
  return {
    trade: fill(1, 'AAPL', side, quantity, 190),
    portfolio: portfolioOf(cash, held ? [position('AAPL', held, 180, 190)] : []),
  }
}

describe('TradeTicket', () => {
  it('shows the selected ticker, its price, cash and holding', () => {
    const t = ticket()
    expect(t.wrapper.find('header').text()).toContain('AAPL')
    expect(t.wrapper.find('header').text()).toContain('190.00')
    expect(t.figure('Cash available')).toBe('$5,000.00')
    expect(t.figure('Shares held')).toBe('10')
    expect(t.figure('Estimated cost')).toBe('—')
    expect(t.submit().text()).toBe('Buy AAPL')
  })

  it('cannot submit without a ticker', async () => {
    const t = ticket(null)
    await t.input().setValue('3')
    expect(t.submit().text()).toBe('Select a ticker')
    expect(t.submit().element.disabled).toBe(true)
    expect(t.max().element.hasAttribute('disabled')).toBe(true)
    await t.send()
    expect(fake.placeTrade).not.toHaveBeenCalled()
  })

  it.each(['', '0', '-2', 'abc'])('keeps submit disabled for quantity %j', async (value) => {
    const t = ticket()
    await t.input().setValue(value)
    expect(t.submit().element.disabled).toBe(true)
    await t.send()
    expect(fake.placeTrade).not.toHaveBeenCalled()
  })

  it('estimates the cost as the quantity is typed and follows the live price', async () => {
    const t = ticket()
    await t.input().setValue('3')
    expect(t.figure('Estimated cost')).toBe('$570.00')
    expect(t.submit().element.disabled).toBe(false)

    usePrices().prices.value = { AAPL: quote('AAPL', 200.5) }
    await flushPromises()
    expect(t.figure('Estimated cost')).toBe('$601.50')
  })

  it('places a buy, confirms the fill and shows the new cash and holding', async () => {
    fake.placeTrade.mockResolvedValue(filled('buy', 3, 4_430, 13))
    const t = ticket()
    await t.input().setValue('3')
    await t.send()

    expect(fake.placeTrade).toHaveBeenCalledExactlyOnceWith({ ticker: 'AAPL', side: 'buy', quantity: 3 })
    expect(t.status().text()).toBe('Bought 3 AAPL at $190.00 — $570.00 total')
    expect(t.alert().exists()).toBe(false)
    expect(t.input().element.value).toBe('')
    expect(t.figure('Cash available')).toBe('$4,430.00')
    expect(t.figure('Shares held')).toBe('13')
  })

  it('switches to sell: labels, estimate and the order side', async () => {
    fake.placeTrade.mockResolvedValue(filled('sell', 2.5, 5_475, 7.5))
    const t = ticket()
    await t.side('Sell').trigger('click')
    expect(t.side('Sell').attributes('aria-pressed')).toBe('true')
    expect(t.side('Buy').attributes('aria-pressed')).toBe('false')
    expect(t.submit().text()).toBe('Sell AAPL')

    await t.input().setValue('2.5')
    expect(t.figure('Estimated proceeds')).toBe('$475.00')
    await t.send()

    expect(fake.placeTrade).toHaveBeenCalledExactlyOnceWith({ ticker: 'AAPL', side: 'sell', quantity: 2.5 })
    expect(t.status().text()).toBe('Sold 2.5 AAPL at $190.00 — $475.00 total')
    expect(t.figure('Shares held')).toBe('7.5')
  })

  it("shows the backend's refusal and keeps the quantity for another try", async () => {
    fake.placeTrade.mockRejectedValue(new ApiError(400, 'Insufficient cash: need $19000.00, have $5000.00'))
    const t = ticket()
    await t.input().setValue('100')
    await t.send()

    expect(t.alert().text()).toBe('Insufficient cash: need $19000.00, have $5000.00')
    expect(t.status().exists()).toBe(false)
    expect(t.input().element.value).toBe('100')
    expect(t.figure('Cash available')).toBe('$5,000.00')
    expect(t.submit().element.disabled).toBe(false)

    await t.input().setValue('1')
    expect(t.alert().exists()).toBe(false)
  })

  it('warns, without blocking, when a buy costs more than the cash', async () => {
    const t = ticket()
    await t.input().setValue('26') // 4,940: affordable
    expect(t.warning().exists()).toBe(false)
    await t.input().setValue('27') // 5,130
    expect(t.warning().text()).toBe('Estimated cost is more than your cash.')
    expect(t.submit().element.disabled).toBe(false)
  })

  it('warns when selling more than is held, or something not held at all', async () => {
    const t = ticket()
    await t.side('Sell').trigger('click')
    await t.input().setValue('10')
    expect(t.warning().exists()).toBe(false)
    await t.input().setValue('10.5')
    expect(t.warning().text()).toBe('You hold 10 shares.')

    await t.wrapper.setProps({ ticker: 'MSFT' })
    await t.input().setValue('1')
    expect(t.figure('Shares held')).toBe('0')
    expect(t.warning().text()).toBe('You hold no MSFT.')
  })

  it('fills the largest affordable whole quantity, or the whole holding when selling', async () => {
    const t = ticket()
    await t.max().trigger('click')
    expect(t.input().element.value).toBe('26') // floor(5000 / 190)
    expect(t.figure('Estimated cost')).toBe('$4,940.00')
    expect(t.warning().exists()).toBe(false)

    await t.side('Sell').trigger('click')
    expect(t.max().text()).toBe('Sell all')
    await t.max().trigger('click')
    expect(t.input().element.value).toBe('10')
  })

  it('disables the shortcut when there is nothing to buy with or nothing to sell', async () => {
    const t = ticket('MSFT')
    expect(t.max().element.hasAttribute('disabled')).toBe(false)
    await t.side('Sell').trigger('click')
    expect(t.max().element.hasAttribute('disabled')).toBe(true) // no MSFT held

    usePortfolio().portfolio.value = portfolioOf(0, [position('AAPL', 10, 180, 190)])
    await t.side('Buy').trigger('click')
    expect(t.max().element.hasAttribute('disabled')).toBe(true) // no cash
  })

  it('locks the form while an order is in flight and sends it only once', async () => {
    let resolve!: (value: TradeResponse) => void
    fake.placeTrade.mockReturnValue(new Promise<TradeResponse>((r) => (resolve = r)))
    const t = ticket()
    await t.input().setValue('1')

    await t.wrapper.find('form').trigger('submit')
    expect(t.submit().text()).toBe('Submitting…')
    expect(t.submit().element.disabled).toBe(true)
    await t.wrapper.find('form').trigger('submit')
    expect(fake.placeTrade).toHaveBeenCalledTimes(1)

    resolve(filled('buy', 1, 4_810, 11))
    await flushPromises()
    expect(t.submit().text()).toBe('Buy AAPL')
    expect(t.status().text()).toContain('Bought 1 AAPL')
  })

  it('clears the quantity and messages when another ticker is selected', async () => {
    fake.placeTrade.mockRejectedValue(new ApiError(409, 'No price available for AAPL yet'))
    const t = ticket()
    await t.input().setValue('4')
    await t.send()
    expect(t.alert().exists()).toBe(true)

    await t.wrapper.setProps({ ticker: 'MSFT' })
    expect(t.input().element.value).toBe('')
    expect(t.alert().exists()).toBe(false)
    expect(t.submit().text()).toBe('Buy MSFT')
    expect(t.wrapper.find('header').text()).toContain('420.00')
  })

  it('clears the confirmation when the side changes', async () => {
    fake.placeTrade.mockResolvedValue(filled('buy', 1, 4_810, 11))
    const t = ticket()
    await t.input().setValue('1')
    await t.send()
    expect(t.status().exists()).toBe(true)
    await t.side('Sell').trigger('click')
    expect(t.status().exists()).toBe(false)
  })

  it('shows a dash for the price and no estimate while the ticker has no quote', async () => {
    const t = ticket('PYPL')
    await t.input().setValue('2')
    expect(t.wrapper.find('header').text()).toContain('—')
    expect(t.figure('Estimated cost')).toBe('—')
    expect(t.max().element.hasAttribute('disabled')).toBe(true)
    expect(t.submit().element.disabled).toBe(false) // the backend decides (409)
  })

  it('replaces the form with a notice while the portfolio service is down', () => {
    usePortfolio().portfolioState.value = 'unavailable'
    usePrices().prices.value = { AAPL: quote('AAPL', 190) }
    const wrapper = mount(TradeTicket, { props: { ticker: 'AAPL' } })
    expect(wrapper.text()).toContain('Trading unavailable')
    expect(wrapper.find('form').exists()).toBe(false)
  })

  it('shows dashes for cash and holding before the portfolio has loaded', () => {
    usePrices().prices.value = { AAPL: quote('AAPL', 190) }
    const wrapper = mount(TradeTicket, { props: { ticker: 'AAPL' } })
    const figures = wrapper.findAll('dd').map((dd) => dd.text())
    expect(figures).toEqual(['—', '—', '—'])
  })
})
