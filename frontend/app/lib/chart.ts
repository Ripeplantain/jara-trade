/** Geometry for the hand-rolled SVG line charts (main chart and sparklines). */
import type { PricePoint } from '~/types/api'

export interface Box {
  width: number
  height: number
  top?: number
  right?: number
  bottom?: number
  left?: number
}

export interface Scale {
  x: (timestamp: number) => number
  y: (price: number) => number
  minTime: number
  maxTime: number
  minPrice: number
  maxPrice: number
}

/** Linear scales that fit `points` inside the box, with a little headroom on the price axis. */
export function makeScale(points: readonly PricePoint[], box: Box, pad = 0.08): Scale | null {
  if (points.length === 0) return null
  const { width, height, top = 0, right = 0, bottom = 0, left = 0 } = box
  let minPrice = Infinity
  let maxPrice = -Infinity
  for (const p of points) {
    if (p.price < minPrice) minPrice = p.price
    if (p.price > maxPrice) maxPrice = p.price
  }
  // A flat series still needs a visible band around it.
  const spread = maxPrice - minPrice || Math.max(Math.abs(maxPrice) * 0.001, 0.01)
  minPrice -= spread * pad
  maxPrice += spread * pad

  const minTime = points[0]!.timestamp
  const maxTime = points[points.length - 1]!.timestamp
  const timeSpan = maxTime - minTime || 1
  const innerW = Math.max(width - left - right, 1)
  const innerH = Math.max(height - top - bottom, 1)

  return {
    x: (t) => left + ((t - minTime) / timeSpan) * innerW,
    y: (p) => top + (1 - (p - minPrice) / (maxPrice - minPrice)) * innerH,
    minTime,
    maxTime,
    minPrice,
    maxPrice,
  }
}

/** SVG path data for the line through `points`. */
export function linePath(points: readonly PricePoint[], scale: Scale): string {
  let d = ''
  for (let i = 0; i < points.length; i++) {
    const p = points[i]!
    d += `${i === 0 ? 'M' : 'L'}${scale.x(p.timestamp).toFixed(1)},${scale.y(p.price).toFixed(1)}`
  }
  return d
}

/** The line closed down to `baseline` (a y pixel), for the soft fill under it. */
export function areaPath(points: readonly PricePoint[], scale: Scale, baseline: number): string {
  if (points.length < 2) return ''
  const first = points[0]!
  const last = points[points.length - 1]!
  return `${linePath(points, scale)}L${scale.x(last.timestamp).toFixed(1)},${baseline.toFixed(1)}L${scale
    .x(first.timestamp)
    .toFixed(1)},${baseline.toFixed(1)}Z`
}

/** About `count` round-numbered tick values covering [min, max]. */
export function niceTicks(min: number, max: number, count = 4): number[] {
  const span = max - min
  if (!(span > 0) || count < 1) return [min]
  const raw = span / count
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const residual = raw / magnitude
  // Round the step up so there are never more than `count` + 1 ticks.
  const step = (residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10) * magnitude
  const ticks: number[] = []
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) {
    ticks.push(Number(v.toFixed(6)))
  }
  return ticks
}

/** Index of the point whose timestamp is closest to `timestamp` (points sorted by time). */
export function nearestIndex(points: readonly PricePoint[], timestamp: number): number {
  if (points.length === 0) return -1
  let lo = 0
  let hi = points.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (points[mid]!.timestamp < timestamp) lo = mid
    else hi = mid
  }
  return timestamp - points[lo]!.timestamp <= points[hi]!.timestamp - timestamp ? lo : hi
}

const TIME_STEPS = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]

/** Timestamps on round clock boundaries, at most `maxCount` of them, within [min, max]. */
export function timeTicks(min: number, max: number, maxCount = 6): number[] {
  const span = max - min
  if (!(span > 0) || maxCount < 1) return []
  const step = TIME_STEPS.find((s) => span / s <= maxCount) ?? TIME_STEPS[TIME_STEPS.length - 1]!
  const ticks: number[] = []
  for (let t = Math.ceil(min / step) * step; t <= max; t += step) ticks.push(t)
  return ticks
}
