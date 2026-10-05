import { describe, expect, it } from 'vitest'
import { ApiError, createApi, errorMessage, readDetail } from '~/lib/api'

function respond(status: number, body: unknown): typeof fetch {
  return async () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('readDetail', () => {
  it('reads a plain detail message', () => {
    expect(readDetail({ detail: 'Insufficient cash' })).toBe('Insufficient cash')
  })

  it('joins FastAPI validation errors', () => {
    expect(readDetail({ detail: [{ msg: 'Field required' }, { msg: 'Must be positive' }] })).toBe(
      'Field required; Must be positive',
    )
  })

  it('returns null when there is no detail', () => {
    expect(readDetail(null)).toBeNull()
    expect(readDetail({ error: true })).toBeNull()
  })
})

describe('createApi', () => {
  it('returns the parsed body and sends JSON for writes', async () => {
    let seen: { url: string; init?: RequestInit } | null = null
    const api = createApi('http://x/', async (url, init) => {
      seen = { url: String(url), init }
      return new Response(JSON.stringify({ tickers: ['AAPL'] }))
    })
    expect(await api.addToWatchlist('AAPL')).toEqual({ tickers: ['AAPL'] })
    expect(seen!.url).toBe('http://x/api/watchlist')
    expect(seen!.init?.method).toBe('POST')
    expect(seen!.init?.body).toBe('{"ticker":"AAPL"}')
  })

  it("throws the backend's detail with its status", async () => {
    const api = createApi('', respond(400, { detail: 'Insufficient cash: need $500.00, have $10.00' }))
    const error = await api.placeTrade({ ticker: 'AAPL', side: 'buy', quantity: 5 }).catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(400)
    expect(error.message).toBe('Insufficient cash: need $500.00, have $10.00')
  })

  it('explains a missing endpoint and an unreachable server', async () => {
    const missing = await createApi('', respond(404, { detail: 'Not Found' })).portfolio().catch((e) => e)
    expect(missing.message).toBe('This service is not available')

    const down = await createApi('', async () => {
      throw new TypeError('fetch failed')
    })
      .portfolio()
      .catch((e) => e)
    expect(down.status).toBe(0)
    expect(down.message).toBe('Cannot reach the server')
  })
})

function raw(status: number, body: string, contentType = 'text/html'): typeof fetch {
  return async () => new Response(body, { status, headers: { 'Content-Type': contentType } })
}

/** A client that records every request and answers `{}`. */
function recording(base = '') {
  const calls: { url: string; init?: RequestInit }[] = []
  const api = createApi(base, async (url, init) => {
    calls.push({ url: String(url), init })
    return new Response('{}')
  })
  return { api, calls }
}

describe('readDetail edge cases', () => {
  it('returns null for detail values that are not a message', () => {
    expect(readDetail({ detail: null })).toBeNull()
    expect(readDetail({ detail: 42 })).toBeNull()
    expect(readDetail({ detail: { msg: 'nested' } })).toBeNull()
    expect(readDetail({ detail: [] })).toBeNull()
    expect(readDetail({ detail: [null, 'text', { loc: ['body'] }] })).toBeNull()
    expect(readDetail('Insufficient cash')).toBeNull()
    expect(readDetail(undefined)).toBeNull()
  })

  it('keeps only the entries that carry a message', () => {
    expect(readDetail({ detail: [{ loc: ['body'] }, { msg: 'Field required' }, { msg: '' }] })).toBe('Field required')
  })
})

describe('error mapping', () => {
  const failure = (fetcher: typeof fetch) => createApi('', fetcher).portfolio().catch((e) => e)

  it.each([502, 503, 504])('explains a %i from the proxy with a non-JSON body', async (status) => {
    const error = await failure(raw(status, '<html><body>Bad Gateway</body></html>'))
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(status)
    expect(error.message).toBe('The backend is not reachable')
  })

  it('falls back to the status code for other non-JSON failures', async () => {
    expect((await failure(raw(500, 'Internal Server Error', 'text/plain'))).message).toBe('Request failed (500)')
    expect((await failure(raw(500, ''))).message).toBe('Request failed (500)')
    expect((await failure(raw(401, '<html></html>'))).message).toBe('Request failed (401)')
  })

  it('treats a 404 without a usable body as a missing service', async () => {
    const error = await failure(raw(404, '<h1>Not Found</h1>'))
    expect(error.status).toBe(404)
    expect(error.message).toBe('This service is not available')
    expect((await failure(raw(404, ''))).message).toBe('This service is not available')
  })

  it("passes a real 404 detail through (a ticker that isn't on the watchlist)", async () => {
    const error = await createApi('', respond(404, { detail: 'PYPL is not on the watchlist' }))
      .removeFromWatchlist('PYPL')
      .catch((e) => e)
    expect(error.status).toBe(404)
    expect(error.message).toBe('PYPL is not on the watchlist')
  })

  it('prefers a JSON detail over the generic gateway message', async () => {
    expect((await failure(respond(503, { detail: 'Market source is restarting' }))).message).toBe(
      'Market source is restarting',
    )
  })

  it('joins a validation error list and survives one with no messages', async () => {
    expect((await failure(respond(422, { detail: [{ msg: 'Field required' }, { msg: 'Too big' }] }))).message).toBe(
      'Field required; Too big',
    )
    expect((await failure(respond(422, { detail: [{ loc: ['body'] }] }))).message).toBe('Request failed (422)')
    expect((await failure(respond(400, { detail: '' }))).message).toBe('Request failed (400)')
    expect((await failure(respond(409, { message: 'wrong key' }))).message).toBe('Request failed (409)')
  })

  it('reports an unreachable server whatever the fetcher threw', async () => {
    const error = await failure(async () => {
      throw 'offline' // eslint-disable-line no-throw-literal
    })
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(0)
    expect(error.name).toBe('ApiError')
  })
})

describe('errorMessage', () => {
  it('uses the message of an Error and a fixed text for anything else', () => {
    expect(errorMessage(new ApiError(400, 'Insufficient cash'))).toBe('Insufficient cash')
    expect(errorMessage(new TypeError('boom'))).toBe('boom')
    expect(errorMessage('boom')).toBe('Something went wrong')
    expect(errorMessage(null)).toBe('Something went wrong')
    expect(errorMessage({ message: 'not an Error' })).toBe('Something went wrong')
  })
})

describe('requests', () => {
  it('strips trailing slashes from the base and builds the stream URL', () => {
    expect(createApi('http://x:8000///').streamUrl).toBe('http://x:8000/api/stream/prices')
    expect(createApi().streamUrl).toBe('/api/stream/prices')
  })

  it('hits the documented path and method for every endpoint', async () => {
    const { api, calls } = recording()
    await api.prices()
    await api.history('AAPL')
    await api.status()
    await api.watchlist()
    await api.addToWatchlist('PYPL')
    await api.removeFromWatchlist('PYPL')
    await api.portfolio()
    await api.trades()
    await api.trades(10)
    await api.placeTrade({ ticker: 'AAPL', side: 'sell', quantity: 0.5 })
    await api.resetPortfolio()
    expect(calls.map((c) => `${c.init?.method ?? 'GET'} ${c.url}`)).toEqual([
      'GET /api/market/prices',
      'GET /api/market/history/AAPL',
      'GET /api/market/status',
      'GET /api/watchlist',
      'POST /api/watchlist',
      'DELETE /api/watchlist/PYPL',
      'GET /api/portfolio',
      'GET /api/trades?limit=50',
      'GET /api/trades?limit=10',
      'POST /api/trades',
      'POST /api/portfolio/reset',
    ])
    expect(calls[9]!.init?.body).toBe('{"ticker":"AAPL","side":"sell","quantity":0.5}')
  })

  it('escapes a ticker used as a path segment', async () => {
    const { api, calls } = recording()
    await api.removeFromWatchlist('BRK.B')
    await api.history('A/B?x')
    await api.removeFromWatchlist('bad ticker')
    expect(calls.map((c) => c.url)).toEqual([
      '/api/watchlist/BRK.B',
      '/api/market/history/A%2FB%3Fx',
      '/api/watchlist/bad%20ticker',
    ])
  })

  it('sends Content-Type only when there is a body', async () => {
    const { api, calls } = recording()
    await api.portfolio()
    await api.removeFromWatchlist('AAPL')
    await api.resetPortfolio()
    await api.addToWatchlist('AAPL')
    const headers = calls.map((c) => c.init?.headers as Record<string, string>)
    for (const h of headers) expect(h.Accept).toBe('application/json')
    expect(headers.map((h) => h['Content-Type'])).toEqual([undefined, undefined, undefined, 'application/json'])
    expect(calls[1]!.init?.body).toBeUndefined()
  })
})

describe('assistant endpoints', () => {
  it('assistantChat posts the whole history and returns the stream body', async () => {
    let seen: { url: string; init?: RequestInit } | null = null
    const api = createApi('', async (url, init) => {
      seen = { url: String(url), init }
      return new Response('data: {"type":"done"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
    })
    const controller = new AbortController()
    const messages = [{ role: 'user' as const, content: 'hi' }]
    const body = await api.assistantChat(messages, controller.signal)
    expect(body).toBeInstanceOf(ReadableStream)
    expect(seen!.url).toBe('/api/assistant/chat')
    expect(seen!.init?.method).toBe('POST')
    expect(seen!.init?.signal).toBe(controller.signal)
    expect(JSON.parse(String(seen!.init?.body))).toEqual({ messages })
  })

  it("throws the backend's detail for a 503, and 'not available' for a 404", async () => {
    const detail = 'AI assistant is not configured. Set OPENROUTER_API_KEY and restart the backend.'
    await expect(createApi('', respond(503, { detail })).assistantChat([])).rejects.toMatchObject({ status: 503, message: detail })
    await expect(createApi('', respond(404, { detail: 'Not Found' })).assistantChat([])).rejects.toMatchObject({
      status: 404,
      message: 'This service is not available',
    })
  })

  it('reports an unreachable server as status 0, but lets an abort through untouched', async () => {
    const down = createApi('', async () => Promise.reject(new TypeError('failed')))
    await expect(down.assistantChat([])).rejects.toMatchObject({ status: 0 })

    const controller = new AbortController()
    controller.abort()
    const aborted = createApi('', async () => Promise.reject(new DOMException('aborted', 'AbortError')))
    await expect(aborted.assistantChat([], controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
  })
})
