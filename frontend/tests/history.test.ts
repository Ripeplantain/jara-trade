import { describe, expect, it } from 'vitest'
import { appendPoint, HISTORY_INTERVAL, HISTORY_MAX_POINTS, mergeHistory } from '~/lib/history'
import type { PricePoint } from '~/types/api'

const pt = (timestamp: number, price: number): PricePoint => ({ timestamp, price })

describe('appendPoint', () => {
  it('starts a series', () => {
    expect(appendPoint([], pt(100, 1))).toEqual([pt(100, 1)])
  })

  it('ignores a point that is not newer than the last one', () => {
    const series = [pt(100, 1), pt(105, 2)]
    expect(appendPoint(series, pt(105, 3))).toBe(series)
    expect(appendPoint(series, pt(101, 3))).toBe(series)
  })

  it('appends once the interval since the last kept sample has passed', () => {
    const series = [pt(100, 1), pt(105, 2)]
    expect(appendPoint(series, pt(110, 3))).toEqual([pt(100, 1), pt(105, 2), pt(110, 3)])
  })

  it('moves the live tail for quotes that arrive faster than the interval', () => {
    let series = [pt(100, 1), pt(105, 2)]
    series = appendPoint(series, pt(105.5, 2.1)) // new tail after the 105 sample
    series = appendPoint(series, pt(106, 2.2)) // replaces the tail
    series = appendPoint(series, pt(106.5, 2.3))
    expect(series).toEqual([pt(100, 1), pt(105, 2), pt(106.5, 2.3)])
  })

  it('never mutates its input', () => {
    const series = [pt(100, 1), pt(105, 2)]
    appendPoint(series, pt(106, 9))
    appendPoint(series, pt(120, 9))
    expect(series).toEqual([pt(100, 1), pt(105, 2)])
  })

  it('drops the oldest points beyond maxPoints', () => {
    const series = [pt(0, 1), pt(5, 2), pt(10, 3)]
    expect(appendPoint(series, pt(15, 4), { maxPoints: 3 })).toEqual([pt(5, 2), pt(10, 3), pt(15, 4)])
  })

  it('skips non-finite values', () => {
    const series = [pt(100, 1)]
    expect(appendPoint(series, pt(105, Number.NaN))).toBe(series)
  })
})

describe('mergeHistory', () => {
  it('adds only the streamed points newer than the fetched history', () => {
    const fetched = [pt(100, 1), pt(105, 2)]
    const streamed = [pt(104, 9), pt(105, 9), pt(111, 3)]
    expect(mergeHistory(fetched, streamed)).toEqual([pt(100, 1), pt(105, 2), pt(111, 3)])
  })

  it('falls back to the streamed points when the history is empty', () => {
    expect(mergeHistory([], [pt(1, 1), pt(7, 2)])).toEqual([pt(1, 1), pt(7, 2)])
  })
})

/** Push a stream of quotes through appendPoint, as usePrices does. */
function stream(start: PricePoint[], quotes: PricePoint[], options = {}) {
  return quotes.reduce((series, quote) => appendPoint(series, quote, options), start)
}

describe('appendPoint de-duplication', () => {
  it('returns the same array for a repeated SSE message, so nothing re-renders', () => {
    const series = stream([], [pt(100, 1), pt(106, 2)])
    expect(appendPoint(series, pt(106, 2))).toBe(series)
    expect(appendPoint(series, pt(106, 99))).toBe(series) // same timestamp, different price
    expect(stream(series, [pt(106, 2), pt(106, 2), pt(106, 2)])).toBe(series)
  })

  it('ignores a non-finite timestamp as well as a non-finite price', () => {
    const series = [pt(100, 1)]
    expect(appendPoint(series, pt(Number.NaN, 1))).toBe(series)
    expect(appendPoint(series, pt(Infinity, 1))).toBe(series)
    expect(appendPoint(series, pt(105, Infinity))).toBe(series)
    expect(appendPoint([], pt(Number.NaN, 1))).toEqual([])
  })

  it('always keeps the newest quote as the last point', () => {
    const quotes = Array.from({ length: 40 }, (_, i) => pt(100 + i * 0.5, i))
    let series: PricePoint[] = []
    for (const quote of quotes) {
      series = appendPoint(series, quote)
      expect(series[series.length - 1]).toEqual(quote)
    }
    const times = series.map((p) => p.timestamp)
    expect(times).toEqual([...times].sort((a, b) => a - b))
    expect(new Set(times).size).toBe(times.length)
  })
})

describe('appendPoint windowing', () => {
  it('mirrors the backend defaults: five seconds, one hour', () => {
    expect(HISTORY_INTERVAL).toBe(5)
    expect(HISTORY_MAX_POINTS).toBe(720)
  })

  it('caps a long stream at maxPoints, keeping the newest', () => {
    const quotes = Array.from({ length: 1000 }, (_, i) => pt(i * 5, i))
    const series = stream([], quotes)
    expect(series).toHaveLength(720)
    expect(series[0]).toEqual(pt(280 * 5, 280))
    expect(series[719]).toEqual(pt(999 * 5, 999))
  })

  it('does not trim when the tail is only being replaced', () => {
    const full = [pt(0, 1), pt(5, 2), pt(6, 3)]
    expect(appendPoint(full, pt(7, 9), { maxPoints: 3 })).toEqual([pt(0, 1), pt(5, 2), pt(7, 9)])
  })

  it('honours a custom interval', () => {
    const series = stream([pt(0, 1)], [pt(10, 2), pt(20, 3), pt(40, 4), pt(65, 5)], { minInterval: 60 })
    expect(series).toEqual([pt(0, 1), pt(65, 5)])
  })

  it('keeps samples at least minInterval apart under a fast stream', () => {
    const quotes = Array.from({ length: 60 }, (_, i) => pt(101 + i, i))
    const kept = stream([pt(95, 0), pt(100, 0)], quotes).slice(0, -1) // all but the live tail
    const gaps = kept.slice(1).map((p, i) => p.timestamp - kept[i]!.timestamp)
    expect(Math.min(...gaps)).toBeGreaterThanOrEqual(5)
  })

  it('keeps samples at least minInterval apart at the simulator tick of half a second', () => {
    const quotes = Array.from({ length: 400 }, (_, i) => pt(100 + i * 0.5, i))
    const series = stream([pt(95, 0), pt(100, 0)], quotes)
    const kept = series.slice(0, -1)
    const gaps = kept.slice(1).map((p, i) => p.timestamp - kept[i]!.timestamp)
    expect(Math.min(...gaps)).toBeGreaterThanOrEqual(5)
    expect(series[series.length - 1]).toEqual(quotes[quotes.length - 1]) // live tail still current
  })
})

describe('mergeHistory edge cases', () => {
  it('returns an empty series when there is nothing at all', () => {
    expect(mergeHistory([], [])).toEqual([])
  })

  it('keeps the fetched history when nothing streamed', () => {
    const fetched = [pt(100, 1), pt(105, 2)]
    expect(mergeHistory(fetched, [])).toEqual(fetched)
  })

  it('sorts an out-of-order history without mutating it', () => {
    const fetched = [pt(105, 2), pt(100, 1), pt(110, 3)]
    expect(mergeHistory(fetched, [pt(120, 4)])).toEqual([pt(100, 1), pt(105, 2), pt(110, 3), pt(120, 4)])
    expect(fetched).toEqual([pt(105, 2), pt(100, 1), pt(110, 3)])
  })

  it('drops streamed duplicates and anything the history already covers', () => {
    const fetched = [pt(100, 1), pt(105, 2)]
    const streamed = [pt(99, 7), pt(100, 7), pt(105, 7), pt(111, 3), pt(111, 3), pt(117, 4)]
    expect(mergeHistory(fetched, streamed)).toEqual([pt(100, 1), pt(105, 2), pt(111, 3), pt(117, 4)])
  })

  it('trims to maxPoints even when the fetched history alone is longer', () => {
    const fetched = Array.from({ length: 10 }, (_, i) => pt(i * 5, i))
    expect(mergeHistory(fetched, [], { maxPoints: 4 })).toEqual(fetched.slice(6))
    expect(mergeHistory(fetched, [pt(100, 99)], { maxPoints: 4 })).toEqual([...fetched.slice(7), pt(100, 99)])
  })
})
