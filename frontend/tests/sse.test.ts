import { describe, expect, it } from 'vitest'
import { createSseParser } from '~/lib/sse'

function run(chunks: string[], flush = true): string[] {
  const out: string[] = []
  const parser = createSseParser((d) => out.push(d))
  for (const chunk of chunks) parser.feed(chunk)
  if (flush) parser.flush()
  return out
}

describe('createSseParser', () => {
  it('reads one frame', () => {
    expect(run(['data: {"a":1}\n\n'])).toEqual(['{"a":1}'])
  })

  it('reads several frames from one chunk', () => {
    expect(run(['data: 1\n\ndata: 2\n\ndata: 3\n\n'])).toEqual(['1', '2', '3'])
  })

  it('joins a frame split anywhere across chunks', () => {
    const text = 'data: {"type":"text","delta":"hi"}\n\ndata: {"type":"done"}\n\n'
    for (let cut = 1; cut < text.length; cut++) {
      expect(run([text.slice(0, cut), text.slice(cut)])).toEqual(['{"type":"text","delta":"hi"}', '{"type":"done"}'])
    }
    expect(run([...text].map((c) => c))).toHaveLength(2) // one character at a time
  })

  it('accepts \\r\\n and bare \\r line ends, even a \\r\\n cut in two', () => {
    expect(run(['data: a\r\n\r\ndata: b\r\r'])).toEqual(['a', 'b'])
    expect(run(['data: a\r', '\n\r', '\ndata: b\n\n'])).toEqual(['a', 'b'])
  })

  it('ignores comments, blank lines and other fields', () => {
    expect(run([': keep-alive\n\n\n\nevent: x\nid: 4\nretry: 10\ndata: ok\n\n'])).toEqual(['ok'])
  })

  it('joins multi-line data with a newline', () => {
    expect(run(['data: a\ndata: b\n\n'])).toEqual(['a\nb'])
  })

  it('tolerates data without the space after the colon', () => {
    expect(run(['data:x\n\n'])).toEqual(['x'])
  })

  it('holds an unfinished frame until it is complete', () => {
    expect(run(['data: a\n'], false)).toEqual([])
    expect(run(['data: a'], true)).toEqual(['a']) // flush delivers a final unterminated frame
  })
})
