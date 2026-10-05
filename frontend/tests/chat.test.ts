import { describe, expect, it } from 'vitest'
import { applyEvent, isNearBottom, patchProposal, pendingReply, settle, toHistory, userMessage, type AssistantMessage } from '~/lib/chat'
import type { TradeProposal } from '~/types/api'

const proposal: TradeProposal = { ticker: 'AAPL', side: 'buy', quantity: 2, rationale: 'r', estimated_price: 190, estimated_total: 380 }

describe('applyEvent', () => {
  it('appends text deltas and clears the activity line', () => {
    let m = applyEvent(pendingReply('a'), { type: 'tool', name: 'get_portfolio' }, 'p')
    expect(m.activity).toBe('Checking portfolio…')
    m = applyEvent(m, { type: 'text', delta: 'Hel' }, 'p')
    m = applyEvent(m, { type: 'text', delta: 'lo' }, 'p')
    expect(m).toMatchObject({ content: 'Hello', activity: '', streaming: true })
  })

  it('uses a generic label for an unknown tool', () => {
    expect(applyEvent(pendingReply('a'), { type: 'tool', name: 'mystery' }, 'p').activity).toBe('Looking that up…')
  })

  it('adds proposals as pending cards, in order', () => {
    let m = applyEvent(pendingReply('a'), { type: 'trade_proposal', proposal }, 'p1')
    m = applyEvent(m, { type: 'trade_proposal', proposal: { ...proposal, ticker: 'MSFT' } }, 'p2')
    expect(m.proposals.map((p) => [p.id, p.status, p.proposal.ticker])).toEqual([
      ['p1', 'pending', 'AAPL'],
      ['p2', 'pending', 'MSFT'],
    ])
  })

  it('records an error but keeps the text; done ends the stream', () => {
    let m = applyEvent(pendingReply('a'), { type: 'text', delta: 'part' }, 'p')
    m = applyEvent(m, { type: 'error', detail: 'overloaded' }, 'p')
    m = applyEvent(m, { type: 'done' }, 'p')
    expect(m).toMatchObject({ content: 'part', error: 'overloaded', streaming: false })
  })

  it('does not mutate the message it is given', () => {
    const before = pendingReply('a')
    applyEvent(before, { type: 'text', delta: 'x' }, 'p')
    expect(before.content).toBe('')
  })

  it('settle ends streaming and activity', () => {
    const m = settle({ ...pendingReply('a'), activity: 'x' }, { stopped: true })
    expect(m).toMatchObject({ streaming: false, activity: '', stopped: true })
  })
})

describe('patchProposal', () => {
  it('changes only the named card', () => {
    const reply: AssistantMessage = applyEvent(applyEvent(pendingReply('a'), { type: 'trade_proposal', proposal }, 'p1'), { type: 'trade_proposal', proposal }, 'p2')
    const out = patchProposal([userMessage('u', 'hi'), reply], 'a', 'p2', { status: 'dismissed' })
    const cards = (out[1] as AssistantMessage).proposals
    expect(cards.map((c) => c.status)).toEqual(['pending', 'dismissed'])
  })
})

describe('toHistory', () => {
  it('sends text turns only, skipping empty replies and merging same-role neighbours', () => {
    const reply = (id: string, content: string): AssistantMessage => ({ ...pendingReply(id), content, streaming: false })
    const withCard = applyEvent(reply('a2', ''), { type: 'trade_proposal', proposal }, 'p')
    const turns = toHistory([
      userMessage('u1', 'one'),
      reply('a1', 'answer'),
      userMessage('u2', 'two'),
      withCard, // proposal only: no text
      userMessage('u3', ' three '),
    ])
    expect(turns).toEqual([
      { role: 'user', content: 'one' },
      { role: 'assistant', content: 'answer' },
      { role: 'user', content: 'two\n\nthree' },
    ])
  })
})

describe('isNearBottom', () => {
  it('is true within the slack and false once scrolled up', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 560, clientHeight: 400 })).toBe(true)
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 300, clientHeight: 400 })).toBe(false)
  })
})
