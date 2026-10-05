/**
 * The assistant conversation: status, messages and the in-flight reply. Kept in
 * memory only (module-level refs, like the other composables), so it survives
 * the panel remounting but not a page reload.
 */
import { ApiError, errorMessage } from '~/lib/api'
import {
  applyEvent,
  patchProposal,
  pendingReply,
  settle,
  toHistory,
  userMessage,
  type AssistantMessage,
  type ChatItem,
} from '~/lib/chat'
import { readChatStream } from '~/lib/chatStream'

export type AssistantState = 'loading' | 'ready' | 'unavailable' | 'error'

const NOT_CONFIGURED = 'AI assistant is not configured. Set OPENROUTER_API_KEY and restart the backend.'
const RETRY_MS = 10_000

const items = ref<ChatItem[]>([])
const state = ref<AssistantState>('loading')
/** The backend's explanation when the state is `unavailable`. */
const notice = ref('')
const model = ref<string | null>(null)
const streaming = ref(false)

let controller: AbortController | null = null
let retryTimer: ReturnType<typeof setTimeout> | null = null
let seq = 0
const nextId = (prefix: string) => `${prefix}-${++seq}`

function updateReply(id: string, change: (message: AssistantMessage) => AssistantMessage) {
  items.value = items.value.map((item) => (item.role === 'assistant' && item.id === id ? change(item) : item))
}

export function useAssistant() {
  const { placeTrade } = usePortfolio()

  /** Ask whether the assistant is configured. While the endpoint is missing or down, ask again every few seconds. */
  async function loadStatus() {
    if (retryTimer) clearTimeout(retryTimer)
    retryTimer = null
    try {
      const status = await useApi().assistantStatus()
      model.value = status.model
      if (status.available) {
        state.value = 'ready'
        notice.value = ''
      } else {
        state.value = 'unavailable'
        notice.value = NOT_CONFIGURED
      }
    } catch {
      if (state.value === 'ready') return // a blip after we were up: keep going
      state.value = 'error'
      retryTimer = setTimeout(loadStatus, RETRY_MS)
    }
  }

  async function send(text: string) {
    const content = text.trim()
    if (!content || streaming.value || state.value !== 'ready') return

    const user = userMessage(nextId('u'), content)
    const reply = pendingReply(nextId('a'))
    const history = toHistory([...items.value, user])
    items.value = [...items.value, user, reply]
    streaming.value = true

    const mine = new AbortController()
    controller = mine
    try {
      const body = await useApi().assistantChat(history, mine.signal)
      const sawDone = await readChatStream(body, (event) => {
        updateReply(reply.id, (m) => applyEvent(m, event, nextId('p')))
      })
      updateReply(reply.id, (m) =>
        settle(m, sawDone || m.error ? {} : { error: 'The connection dropped before the reply finished.' }),
      )
    } catch (e) {
      if (mine.signal.aborted) {
        updateReply(reply.id, (m) => settle(m, { stopped: true }))
      } else if (e instanceof ApiError) {
        if (e.status === 503) {
          state.value = 'unavailable'
          notice.value = e.message
        }
        updateReply(reply.id, (m) => settle(m, { error: e.message }))
      } else {
        updateReply(reply.id, (m) => settle(m, { error: 'The connection dropped before the reply finished.' }))
      }
    } finally {
      if (controller === mine) {
        controller = null
        streaming.value = false
      }
    }
  }

  /** Stop the reply in progress, keeping what has arrived. */
  function stop() {
    controller?.abort()
  }

  function clear() {
    const running = controller
    controller = null
    running?.abort()
    streaming.value = false
    items.value = []
  }

  /** Back to a fresh page load (used by tests). */
  function reset() {
    clear()
    if (retryTimer) clearTimeout(retryTimer)
    retryTimer = null
    state.value = 'loading'
    notice.value = ''
    model.value = null
  }

  /** Execute a proposal. Only a pending one can be confirmed, and only once. */
  async function confirmProposal(messageId: string, proposalId: string) {
    const message = items.value.find((i) => i.role === 'assistant' && i.id === messageId)
    const card = message?.role === 'assistant' ? message.proposals.find((p) => p.id === proposalId) : undefined
    if (!card || card.status !== 'pending') return

    items.value = patchProposal(items.value, messageId, proposalId, { status: 'confirming' })
    try {
      const { ticker, side, quantity } = card.proposal
      const trade = await placeTrade({ ticker, side, quantity })
      items.value = patchProposal(items.value, messageId, proposalId, { status: 'filled', trade })
    } catch (e) {
      items.value = patchProposal(items.value, messageId, proposalId, { status: 'failed', error: errorMessage(e) })
    }
  }

  function dismissProposal(messageId: string, proposalId: string) {
    const message = items.value.find((i) => i.role === 'assistant' && i.id === messageId)
    const card = message?.role === 'assistant' ? message.proposals.find((p) => p.id === proposalId) : undefined
    if (card?.status !== 'pending') return
    items.value = patchProposal(items.value, messageId, proposalId, { status: 'dismissed' })
  }

  return { items, state, notice, model, streaming, loadStatus, send, stop, clear, reset, confirmProposal, dismissProposal }
}
