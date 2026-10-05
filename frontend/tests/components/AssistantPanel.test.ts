// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AssistantPanel from '~/components/AssistantPanel.vue'
import { ApiError } from '~/lib/api'
import { controlledStream, createFakeApi, fill, portfolioOf, resetAppState, type FakeApi } from '../helpers'

const api = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('~/composables/useApi', () => ({ useApi: () => api.current }))

let fake: FakeApi

beforeEach(() => {
  fake = createFakeApi()
  fake.trades.mockResolvedValue({ trades: [] })
  fake.assistantStatus.mockResolvedValue({ available: true, model: 'claude-test' })
  api.current = fake
  resetAppState()
})

const PROPOSAL = {
  ticker: 'AAPL',
  side: 'buy',
  quantity: 3,
  rationale: 'Strong momentum.',
  estimated_price: 190,
  estimated_total: 570,
}

async function panel() {
  const wrapper = mount(AssistantPanel, { attachTo: document.body })
  await flushPromises()
  const ui = {
    wrapper,
    input: () => wrapper.find<HTMLTextAreaElement>('#assistant-input'),
    button: (label: string) => wrapper.findAll('button').find((b) => b.text().includes(label)),
    log: () => wrapper.find('[role="log"]'),
    type: async (text: string) => wrapper.find('#assistant-input').setValue(text),
    send: async () => {
      await wrapper.find('form').trigger('submit')
      await flushPromises()
    },
  }
  return ui
}

describe('AssistantPanel', () => {
  it('shows starter prompts when empty, and a starter sends itself', async () => {
    const s = controlledStream()
    fake.assistantChat.mockResolvedValue(s.body)
    const ui = await panel()
    expect(ui.wrapper.text()).toContain('claude-test')
    expect(ui.button('How is my portfolio doing?')).toBeTruthy()
    expect(ui.button('What moved most today?')).toBeTruthy()

    await ui.button('What moved most today?')!.trigger('click')
    await flushPromises()
    expect(fake.assistantChat).toHaveBeenCalledOnce()
    expect(fake.assistantChat.mock.calls[0]![0]).toEqual([{ role: 'user', content: 'What moved most today?' }])
    expect(ui.button('How is my portfolio doing?')).toBeUndefined()
  })

  it('streams the reply in live, then sends only text turns as history next time', async () => {
    const first = controlledStream()
    fake.assistantChat.mockResolvedValueOnce(first.body)
    const ui = await panel()
    await ui.type('Hello there')
    await ui.send()

    expect(ui.input().element.value).toBe('')
    expect(ui.log().text()).toContain('Hello there')
    expect(ui.button('Stop')).toBeTruthy()

    first.event({ type: 'tool', name: 'get_portfolio' })
    await flushPromises()
    expect(ui.log().text()).toContain('Checking portfolio…')

    first.event({ type: 'text', delta: 'You are **up**' })
    await flushPromises()
    expect(ui.log().text()).toContain('You are up')
    expect(ui.log().text()).not.toContain('Checking portfolio')
    expect(ui.log().find('strong').text()).toBe('up')

    first.event({ type: 'text', delta: ' today.' })
    first.event({ type: 'done' })
    await flushPromises()
    expect(ui.log().text()).toContain('You are up today.')
    expect(ui.button('Stop')).toBeUndefined()
    expect(ui.button('Send')).toBeTruthy()

    const second = controlledStream()
    fake.assistantChat.mockResolvedValueOnce(second.body)
    await ui.type('And MSFT?')
    await ui.send()
    expect(fake.assistantChat.mock.calls[1]![0]).toEqual([
      { role: 'user', content: 'Hello there' },
      { role: 'assistant', content: 'You are **up** today.' }, // as the model wrote it
      { role: 'user', content: 'And MSFT?' },
    ])
  })

  it('sends on Enter but not on Shift+Enter, and not when empty', async () => {
    fake.assistantChat.mockResolvedValue(controlledStream().body)
    const ui = await panel()
    await ui.type('line one')
    await ui.input().trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(fake.assistantChat).not.toHaveBeenCalled()
    await ui.input().trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(fake.assistantChat).toHaveBeenCalledOnce()

    await ui.input().trigger('keydown', { key: 'Enter' }) // empty input
    expect(fake.assistantChat).toHaveBeenCalledOnce()
  })

  it('renders model text as text, never as markup', async () => {
    const s = controlledStream()
    fake.assistantChat.mockResolvedValue(s.body)
    const ui = await panel()
    await ui.type('hi')
    await ui.send()
    s.event({ type: 'text', delta: '<img src=x onerror=alert(1)> <script>boom()</script>' })
    s.event({ type: 'done' })
    await flushPromises()
    expect(ui.log().find('img').exists()).toBe(false)
    expect(ui.log().find('script').exists()).toBe(false)
    expect(ui.log().text()).toContain('<img src=x onerror=alert(1)>')
  })

  it('stop aborts the request and keeps the text received so far', async () => {
    const s = controlledStream()
    fake.assistantChat.mockImplementation(async (_messages, signal) => {
      signal?.addEventListener('abort', () => s.fail(new DOMException('aborted', 'AbortError')))
      return s.body
    })
    const ui = await panel()
    await ui.type('go')
    await ui.send()
    s.event({ type: 'text', delta: 'partial' })
    await flushPromises()

    await ui.button('Stop')!.trigger('click')
    await flushPromises()
    expect(fake.assistantChat.mock.calls[0]![1]!.aborted).toBe(true)
    expect(ui.log().text()).toContain('partial')
    expect(ui.log().text()).toContain('Stopped')
    expect(ui.log().find('[role="alert"]').exists()).toBe(false)
    expect(ui.button('Send')).toBeTruthy()
  })

  it('clear empties the conversation and aborts a running reply', async () => {
    const s = controlledStream()
    fake.assistantChat.mockResolvedValue(s.body)
    const ui = await panel()
    await ui.type('zebra')
    await ui.send()
    await ui.button('Clear')!.trigger('click')
    await flushPromises()
    expect(ui.log().text()).not.toContain('zebra')
    expect(ui.button('How is my portfolio doing?')).toBeTruthy()
    expect(fake.assistantChat.mock.calls[0]![1]!.aborted).toBe(true)
    expect(ui.button('Clear')).toBeUndefined()
  })

  describe('trade proposals', () => {
    async function withProposal() {
      const s = controlledStream()
      fake.assistantChat.mockResolvedValue(s.body)
      const ui = await panel()
      await ui.type('should I buy AAPL?')
      await ui.send()
      s.event({ type: 'text', delta: 'Maybe.' })
      s.event({ type: 'trade_proposal', proposal: PROPOSAL })
      s.event({ type: 'done' })
      await flushPromises()
      return ui
    }

    it('renders the card and does not trade until Confirm is clicked', async () => {
      const ui = await withProposal()
      const card = ui.wrapper.find('article')
      expect(card.text()).toContain('buy 3 AAPL')
      expect(card.text()).toContain('190.00')
      expect(card.text()).toContain('$570.00')
      expect(card.text()).toContain('Strong momentum.')
      expect(fake.placeTrade).not.toHaveBeenCalled()
    })

    it('confirm places the trade once, shows the actual fill and updates the portfolio', async () => {
      const ui = await withProposal()
      fake.trades.mockResolvedValue({ trades: [fill(7, 'AAPL', 'buy', 3, 191.5)] })
      fake.placeTrade.mockResolvedValue({
        trade: fill(7, 'AAPL', 'buy', 3, 191.5),
        portfolio: portfolioOf(9_425.5),
      })
      const confirm = ui.button('Confirm buy')!
      await confirm.trigger('click')
      await confirm.trigger('click') // a second click while in flight must not repeat it
      await flushPromises()

      expect(fake.placeTrade).toHaveBeenCalledExactlyOnceWith({ ticker: 'AAPL', side: 'buy', quantity: 3 })
      const status = ui.wrapper.find('article [role="status"]')
      expect(status.text()).toBe('Filled: bought 3 AAPL at $191.50, $574.50 total')
      expect(ui.button('Confirm')).toBeUndefined()
      expect(ui.button('Dismiss')).toBeUndefined()
      expect(usePortfolio().portfolio.value!.cash).toBe(9_425.5)
      expect(usePortfolio().trades.value[0]!.id).toBe(7)
    })

    it("shows the backend's detail when the order is refused, and cannot be confirmed again", async () => {
      const ui = await withProposal()
      fake.placeTrade.mockRejectedValue(new ApiError(400, 'Insufficient cash: need $570.00, have $100.00'))
      await ui.button('Confirm buy')!.trigger('click')
      await flushPromises()

      expect(ui.wrapper.find('article [role="alert"]').text()).toBe('Not placed: Insufficient cash: need $570.00, have $100.00')
      expect(ui.button('Confirm')).toBeUndefined()
      expect(fake.placeTrade).toHaveBeenCalledOnce()
    })

    it('dismiss removes the actions without trading', async () => {
      const ui = await withProposal()
      await ui.button('Dismiss')!.trigger('click')
      expect(ui.wrapper.find('article').text()).toContain('Dismissed')
      expect(ui.button('Confirm')).toBeUndefined()
      expect(fake.placeTrade).not.toHaveBeenCalled()
    })

    it('keeps proposals out of the history sent next', async () => {
      const ui = await withProposal()
      fake.assistantChat.mockResolvedValue(controlledStream().body)
      await ui.type('thanks')
      await ui.send()
      expect(fake.assistantChat.mock.calls[1]![0]).toEqual([
        { role: 'user', content: 'should I buy AAPL?' },
        { role: 'assistant', content: 'Maybe.' },
        { role: 'user', content: 'thanks' },
      ])
    })
  })

  describe('failures', () => {
    it('shows a mid-stream error inline, keeps the text, and stays usable', async () => {
      const s = controlledStream()
      fake.assistantChat.mockResolvedValueOnce(s.body)
      const ui = await panel()
      await ui.type('hi')
      await ui.send()
      s.event({ type: 'text', delta: 'Half an answ' })
      s.event({ type: 'error', detail: 'The model is overloaded' })
      s.event({ type: 'done' })
      await flushPromises()

      expect(ui.log().text()).toContain('Half an answ')
      expect(ui.log().find('[role="alert"]').text()).toBe('The model is overloaded')
      expect(ui.input().element.disabled).toBe(false)

      const next = controlledStream()
      fake.assistantChat.mockResolvedValueOnce(next.body)
      await ui.type('retry')
      await ui.send()
      expect(fake.assistantChat).toHaveBeenCalledTimes(2)
    })

    it('treats a network drop mid-stream the same way', async () => {
      const s = controlledStream()
      fake.assistantChat.mockResolvedValue(s.body)
      const ui = await panel()
      await ui.type('hi')
      await ui.send()
      s.event({ type: 'text', delta: 'Before the drop' })
      await flushPromises()
      s.fail(new TypeError('network error'))
      await flushPromises()

      expect(ui.log().text()).toContain('Before the drop')
      expect(ui.log().find('[role="alert"]').text()).toBe('The connection dropped before the reply finished.')
      expect(ui.button('Stop')).toBeUndefined()
      expect(ui.input().element.disabled).toBe(false)
    })

    it('flags a stream that ends without done as dropped', async () => {
      const s = controlledStream()
      fake.assistantChat.mockResolvedValue(s.body)
      const ui = await panel()
      await ui.type('hi')
      await ui.send()
      s.event({ type: 'text', delta: 'cut short' })
      s.end()
      await flushPromises()
      expect(ui.log().find('[role="alert"]').text()).toContain('dropped')
    })

    it("shows the backend's refusal on the message and switches to the unavailable notice on 503", async () => {
      const detail = 'AI assistant is not configured. Set OPENROUTER_API_KEY and restart the backend.'
      fake.assistantChat.mockRejectedValue(new ApiError(503, detail))
      const ui = await panel()
      await ui.type('hi')
      await ui.send()
      expect(ui.wrapper.text()).toContain('Assistant not configured')
      expect(ui.wrapper.text()).toContain(detail)
      expect(ui.input().element.disabled).toBe(true)
    })

    it('shows a 422 detail inline and keeps going', async () => {
      fake.assistantChat.mockRejectedValue(new ApiError(422, 'messages: too long'))
      const ui = await panel()
      await ui.type('hi')
      await ui.send()
      expect(ui.log().find('[role="alert"]').text()).toBe('messages: too long')
      expect(ui.input().element.disabled).toBe(false)
    })
  })

  describe('availability', () => {
    it('explains an unconfigured assistant and disables the input', async () => {
      fake.assistantStatus.mockResolvedValue({ available: false, model: null })
      const ui = await panel()
      expect(ui.wrapper.text()).toContain('Assistant not configured')
      expect(ui.wrapper.text()).toContain('Set OPENROUTER_API_KEY and restart the backend.')
      expect(ui.input().element.disabled).toBe(true)
      expect(ui.button('Send')!.element.disabled).toBe(true)
      expect(ui.button('How is my portfolio doing?')).toBeUndefined()
    })

    it('says "Assistant unavailable" when the status endpoint is missing, then recovers', async () => {
      vi.useFakeTimers()
      try {
        fake.assistantStatus.mockRejectedValue(new ApiError(404, 'This service is not available'))
        const ui = await panel()
        expect(ui.wrapper.text()).toContain('Assistant unavailable')
        expect(ui.input().exists()).toBe(false)

        fake.assistantStatus.mockResolvedValue({ available: true, model: 'claude-test' })
        await vi.advanceTimersByTimeAsync(10_000)
        await flushPromises()
        expect(ui.wrapper.text()).not.toContain('Assistant unavailable')
        expect(ui.input().element.disabled).toBe(false)
      } finally {
        vi.useRealTimers()
      }
    })

    it('says "Assistant unavailable" when the backend cannot be reached', async () => {
      fake.assistantStatus.mockRejectedValue(new ApiError(0, 'Cannot reach the server'))
      const ui = await panel()
      expect(ui.wrapper.text()).toContain('Assistant unavailable')
    })
  })
})
