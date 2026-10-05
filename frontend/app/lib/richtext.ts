/**
 * Minimal, safe formatting for assistant text. The output is a plain data tree
 * that the template renders as text nodes, so nothing the model writes can
 * become markup. Supported: paragraphs, line breaks, `- ` / `* ` bullet lists,
 * `1. ` numbered lists, **bold** and `inline code`. Anything else (including an
 * unfinished `**` while a reply is still streaming) stays literal text.
 */
export type Inline =
  | { kind: 'text'; text: string }
  | { kind: 'bold'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'br' }

export type Block =
  | { kind: 'p'; inline: Inline[] }
  | { kind: 'ul' | 'ol'; items: Inline[][] }

const TOKEN = /`([^`\n]+)`|\*\*([^*\n]+?)\*\*/g

export function parseInline(text: string): Inline[] {
  const out: Inline[] = []
  let last = 0
  for (const match of text.matchAll(TOKEN)) {
    const index = match.index ?? 0
    if (index > last) out.push({ kind: 'text', text: text.slice(last, index) })
    if (match[1] !== undefined) out.push({ kind: 'code', text: match[1] })
    else out.push({ kind: 'bold', text: match[2] ?? '' })
    last = index + match[0].length
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) })
  return out
}

const BULLET = /^\s*[-*•]\s+(.*)$/
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/

export function parseRichText(source: string): Block[] {
  const blocks: Block[] = []
  let paragraph: string[] = []

  const endParagraph = () => {
    if (!paragraph.length) return
    const inline: Inline[] = []
    paragraph.forEach((text, i) => {
      if (i > 0) inline.push({ kind: 'br' })
      inline.push(...parseInline(text))
    })
    blocks.push({ kind: 'p', inline })
    paragraph = []
  }

  for (const line of source.replace(/\r\n?/g, '\n').split('\n')) {
    const bullet = BULLET.exec(line)
    const numbered = bullet ? null : NUMBERED.exec(line)
    const item = bullet ?? numbered
    if (item) {
      endParagraph()
      const kind = bullet ? 'ul' : 'ol'
      const prev = blocks[blocks.length - 1]
      const entry = parseInline(item[1] ?? '')
      if (prev && prev.kind === kind) prev.items.push(entry)
      else blocks.push({ kind, items: [entry] })
    } else if (line.trim() === '') {
      endParagraph()
      // A blank line also ends a list: the next item starts a new one.
      const prev = blocks[blocks.length - 1]
      if (prev && prev.kind !== 'p') blocks.push({ kind: 'p', inline: [] }) // spacer, dropped below
    } else {
      paragraph.push(line)
    }
  }
  endParagraph()
  return blocks.filter((b) => b.kind !== 'p' || b.inline.length > 0)
}
