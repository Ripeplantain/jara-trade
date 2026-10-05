import { describe, expect, it } from 'vitest'
import { parseInline, parseRichText } from '~/lib/richtext'

describe('parseInline', () => {
  it('finds bold and code, leaving the rest as text', () => {
    expect(parseInline('a **b** and `c` d')).toEqual([
      { kind: 'text', text: 'a ' },
      { kind: 'bold', text: 'b' },
      { kind: 'text', text: ' and ' },
      { kind: 'code', text: 'c' },
      { kind: 'text', text: ' d' },
    ])
  })

  it('keeps an unfinished marker literal (a reply still streaming)', () => {
    expect(parseInline('so **far')).toEqual([{ kind: 'text', text: 'so **far' }])
    expect(parseInline('a `half')).toEqual([{ kind: 'text', text: 'a `half' }])
  })

  it('never turns markup into anything but text', () => {
    const parts = parseInline('<img src=x onerror=alert(1)> **<b>x</b>**')
    expect(parts.every((p) => p.kind === 'text' || p.kind === 'bold')).toBe(true)
    expect(parts[0]).toEqual({ kind: 'text', text: '<img src=x onerror=alert(1)> ' })
  })
})

describe('parseRichText', () => {
  it('keeps line breaks inside a paragraph and splits paragraphs on blank lines', () => {
    expect(parseRichText('one\ntwo\n\nthree')).toEqual([
      { kind: 'p', inline: [{ kind: 'text', text: 'one' }, { kind: 'br' }, { kind: 'text', text: 'two' }] },
      { kind: 'p', inline: [{ kind: 'text', text: 'three' }] },
    ])
  })

  it('builds bullet and numbered lists', () => {
    const blocks = parseRichText('Intro\n- a\n* b\n\n1. x\n2) y')
    expect(blocks.map((b) => b.kind)).toEqual(['p', 'ul', 'ol'])
    expect(blocks[1]).toMatchObject({ items: [[{ text: 'a' }], [{ text: 'b' }]] })
    expect(blocks[2]).toMatchObject({ items: [[{ text: 'x' }], [{ text: 'y' }]] })
  })

  it('starts a new list after a blank line', () => {
    expect(parseRichText('- a\n\n- b').map((b) => b.kind)).toEqual(['ul', 'ul'])
  })

  it('handles \\r\\n and empty input', () => {
    expect(parseRichText('a\r\nb')).toHaveLength(1)
    expect(parseRichText('')).toEqual([])
  })
})
