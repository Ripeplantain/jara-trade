/** Keeping a ticker's price series up to date from the stream. */
import type { PricePoint } from '~/types/api'

export interface AppendOptions {
  /** Seconds between kept points. Quotes arriving faster than this move the last point instead. */
  minInterval?: number
  /** Oldest points are dropped beyond this length. */
  maxPoints?: number
}

/** The backend keeps one hour at five-second spacing; the client mirrors that. */
export const HISTORY_INTERVAL = 5
export const HISTORY_MAX_POINTS = 720

/**
 * Return a new series with `point` applied. Never mutates the input.
 *
 * - A point that is not newer than the last one is ignored (repeated SSE message).
 * - The last point is the live tail and the one before it is the last *kept*
 *   sample. While the tail is closer than `minInterval` to that sample it is not
 *   a sample yet, so the new point replaces it (the chart's latest point keeps
 *   moving).
 * - Once the tail is at least `minInterval` past the last kept sample it is
 *   promoted by appending the new point after it, so kept samples are never
 *   closer together than `minInterval`. The series is trimmed to `maxPoints`.
 */
export function appendPoint(
  points: readonly PricePoint[],
  point: PricePoint,
  { minInterval = HISTORY_INTERVAL, maxPoints = HISTORY_MAX_POINTS }: AppendOptions = {},
): PricePoint[] {
  if (!Number.isFinite(point.price) || !Number.isFinite(point.timestamp)) return points as PricePoint[]
  const last = points[points.length - 1]
  if (!last) return [point]
  if (point.timestamp <= last.timestamp) return points as PricePoint[]

  const anchor = points[points.length - 2]
  if (anchor && last.timestamp - anchor.timestamp < minInterval) {
    return [...points.slice(0, -1), point]
  }
  const next = [...points, point]
  return next.length > maxPoints ? next.slice(next.length - maxPoints) : next
}

/**
 * Combine a fetched history with points that streamed in while it was loading.
 * History wins for the past; streamed points newer than its end are appended.
 */
export function mergeHistory(
  fetched: readonly PricePoint[],
  streamed: readonly PricePoint[],
  options: AppendOptions = {},
): PricePoint[] {
  let merged: PricePoint[] = [...fetched].sort((a, b) => a.timestamp - b.timestamp)
  for (const point of streamed) merged = appendPoint(merged, point, options)
  const max = options.maxPoints ?? HISTORY_MAX_POINTS
  return merged.length > max ? merged.slice(merged.length - max) : merged
}
