/** Portfolio maths recomputed on the client from live prices. */
import type { Portfolio, Position, PriceMap } from '~/types/api'

export function roundMoney(value: number): number {
  const rounded = Math.round((value + Number.EPSILON) * 100) / 100
  return rounded === 0 ? 0 : rounded
}

function percentOf(part: number, whole: number): number {
  return whole ? Math.round((part / whole) * 100 * 10000) / 10000 : 0
}

/** Re-mark one position at `price` (falls back to the price the backend last reported). */
export function markPosition(position: Position, price: number | undefined): Position {
  const mark = typeof price === 'number' && Number.isFinite(price) && price > 0 ? price : position.price
  const marketValue = roundMoney(position.quantity * mark)
  const unrealized = roundMoney(marketValue - position.cost_basis)
  return {
    ...position,
    price: mark,
    market_value: marketValue,
    unrealized_pnl: unrealized,
    unrealized_pnl_percent: percentOf(unrealized, position.cost_basis),
  }
}

/** Every position re-marked at the latest streamed price. */
export function markPositions(positions: Position[], prices: PriceMap): Position[] {
  return positions.map((p) => markPosition(p, prices[p.ticker]?.price))
}

/**
 * The portfolio as it stands at the latest streamed prices. Cash, starting cash
 * and realised P&L only change when a trade happens, so they pass through.
 */
export function markPortfolio(portfolio: Portfolio, prices: PriceMap): Portfolio {
  const positions = markPositions(portfolio.positions, prices)
  const positionsValue = roundMoney(positions.reduce((sum, p) => sum + p.market_value, 0))
  const unrealized = roundMoney(positions.reduce((sum, p) => sum + p.unrealized_pnl, 0))
  const totalValue = roundMoney(portfolio.cash + positionsValue)
  const totalPnl = roundMoney(totalValue - portfolio.starting_cash)
  return {
    ...portfolio,
    positions,
    positions_value: positionsValue,
    total_value: totalValue,
    unrealized_pnl: unrealized,
    total_pnl: totalPnl,
    total_pnl_percent: percentOf(totalPnl, portfolio.starting_cash),
  }
}

/** What an order of `quantity` shares would cost (or raise) at `price`. */
export function estimateTotal(quantity: number, price: number | undefined): number | null {
  if (!Number.isFinite(quantity) || quantity <= 0) return null
  if (typeof price !== 'number' || !Number.isFinite(price) || price <= 0) return null
  return roundMoney(quantity * price)
}

/**
 * The largest quantity `cash` can buy at `price`: whole shares when at least one
 * is affordable, otherwise a fractional amount rounded down to four places.
 */
export function maxAffordable(cash: number, price: number | undefined): number {
  if (typeof price !== 'number' || !Number.isFinite(price) || price <= 0) return 0
  if (!Number.isFinite(cash) || cash <= 0) return 0
  // The epsilon keeps an exact multiple (70.07 / 10.01) from flooring one short;
  // the cost check keeps it from ever suggesting more than cash covers.
  const fits = (quantity: number) => roundMoney(quantity * price) <= cash
  const whole = Math.floor(cash / price + 1e-9)
  if (whole >= 1) return fits(whole) ? whole : whole - 1
  const units = Math.floor((cash / price) * 10000 + 1e-6)
  return (fits(units / 10000) ? units : Math.max(units - 1, 0)) / 10000
}
