import { describe, expect, it } from 'vitest'
import { detectFlashes, rememberPrices } from '~/lib/flash'
import type { Direction, PriceMap } from '~/types/api'

function message(price: number, timestamp: number, direction: Direction = 'flat'): PriceMap {
  return {
    AAPL: {
      ticker: 'AAPL',
      price,
      previous_price: price,
      timestamp,
      direction,
      change: 0,
      prev_close: null,
      day_change: null,
      day_change_percent: null,
    },
  }
}

describe('detectFlashes', () => {
  it('does not flash a ticker seen for the first time', () => {
    expect(detectFlashes({}, message(190, 1, 'up'))).toEqual({})
  })

  it('flashes up or down when the price moved since the last message', () => {
    const seen = rememberPrices(message(190, 1))
    expect(detectFlashes(seen, message(190.5, 2))).toEqual({ AAPL: 'up' })
    expect(detectFlashes(seen, message(189.5, 2))).toEqual({ AAPL: 'down' })
  })

  it('does not flash a repeated event that still says direction "up"', () => {
    const seen = rememberPrices(message(190.5, 2, 'up'))
    expect(detectFlashes(seen, message(190.5, 2, 'up'))).toEqual({})
  })

  it('does not flash when only the timestamp moved', () => {
    const seen = rememberPrices(message(190.5, 2, 'up'))
    expect(detectFlashes(seen, message(190.5, 3, 'up'))).toEqual({})
  })

  it('uses its own comparison rather than the event direction', () => {
    const seen = rememberPrices(message(190, 1))
    expect(detectFlashes(seen, message(189, 2, 'up'))).toEqual({ AAPL: 'down' })
  })

  it('ignores a quote older than the one already held', () => {
    const seen = rememberPrices(message(190, 5))
    expect(detectFlashes(seen, message(191, 4))).toEqual({})
  })
})

describe('rememberPrices', () => {
  it('keeps only the tickers in the latest message', () => {
    expect(rememberPrices(message(190, 1))).toEqual({ AAPL: { price: 190, timestamp: 1 } })
  })
})

function update(ticker: string, price: number, timestamp: number, direction: Direction = 'flat') {
  return { ...message(price, timestamp, direction).AAPL!, ticker }
}

/** Feed messages through detect + remember the way usePrices does. */
function run(messages: PriceMap[]) {
  let seen = {}
  return messages.map((next) => {
    const flashes = detectFlashes(seen, next)
    seen = rememberPrices(next)
    return flashes
  })
}

describe('flash sequences', () => {
  it('handles empty inputs', () => {
    expect(detectFlashes({}, {})).toEqual({})
    expect(detectFlashes(rememberPrices(message(190, 1)), {})).toEqual({})
    expect(rememberPrices({})).toEqual({})
  })

  it('flashes once for a move, however often the same event is repeated', () => {
    const moved = message(191, 2, 'up')
    expect(run([message(190, 1), moved, moved, moved, message(191, 3, 'up')])).toEqual([
      {}, { AAPL: 'up' }, {}, {}, {},
    ])
  })

  it('flashes again on each real move, including straight back to the old price', () => {
    expect(run([message(190, 1), message(191, 2), message(190, 3), message(190, 4), message(190.01, 5)])).toEqual([
      {}, { AAPL: 'up' }, { AAPL: 'down' }, {}, { AAPL: 'up' },
    ])
  })

  it('judges each ticker in a message on its own', () => {
    const before = { AAPL: update('AAPL', 190, 1), MSFT: update('MSFT', 420, 1), TSLA: update('TSLA', 250, 1) }
    const after = {
      AAPL: update('AAPL', 191, 2),
      MSFT: update('MSFT', 419.99, 2),
      TSLA: update('TSLA', 250, 2),
      NVDA: update('NVDA', 800, 2, 'up'),
    }
    expect(detectFlashes(rememberPrices(before), after)).toEqual({ AAPL: 'up', MSFT: 'down' })
  })

  it('does not flash a ticker that left the stream and came back at another price', () => {
    const both = { AAPL: update('AAPL', 190, 1), MSFT: update('MSFT', 420, 1) }
    const onlyApple = { AAPL: update('AAPL', 190, 2) }
    const back = { AAPL: update('AAPL', 190, 3), MSFT: update('MSFT', 500, 3) }
    expect(run([both, onlyApple, back])).toEqual([{}, {}, {}])
  })

  it('flashes a new price that carries the same timestamp', () => {
    expect(detectFlashes(rememberPrices(message(190, 5)), message(189, 5))).toEqual({ AAPL: 'down' })
  })

  it('remembers every ticker of the latest message and nothing else', () => {
    const seen = rememberPrices({ AAPL: update('AAPL', 190, 7), MSFT: update('MSFT', 420, 8) })
    expect(seen).toEqual({ AAPL: { price: 190, timestamp: 7 }, MSFT: { price: 420, timestamp: 8 } })
  })
})
