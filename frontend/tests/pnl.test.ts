import { describe, expect, it } from 'vitest'
import { estimateTotal, markPortfolio, markPosition, markPositions, maxAffordable, roundMoney } from '~/lib/pnl'
import type { Portfolio, Position, PriceMap, PriceUpdate } from '~/types/api'

function quote(ticker: string, price: number): PriceUpdate {
  return {
    ticker,
    price,
    previous_price: price,
    timestamp: 1,
    direction: 'flat',
    change: 0,
    prev_close: null,
    day_change: null,
    day_change_percent: null,
  }
}

const aapl: Position = {
  ticker: 'AAPL',
  quantity: 10,
  avg_cost: 100,
  price: 100,
  market_value: 1000,
  cost_basis: 1000,
  unrealized_pnl: 0,
  unrealized_pnl_percent: 0,
}

const portfolio: Portfolio = {
  cash: 9000,
  positions_value: 1000,
  total_value: 10000,
  starting_cash: 10000,
  total_pnl: 0,
  total_pnl_percent: 0,
  realized_pnl: 0,
  unrealized_pnl: 0,
  positions: [aapl],
}

describe('markPosition', () => {
  it('revalues a position at the live price', () => {
    const marked = markPosition(aapl, 110)
    expect(marked.price).toBe(110)
    expect(marked.market_value).toBe(1100)
    expect(marked.unrealized_pnl).toBe(100)
    expect(marked.unrealized_pnl_percent).toBe(10)
  })

  it('keeps the backend price when there is no live one', () => {
    expect(markPosition(aapl, undefined)).toEqual(aapl)
  })

  it('handles fractional quantities without float noise', () => {
    const marked = markPosition({ ...aapl, quantity: 0.1, cost_basis: 10 }, 100.3)
    expect(marked.market_value).toBe(10.03)
    expect(marked.unrealized_pnl).toBe(0.03)
  })
})

describe('markPortfolio', () => {
  it('recomputes totals and P&L from streamed prices', () => {
    const prices: PriceMap = { AAPL: quote('AAPL', 95) }
    const live = markPortfolio(portfolio, prices)
    expect(live.positions_value).toBe(950)
    expect(live.total_value).toBe(9950)
    expect(live.unrealized_pnl).toBe(-50)
    expect(live.total_pnl).toBe(-50)
    expect(live.total_pnl_percent).toBe(-0.5)
    expect(live.cash).toBe(9000)
  })

  it('leaves the input untouched and ignores unrelated tickers', () => {
    const live = markPortfolio(portfolio, { MSFT: quote('MSFT', 1) })
    expect(live.total_value).toBe(10000)
    expect(portfolio.positions[0]).toBe(aapl)
    expect(aapl.price).toBe(100)
  })

  it('includes realised gains through cash in total P&L', () => {
    const live = markPortfolio({ ...portfolio, cash: 9200, realized_pnl: 200 }, { AAPL: quote('AAPL', 100) })
    expect(live.total_pnl).toBe(200)
    expect(live.total_pnl_percent).toBe(2)
  })
})

describe('order helpers', () => {
  it('estimates an order total', () => {
    expect(estimateTotal(3, 190.255)).toBe(570.77)
    expect(estimateTotal(0, 190)).toBeNull()
    expect(estimateTotal(2, undefined)).toBeNull()
  })

  it('buys whole shares when it can', () => {
    expect(maxAffordable(1000, 190)).toBe(5)
  })

  it('falls back to a fraction, rounded down, when one share is too dear', () => {
    expect(maxAffordable(100, 300)).toBe(0.3333)
    expect(maxAffordable(100, 300) * 300).toBeLessThanOrEqual(100)
  })

  it('returns zero without cash or a price', () => {
    expect(maxAffordable(0, 100)).toBe(0)
    expect(maxAffordable(100, undefined)).toBe(0)
  })
})

const msft: Position = {
  ticker: 'MSFT',
  quantity: 2,
  avg_cost: 400,
  price: 410,
  market_value: 820,
  cost_basis: 800,
  unrealized_pnl: 20,
  unrealized_pnl_percent: 2.5,
}

describe('roundMoney', () => {
  it('rounds to cents, half up', () => {
    expect(roundMoney(1.005)).toBe(1.01)
    expect(roundMoney(570.765)).toBe(570.77)
    expect(roundMoney(0.1 + 0.2)).toBe(0.3)
    expect(roundMoney(-12.345)).toBe(-12.34)
  })

  it('never returns negative zero', () => {
    expect(Object.is(roundMoney(-0.001), 0)).toBe(true)
    expect(Object.is(roundMoney(-0), 0)).toBe(true)
  })
})

describe('marking with missing or unusable prices', () => {
  it.each([0, -5, Number.NaN, Infinity])('ignores a live price of %s', (price) => {
    expect(markPosition(msft, price)).toEqual(msft)
  })

  it('re-marks only the positions that have a quote', () => {
    const [a, m] = markPositions([aapl, msft], { AAPL: quote('AAPL', 90) })
    expect(a).toMatchObject({ price: 90, market_value: 900, unrealized_pnl: -100, unrealized_pnl_percent: -10 })
    expect(m).toEqual(msft)
  })

  it('totals a portfolio where one position has no live price', () => {
    const held: Portfolio = { ...portfolio, cash: 8200, positions: [aapl, msft] }
    const live = markPortfolio(held, { AAPL: quote('AAPL', 90) })
    expect(live.positions_value).toBe(1720) // 900 live + 820 as last reported
    expect(live.unrealized_pnl).toBe(-80)
    expect(live.total_value).toBe(9920)
    expect(live.total_pnl).toBe(-80)
    expect(live.total_pnl_percent).toBe(-0.8)
  })

  it('recomputes stale totals from the positions even with no prices at all', () => {
    const stale: Portfolio = { ...portfolio, positions_value: 1, total_value: 2, unrealized_pnl: 3, total_pnl: 4 }
    const live = markPortfolio(stale, {})
    expect(live).toMatchObject({ positions_value: 1000, total_value: 10000, unrealized_pnl: 0, total_pnl: 0 })
  })

  it('handles an empty portfolio', () => {
    const empty: Portfolio = { ...portfolio, cash: 10000, positions: [] }
    const live = markPortfolio(empty, { AAPL: quote('AAPL', 500) })
    expect(live.positions).toEqual([])
    expect(live).toMatchObject({ positions_value: 0, total_value: 10000, unrealized_pnl: 0, total_pnl: 0, total_pnl_percent: 0 })
    expect(markPositions([], {})).toEqual([])
  })

  it('passes cash, starting cash and realised P&L through untouched', () => {
    const live = markPortfolio({ ...portfolio, realized_pnl: -42.5 }, { AAPL: quote('AAPL', 250) })
    expect(live).toMatchObject({ cash: 9000, starting_cash: 10000, realized_pnl: -42.5 })
  })

  it('reports 0% rather than dividing by a zero cost basis or zero starting cash', () => {
    const free = markPosition({ ...aapl, cost_basis: 0, avg_cost: 0 }, 50)
    expect(free.unrealized_pnl).toBe(500)
    expect(free.unrealized_pnl_percent).toBe(0)
    expect(markPortfolio({ ...portfolio, starting_cash: 0 }, {}).total_pnl_percent).toBe(0)
  })

  it('keeps the sum of the rows equal to the totals with awkward fractions', () => {
    const positions: Position[] = [
      { ...aapl, quantity: 0.3333, cost_basis: 63.33 },
      { ...msft, quantity: 1.1111, cost_basis: 444.44 },
    ]
    const live = markPortfolio({ ...portfolio, positions }, { AAPL: quote('AAPL', 190.01), MSFT: quote('MSFT', 399.99) })
    const [a, m] = live.positions
    expect(a!.market_value).toBe(63.33) // 0.3333 * 190.01 = 63.330333
    expect(m!.market_value).toBe(444.43) // 1.1111 * 399.99 = 444.428889
    expect(live.positions_value).toBe(507.76)
    expect(live.unrealized_pnl).toBe(roundMoney(a!.unrealized_pnl + m!.unrealized_pnl))
    expect(live.total_pnl).toBe(roundMoney(live.cash + live.positions_value - live.starting_cash))
  })
})

describe('order helper edge cases', () => {
  it('estimates nothing for unusable input', () => {
    expect(estimateTotal(-1, 190)).toBeNull()
    expect(estimateTotal(Number.NaN, 190)).toBeNull()
    expect(estimateTotal(Infinity, 190)).toBeNull()
    expect(estimateTotal(1, 0)).toBeNull()
    expect(estimateTotal(1, -190)).toBeNull()
    expect(estimateTotal(1, Number.NaN)).toBeNull()
  })

  it('estimates the smallest lot to the cent', () => {
    expect(estimateTotal(0.0001, 190)).toBe(0.02)
    expect(estimateTotal(0.1234, 190)).toBe(23.45)
  })

  it('returns zero for unusable cash or price', () => {
    expect(maxAffordable(-50, 100)).toBe(0)
    expect(maxAffordable(Number.NaN, 100)).toBe(0)
    expect(maxAffordable(Infinity, 100)).toBe(0)
    expect(maxAffordable(100, 0)).toBe(0)
    expect(maxAffordable(100, -1)).toBe(0)
    expect(maxAffordable(100, Number.NaN)).toBe(0)
  })

  it('spends exactly all the cash when it is a whole multiple of the price', () => {
    expect(maxAffordable(10000, 800)).toBe(12)
    expect(maxAffordable(10000, 100)).toBe(100)
    expect(maxAffordable(380, 190)).toBe(2)
    expect(maxAffordable(190, 190)).toBe(1)
    expect(maxAffordable(189.99, 190)).toBe(0.9999)
  })

  it('never suggests more than the cash covers', () => {
    for (let cents = 1; cents <= 60000; cents += 137) {
      for (const price of [0.07, 10.01, 33.33, 190.13, 420, 799.99]) {
        const cash = cents / 100
        const quantity = maxAffordable(cash, price)
        expect(roundMoney(quantity * price), `cash ${cash} price ${price}`).toBeLessThanOrEqual(cash)
      }
    }
  })

  it('affords n shares when cash is exactly n times the price', () => {
    expect(maxAffordable(70.07, 10.01)).toBe(7)
    expect(maxAffordable(30.15, 10.05)).toBe(3)
  })

  // Same float quotient on the fractional path: 40.01 / 200.05 is exactly 0.2.
  it('affords the exact fraction when one share is too dear', () => {
    expect(maxAffordable(40.01, 200.05)).toBe(0.2)
  })
})
