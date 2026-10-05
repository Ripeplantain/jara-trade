/** Reading the assistant's reply: bytes -> SSE frames -> validated events. */
import { createSseParser } from '~/lib/sse'
import type { ChatStreamEvent, TradeProposal } from '~/types/api'

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const money = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)

function parseProposal(raw: unknown): TradeProposal | null {
  if (!isRecord(raw)) return null
  const { ticker, side, quantity, rationale } = raw
  if (typeof ticker !== 'string' || !ticker.trim()) return null
  if (side !== 'buy' && side !== 'sell') return null
  if (typeof quantity !== 'number' || !Number.isFinite(quantity) || quantity <= 0) return null
  return {
    ticker: ticker.trim().toUpperCase(),
    side,
    quantity,
    rationale: typeof rationale === 'string' ? rationale : '',
    estimated_price: money(raw.estimated_price),
    estimated_total: money(raw.estimated_total),
  }
}

/** One event's JSON, or null when it is malformed or of a kind this client does not know. */
export function parseChatEvent(data: string): ChatStreamEvent | null {
  let raw: unknown
  try {
    raw = JSON.parse(data)
  } catch {
    return null
  }
  if (!isRecord(raw)) return null
  switch (raw.type) {
    case 'text':
      return typeof raw.delta === 'string' ? { type: 'text', delta: raw.delta } : null
    case 'tool':
      return { type: 'tool', name: typeof raw.name === 'string' ? raw.name : '' }
    case 'trade_proposal': {
      const proposal = parseProposal(raw.proposal)
      return proposal ? { type: 'trade_proposal', proposal } : null
    }
    case 'error':
      return { type: 'error', detail: typeof raw.detail === 'string' && raw.detail ? raw.detail : 'The assistant hit an error' }
    case 'done':
      return { type: 'done' }
    default:
      return null
  }
}

/**
 * Read a chat response body to the end, calling `onEvent` for each event.
 * Resolves true if the `done` event arrived (it is delivered, then reading
 * stops), false if the stream ended without one. Rejects if the read fails or
 * is aborted.
 */
export async function readChatStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<boolean> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let finished = false

  const parser = createSseParser((data) => {
    if (finished) return
    const event = parseChatEvent(data)
    if (!event) return
    if (event.type === 'done') finished = true
    onEvent(event)
  })

  try {
    while (!finished) {
      const { done, value } = await reader.read()
      if (done) break
      parser.feed(decoder.decode(value, { stream: true }))
    }
    if (!finished) {
      parser.feed(decoder.decode())
      parser.flush()
    }
  } finally {
    if (finished) void reader.cancel().catch(() => {})
    else reader.releaseLock()
  }
  return finished
}
