/** Number formatters. Every figure in the UI goes through one of these. */

const DASH = '—'
// A real minus sign lines up with "+" in tabular figures; a hyphen does not.
const MINUS = '−'

const usd = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

function isNumber(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** Treat anything that rounds to zero at `digits` places as zero, so "-0.00" never shows. */
function settle(value: number, digits: number): number {
  const rounded = Number(value.toFixed(digits))
  return rounded === 0 ? 0 : rounded
}

/** `1234.5` → `$1,234.50`, `-3` → `−$3.00`. */
export function formatMoney(value: number | null | undefined): string {
  if (!isNumber(value)) return DASH
  const v = settle(value, 2)
  return `${v < 0 ? MINUS : ''}$${usd.format(Math.abs(v))}`
}

/** Like `formatMoney` but always signed: `+$12.00`, `−$3.50`, `$0.00`. */
export function formatSignedMoney(value: number | null | undefined): string {
  if (!isNumber(value)) return DASH
  const v = settle(value, 2)
  const sign = v > 0 ? '+' : v < 0 ? MINUS : ''
  return `${sign}$${usd.format(Math.abs(v))}`
}

/** A bare price with two decimals and no currency symbol: `190.25`. */
export function formatPrice(value: number | null | undefined): string {
  if (!isNumber(value)) return DASH
  return usd.format(value)
}

/** A signed price change: `+0.42`, `−1.10`, `0.00`. */
export function formatSignedPrice(value: number | null | undefined): string {
  if (!isNumber(value)) return DASH
  const v = settle(value, 2)
  const sign = v > 0 ? '+' : v < 0 ? MINUS : ''
  return `${sign}${usd.format(Math.abs(v))}`
}

/** A signed percentage where the input is already in percent units: `1.5` → `+1.50%`. */
export function formatPercent(value: number | null | undefined): string {
  if (!isNumber(value)) return DASH
  const v = settle(value, 2)
  const sign = v > 0 ? '+' : v < 0 ? MINUS : ''
  return `${sign}${Math.abs(v).toFixed(2)}%`
}

/** Share quantity: whole numbers stay whole, fractions keep up to four places. */
export function formatQuantity(value: number | null | undefined): string {
  if (!isNumber(value)) return DASH
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 }).format(value)
}

/** Which way a figure points, for colouring. Values that display as zero are flat. */
export function trend(value: number | null | undefined, digits = 2): 'up' | 'down' | 'flat' {
  if (!isNumber(value)) return 'flat'
  const v = settle(value, digits)
  return v > 0 ? 'up' : v < 0 ? 'down' : 'flat'
}

/** Local wall-clock time of a Unix-seconds timestamp: `14:03:27`. */
export function formatTime(unixSeconds: number | null | undefined, withSeconds = true): string {
  if (!isNumber(unixSeconds)) return DASH
  return new Date(unixSeconds * 1000).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: withSeconds ? '2-digit' : undefined,
    hour12: false,
  })
}

/** Time for today's timestamps, `3 Oct 14:03` for older ones. */
export function formatDateTime(unixSeconds: number | null | undefined, now = Date.now()): string {
  if (!isNumber(unixSeconds)) return DASH
  const date = new Date(unixSeconds * 1000)
  if (date.toDateString() === new Date(now).toDateString()) return formatTime(unixSeconds)
  const day = date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
  return `${day} ${formatTime(unixSeconds, false)}`
}
