import { describe, expect, it } from 'vitest'
import { parseChatEvent, readChatStream } from '~/lib/chatStream'
import type { ChatStreamEvent } from '~/types/api'
import { controlledStream } from './helpers'

describe('parseChatEvent', () => {
  it('parses each kind', () => {
    expect(parseChatEvent('{"type":"text","delta":"a"}')).toEqual({ type: 'text', delta: 'a' })
    expect(parseChatEvent('{"type":"tool","name":"get_portfolio"}')).toEqual({ type: 'tool', name: 'get_portfolio' })
    expect(parseChatEvent('{"type":"error","detail":"x"}')).toEqual({ type: 'error', detail: 'x' })
    expect(parseChatEvent('{"type":"done"}')).toEqual({ type: 'done' })
    expect(
      parseChatEvent('{"type":"trade_proposal","proposal":{"ticker":"aapl","side":"buy","quantity":2,"rationale":"why","estimated_price":190,"estimated_total":380}}'),
    ).toEqual({
      type: 'trade_proposal',
      proposal: { ticker: 'AAPL', side: 'buy', quantity: 2, rationale: 'why', estimated_price: 190, estimated_total: 380 },
    })
  })

  it.each([
    'not json',
    '[]',
    '{"type":"text"}',
    '{"type":"nope"}',
    '{"type":"trade_proposal","proposal":{"ticker":"AAPL","side":"hold","quantity":1}}',
    '{"type":"trade_proposal","proposal":{"ticker":"AAPL","side":"buy","quantity":-1}}',
    '{"type":"trade_proposal"}',
  ])('rejects %s', (data) => {
    expect(parseChatEvent(data)).toBeNull()
  })
})

describe('readChatStream', () => {
  it('delivers events in order across odd chunking and stops at done', async () => {
    const s = controlledStream()
    const seen: ChatStreamEvent[] = []
    const result = readChatStream(s.body, (e) => seen.push(e))
    s.push('data: {"type":"te')
    s.push('xt","delta":"é"}\r\n\r\ndata: nonsense\n\n: ping\n\ndata: {"type":"done"}\n\n')
    s.push('data: {"type":"text","delta":"after done"}\n\n')
    expect(await result).toBe(true)
    expect(seen).toEqual([{ type: 'text', delta: 'é' }, { type: 'done' }])
  })

  it('decodes a multi-byte character split between chunks', async () => {
    const s = controlledStream()
    const seen: ChatStreamEvent[] = []
    const result = readChatStream(s.body, (e) => seen.push(e))
    const bytes = new TextEncoder().encode('data: {"type":"text","delta":"€"}\n\ndata: {"type":"done"}\n\n')
    const cut = bytes.indexOf(0xe2) + 1 // inside the three-byte euro sign
    s.raw(bytes.slice(0, cut))
    s.raw(bytes.slice(cut))
    await result
    expect(seen[0]).toEqual({ type: 'text', delta: '€' })
  })

  it('resolves false when the stream ends without done', async () => {
    const s = controlledStream()
    const result = readChatStream(s.body, () => {})
    s.event({ type: 'text', delta: 'x' })
    s.end()
    expect(await result).toBe(false)
  })

  it('rejects when the read fails', async () => {
    const s = controlledStream()
    const result = readChatStream(s.body, () => {})
    s.fail(new TypeError('network error'))
    await expect(result).rejects.toThrow('network error')
  })
})
