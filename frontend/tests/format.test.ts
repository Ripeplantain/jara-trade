import { describe, expect, it } from 'vitest'
import {
  formatDateTime,
  formatMoney,
  formatPercent,
  formatPrice,
  formatQuantity,
  formatSignedMoney,
  formatSignedPrice,
  formatTime,
  trend,
} from '~/lib/format'

describe('formatMoney', () => {
  it('groups thousands and keeps two decimals', () => {
    expect(formatMoney(1234.5)).toBe('$1,234.50')
    expect(formatMoney(0)).toBe('$0.00')
  })

  it('puts the minus sign before the currency symbol', () => {
    expect(formatMoney(-3)).toBe('−$3.00')
  })

  it('never shows negative zero', () => {
    expect(formatMoney(-0.001)).toBe('$0.00')
    expect(formatSignedMoney(-0.004)).toBe('$0.00')
  })

  it('shows a dash for missing values', () => {
    expect(formatMoney(null)).toBe('—')
    expect(formatMoney(undefined)).toBe('—')
    expect(formatMoney(Number.NaN)).toBe('—')
  })
})

describe('signed formatters', () => {
  it('always shows the sign of a non-zero value', () => {
    expect(formatSignedMoney(12)).toBe('+$12.00')
    expect(formatSignedMoney(-1234.567)).toBe('−$1,234.57')
    expect(formatSignedPrice(0.42)).toBe('+0.42')
    expect(formatSignedPrice(-1.1)).toBe('−1.10')
    expect(formatSignedPrice(0)).toBe('0.00')
  })

  it('formats percentages that are already in percent units', () => {
    expect(formatPercent(1.5)).toBe('+1.50%')
    expect(formatPercent(-0.257)).toBe('−0.26%')
    expect(formatPercent(0)).toBe('0.00%')
    expect(formatPercent(null)).toBe('—')
  })
})

describe('formatPrice and formatQuantity', () => {
  it('formats a bare price', () => {
    expect(formatPrice(190.2)).toBe('190.20')
    expect(formatPrice(undefined)).toBe('—')
  })

  it('keeps whole quantities whole and fractions to four places', () => {
    expect(formatQuantity(10)).toBe('10')
    expect(formatQuantity(1500)).toBe('1,500')
    expect(formatQuantity(0.123456)).toBe('0.1235')
  })
})

describe('trend', () => {
  it('reports direction', () => {
    expect(trend(0.5)).toBe('up')
    expect(trend(-0.5)).toBe('down')
    expect(trend(0)).toBe('flat')
    expect(trend(null)).toBe('flat')
  })

  it('treats a value that displays as zero as flat', () => {
    expect(trend(0.004)).toBe('flat')
    expect(trend(-0.004)).toBe('flat')
  })
})

describe('missing values', () => {
  it('shows a dash for null, undefined and non-finite input in every formatter', () => {
    const formatters = [
      formatMoney, formatSignedMoney, formatPrice, formatSignedPrice, formatPercent, formatQuantity,
      formatTime, formatDateTime,
    ]
    for (const format of formatters) {
      for (const value of [null, undefined, Number.NaN, Infinity, -Infinity]) {
        expect(format(value), `${format.name}(${value})`).toBe('—')
      }
    }
  })

  it('renders a quote with no previous close (null day change) as dashes, coloured flat', () => {
    expect(formatSignedPrice(null)).toBe('—')
    expect(formatPercent(null)).toBe('—')
    expect(trend(null)).toBe('flat')
    expect(trend(undefined)).toBe('flat')
    expect(trend(Number.NaN)).toBe('flat')
  })
})

describe('zero and negative values', () => {
  it('never signs a zero, whichever side it was rounded from', () => {
    for (const value of [0, -0, 0.004, -0.004]) {
      expect(formatMoney(value)).toBe('$0.00')
      expect(formatSignedMoney(value)).toBe('$0.00')
      expect(formatSignedPrice(value)).toBe('0.00')
      expect(formatPercent(value)).toBe('0.00%')
    }
  })

  it('signs the smallest values that do display', () => {
    expect(formatSignedMoney(0.01)).toBe('+$0.01')
    expect(formatSignedMoney(-0.01)).toBe('−$0.01')
    expect(formatSignedPrice(-0.006)).toBe('−0.01')
    expect(formatPercent(-0.006)).toBe('−0.01%')
    expect(trend(0.006)).toBe('up')
    expect(trend(-0.006)).toBe('down')
  })

  it('groups large negative money and uses a real minus sign, not a hyphen', () => {
    expect(formatMoney(-1234567.891)).toBe('−$1,234,567.89')
    expect(formatSignedMoney(-1000)).toBe('−$1,000.00')
    expect(formatMoney(-5)).not.toContain('-')
    expect(formatMoney(1e9)).toBe('$1,000,000,000.00')
  })

  it('formats large and whole percentages', () => {
    expect(formatPercent(100)).toBe('+100.00%')
    expect(formatPercent(-78.954)).toBe('−78.95%')
    expect(formatPercent(1234.5)).toBe('+1234.50%')
  })

  it('formats prices and quantities at the edges', () => {
    expect(formatPrice(0)).toBe('0.00')
    expect(formatPrice(1234.567)).toBe('1,234.57')
    expect(formatQuantity(0)).toBe('0')
    expect(formatQuantity(0.0001)).toBe('0.0001')
    expect(formatQuantity(2.5)).toBe('2.5')
    expect(formatQuantity(1234.5678)).toBe('1,234.5678')
    expect(formatQuantity(0.00001)).toBe('0')
  })

  it('lets trend use a finer precision than cents', () => {
    expect(trend(0.004, 4)).toBe('up')
    expect(trend(-0.00004, 4)).toBe('flat')
  })
})

describe('time', () => {
  // Built from local-time parts so the expectations hold in any time zone.
  const at = (y: number, m: number, d: number, h: number, min: number, s = 0) =>
    new Date(y, m - 1, d, h, min, s).getTime() / 1000

  it('formats a 24-hour local time, with or without seconds', () => {
    expect(formatTime(at(2026, 10, 3, 14, 3, 27))).toBe('14:03:27')
    expect(formatTime(at(2026, 10, 3, 14, 3, 27), false)).toBe('14:03')
    expect(formatTime(at(2026, 10, 3, 9, 5, 7))).toBe('09:05:07')
  })

  it('shows midnight as 00, not 24', () => {
    expect(formatTime(at(2026, 10, 3, 0, 0, 5))).toBe('00:00:05')
  })

  it('shows only the time for a timestamp from today', () => {
    const now = new Date(2026, 9, 3, 18, 0, 0).getTime()
    expect(formatDateTime(at(2026, 10, 3, 14, 3, 27), now)).toBe('14:03:27')
    expect(formatDateTime(at(2026, 10, 3, 0, 0, 0), now)).toBe('00:00:00')
  })

  it('adds the date for older timestamps', () => {
    const now = new Date(2026, 9, 4, 0, 0, 1).getTime()
    expect(formatDateTime(at(2026, 10, 3, 23, 59, 59), now)).toBe('3 Oct 23:59')
    expect(formatDateTime(at(2025, 12, 25, 9, 5), now)).toBe('25 Dec 09:05')
  })
})
