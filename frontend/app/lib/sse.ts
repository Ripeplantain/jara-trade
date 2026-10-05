/**
 * A tolerant parser for Server-Sent Events read off a `fetch` body (a POST
 * cannot use `EventSource`). Feed it decoded text in any chunking; it calls
 * `onData` once per event with that event's `data` lines joined by "\n".
 *
 * Handles frames split across chunks, several frames in one chunk, `\n`, `\r\n`
 * and bare `\r` line ends (even a `\r\n` cut between two chunks), and ignores
 * comment lines (`: ...`) and fields other than `data`.
 */
export interface SseParser {
  feed: (chunk: string) => void
  /** The stream ended: deliver a final event that was not followed by a blank line. */
  flush: () => void
}

export function createSseParser(onData: (data: string) => void): SseParser {
  let buffer = ''
  let data: string[] = []

  function line(text: string) {
    if (text === '') {
      if (data.length) {
        const joined = data.join('\n')
        data = []
        onData(joined)
      }
      return
    }
    if (text.startsWith(':')) return
    const colon = text.indexOf(':')
    const field = colon === -1 ? text : text.slice(0, colon)
    if (field !== 'data') return
    let value = colon === -1 ? '' : text.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    data.push(value)
  }

  return {
    feed(chunk) {
      buffer += chunk
      let start = 0
      for (let i = 0; i < buffer.length; i++) {
        const c = buffer[i]
        if (c === '\n') {
          line(buffer.slice(start, i))
          start = i + 1
        } else if (c === '\r') {
          if (i === buffer.length - 1) break // maybe the first half of "\r\n"; wait for more
          line(buffer.slice(start, i))
          start = buffer[i + 1] === '\n' ? i + 2 : i + 1
          i = start - 1
        }
      }
      buffer = buffer.slice(start)
    },
    flush() {
      const rest = buffer.replace(/\r$/, '')
      buffer = ''
      if (rest) line(rest)
      line('')
    },
  }
}
