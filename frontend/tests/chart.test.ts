import { describe, expect, it } from 'vitest'
import { linePath, makeScale, nearestIndex, niceTicks, timeTicks } from '~/lib/chart'

const points = [
  { timestamp: 0, price: 10 },
  { timestamp: 10, price: 20 },
]

describe('makeScale', () => {
  it('maps time left to right and price bottom to top inside the padding', () => {
    const scale = makeScale(points, { width: 120, height: 60, left: 10, right: 10, top: 5, bottom: 5 }, 0)!
    expect(scale.x(0)).toBe(10)
    expect(scale.x(10)).toBe(110)
    expect(scale.y(10)).toBe(55)
    expect(scale.y(20)).toBe(5)
    expect(linePath(points, scale)).toBe('M10.0,55.0L110.0,5.0')
  })

  it('gives a flat series a visible band instead of dividing by zero', () => {
    const scale = makeScale([{ timestamp: 0, price: 5 }, { timestamp: 1, price: 5 }], { width: 10, height: 10 })!
    expect(Number.isFinite(scale.y(5))).toBe(true)
    expect(scale.maxPrice).toBeGreaterThan(scale.minPrice)
  })

  it('returns null for an empty series', () => {
    expect(makeScale([], { width: 10, height: 10 })).toBeNull()
  })
})

describe('ticks', () => {
  it('picks round price ticks inside the range', () => {
    expect(niceTicks(189.3, 191.2, 4)).toEqual([189.5, 190, 190.5, 191])
  })

  it('picks clock-aligned time ticks', () => {
    expect(timeTicks(30, 3630, 6)).toEqual([600, 1200, 1800, 2400, 3000, 3600])
  })
})

describe('nearestIndex', () => {
  it('finds the closest point by time', () => {
    const series = [0, 5, 10, 15].map((timestamp) => ({ timestamp, price: 1 }))
    expect(nearestIndex(series, -3)).toBe(0)
    expect(nearestIndex(series, 6)).toBe(1)
    expect(nearestIndex(series, 8)).toBe(2)
    expect(nearestIndex(series, 99)).toBe(3)
    expect(nearestIndex([], 1)).toBe(-1)
  })
})
