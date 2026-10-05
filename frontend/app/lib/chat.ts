/** Conversation state as plain data: pure functions the composable applies to its list. */
import type { ChatStreamEvent, ChatTurn, Trade, TradeProposal } from '~/types/api'

export type ProposalStatus = 'pending' | 'confirming' | 'filled' | 'failed' | 'dismissed'

export interface ProposalItem {
  id: string
  proposal: TradeProposal
  status: ProposalStatus
  /** The fill, once the backend has executed the order. */
  trade: Trade | null
  /** The backend's reason, when the order was refused. */
  error: string
}

export interface UserMessage {
  id: string
  role: 'user'
  content: string
}

export interface AssistantMessage {
  id: string
  role: 'assistant'
  content: string
  /** What the model is doing right now ("Checking portfolio…"); empty when nothing. */
  activity: string
  proposals: ProposalItem[]
  /** Set when the reply failed part-way; the text received so far is kept. */
  error: string
  streaming: boolean
  /** The user pressed stop. */
  stopped: boolean
}

export type ChatItem = UserMessage | AssistantMessage

export function userMessage(id: string, content: string): UserMessage {
  return { id, role: 'user', content }
}

export function pendingReply(id: string): AssistantMessage {
  return { id, role: 'assistant', content: '', activity: '', proposals: [], error: '', streaming: true, stopped: false }
}

const ACTIVITY: Record<string, string> = {
  get_portfolio: 'Checking portfolio…',
  get_positions: 'Checking positions…',
  get_prices: 'Checking prices…',
  get_price: 'Checking prices…',
  get_quote: 'Checking prices…',
  get_watchlist: 'Checking watchlist…',
  get_trades: 'Checking trade history…',
  get_recent_trades: 'Checking trade history…',
  get_trade_history: 'Checking trade history…',
  get_price_history: 'Checking price history…',
  get_history: 'Checking price history…',
  get_market_status: 'Checking market status…',
}

export function activityLabel(tool: string): string {
  return ACTIVITY[tool] ?? 'Looking that up…'
}

/** Fold one stream event into the reply it belongs to. `proposalId` names a new proposal card. */
export function applyEvent(message: AssistantMessage, event: ChatStreamEvent, proposalId: string): AssistantMessage {
  switch (event.type) {
    case 'text':
      return { ...message, content: message.content + event.delta, activity: '' }
    case 'tool':
      return { ...message, activity: activityLabel(event.name) }
    case 'trade_proposal':
      return {
        ...message,
        activity: '',
        proposals: [
          ...message.proposals,
          { id: proposalId, proposal: event.proposal, status: 'pending', trade: null, error: '' },
        ],
      }
    case 'error':
      return { ...message, activity: '', error: event.detail }
    case 'done':
      return { ...message, activity: '', streaming: false }
  }
}

/** The reply is over, however it ended. */
export function settle(message: AssistantMessage, patch: Partial<Pick<AssistantMessage, 'error' | 'stopped'>> = {}): AssistantMessage {
  return { ...message, ...patch, activity: '', streaming: false }
}

export function patchProposal(
  items: readonly ChatItem[],
  messageId: string,
  proposalId: string,
  patch: Partial<Pick<ProposalItem, 'status' | 'trade' | 'error'>>,
): ChatItem[] {
  return items.map((item) =>
    item.role === 'assistant' && item.id === messageId
      ? { ...item, proposals: item.proposals.map((p) => (p.id === proposalId ? { ...p, ...patch } : p)) }
      : item,
  )
}

/**
 * The text history to send back. Only text turns count: proposals, activity and
 * errors are not part of it, and a reply that produced no text is dropped.
 * Consecutive turns by the same role are merged so the roles alternate.
 */
export function toHistory(items: readonly ChatItem[]): ChatTurn[] {
  const turns: ChatTurn[] = []
  for (const item of items) {
    const content = item.content.trim()
    if (!content) continue
    const prev = turns[turns.length - 1]
    if (prev && prev.role === item.role) prev.content += `\n\n${content}`
    else turns.push({ role: item.role, content })
  }
  return turns
}

/** True when the list is scrolled to (or within `slack` px of) its end, i.e. the reader is following along. */
export function isNearBottom(el: { scrollHeight: number; scrollTop: number; clientHeight: number }, slack = 48): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= slack
}
