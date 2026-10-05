/** Typed client for the FastAPI backend. One function per endpoint. */
import type {
  AssistantStatus,
  ChatTurn,
  HistoryResponse,
  MarketStatus,
  Portfolio,
  PriceMap,
  TradeRequest,
  TradeResponse,
  TradesResponse,
  WatchlistResponse,
} from '~/types/api'

/** A failed request. `status` is 0 when the server could not be reached at all. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** FastAPI errors are `{"detail": "message"}`; validation errors are `{"detail": [{msg, ...}]}`. */
export function readDetail(body: unknown): string | null {
  if (!body || typeof body !== 'object' || !('detail' in body)) return null
  const detail = (body as { detail: unknown }).detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === 'object' && 'msg' in item ? String(item.msg) : null))
      .filter((m): m is string => !!m)
    if (messages.length) return messages.join('; ')
  }
  return null
}

function failureMessage(status: number, detail: string | null): string {
  // FastAPI answers an unknown route with exactly {"detail": "Not Found"}.
  if (status === 404 && (detail === null || detail === 'Not Found')) return 'This service is not available'
  if (detail) return detail
  if (status >= 502 && status <= 504) return 'The backend is not reachable'
  return `Request failed (${status})`
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong'
}

export interface Api {
  streamUrl: string
  prices: () => Promise<PriceMap>
  history: (ticker: string) => Promise<HistoryResponse>
  status: () => Promise<MarketStatus>
  watchlist: () => Promise<WatchlistResponse>
  addToWatchlist: (ticker: string) => Promise<WatchlistResponse>
  removeFromWatchlist: (ticker: string) => Promise<WatchlistResponse>
  portfolio: () => Promise<Portfolio>
  trades: (limit?: number) => Promise<TradesResponse>
  placeTrade: (order: TradeRequest) => Promise<TradeResponse>
  resetPortfolio: () => Promise<Portfolio>
  assistantStatus: () => Promise<AssistantStatus>
  /**
   * Start a reply and return its SSE body, unread. Throws an `ApiError` (the
   * backend's `detail`) when the request is refused. An aborted `signal` rejects
   * with the browser's own AbortError.
   */
  assistantChat: (messages: ChatTurn[], signal?: AbortSignal) => Promise<ReadableStream<Uint8Array>>
}

export function createApi(base = '', fetcher: typeof fetch = (...args) => fetch(...args)): Api {
  const root = base.replace(/\/+$/, '')

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response
    try {
      response = await fetcher(`${root}${path}`, {
        ...init,
        headers: {
          Accept: 'application/json',
          ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        },
      })
    } catch {
      throw new ApiError(0, 'Cannot reach the server')
    }

    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      // Not JSON (an HTML error page from a proxy, or an empty body).
    }

    if (!response.ok) {
      throw new ApiError(response.status, failureMessage(response.status, readDetail(body)))
    }
    return body as T
  }

  const json = (method: string, body?: unknown): RequestInit => ({
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const seg = encodeURIComponent

  return {
    streamUrl: `${root}/api/stream/prices`,
    prices: () => request('/api/market/prices'),
    history: (ticker) => request(`/api/market/history/${seg(ticker)}`),
    status: () => request('/api/market/status'),
    watchlist: () => request('/api/watchlist'),
    addToWatchlist: (ticker) => request('/api/watchlist', json('POST', { ticker })),
    removeFromWatchlist: (ticker) => request(`/api/watchlist/${seg(ticker)}`, json('DELETE')),
    portfolio: () => request('/api/portfolio'),
    trades: (limit = 50) => request(`/api/trades?limit=${limit}`),
    placeTrade: (order) => request('/api/trades', json('POST', order)),
    resetPortfolio: () => request('/api/portfolio/reset', json('POST')),
    assistantStatus: () => request('/api/assistant/status'),
    assistantChat: async (messages, signal) => {
      let response: Response
      try {
        response = await fetcher(`${root}/api/assistant/chat`, {
          method: 'POST',
          headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
          body: JSON.stringify({ messages }),
          signal,
        })
      } catch (e) {
        if (signal?.aborted) throw e
        throw new ApiError(0, 'Cannot reach the server')
      }
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new ApiError(response.status, failureMessage(response.status, readDetail(body)))
      }
      if (!response.body) throw new ApiError(0, 'The reply could not be streamed')
      return response.body
    },
  }
}
