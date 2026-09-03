import { Fragment, useMemo } from 'react'
import katex from 'katex'

type Piece =
  | { type: 'text'; value: string }
  | { type: 'formula'; value: string; display: boolean }

const CJK = /[\u4e00-\u9fff]/

function latexDensityScore(text: string): { score: number; cjk: number } {
  const cmd = (text.match(/\\[A-Za-z]+/g) || []).length
  const sub = (text.match(/[_^]\s*\{/g) || []).length
  const braces = (text.match(/\{/g) || []).length
  const cjk = (text.match(CJK) || []).length
  const strong = /\\left|\\right|\\frac|\\sum|\\sqrt|\\int|\\prod|\\big/gi.test(text) ? 3 : 0
  const score = cmd * 3 + sub * 2 + (braces > 0 ? Math.min(braces, 6) : 0) + strong
  return { score, cjk }
}

function isBareFormula(text: string): boolean {
  if (text.length < 5) return false
  const { score, cjk } = latexDensityScore(text)
  return score >= 6 && (cjk === 0 || score >= cjk * 4 + 8)
}

function renderFormula(tex: string, display: boolean): string {
  if (tex.trim().length === 0 || tex.length > 600) return ''
  const html = katex.renderToString(tex, {
    displayMode: display,
    throwOnError: false,
    strict: false,
    output: 'html',
  })
  if (/katex-error/.test(html)) {
    const patched = closeOpenBraces(tex)
    if (patched !== tex) {
      const retry = katex.renderToString(patched, {
        displayMode: display,
        throwOnError: false,
        strict: false,
        output: 'html',
      })
      if (!/katex-error/.test(retry)) return retry
    }
    return ''
  }
  return html
}

// Append enough "}" to balance an unterminated group near the end of a
// truncated formula fragment so KaTeX can still render what is visible.
function closeOpenBraces(tex: string): string {
  let open = 0
  for (const ch of tex) {
    if (ch === '{') open++
    else if (ch === '}') open = Math.max(0, open - 1)
  }
  return open > 0 ? tex + '}'.repeat(open) : tex
}

function isDisplayLike(text: string): boolean {
  return text.length > 80 || /\\left|\\right|\\frac|\\sum|\\int|\\sqrt/.test(text)
}

export function splitLatexSegments(source: string): Piece[] {
  // Normalize spaced display delimiters like "$ $ ... $ $" into "$$...$$".
  const normalized = source.replace(/\$\s*\$/g, '$$')

  const DISPLAY = /\$\$\s*([\s\S]+?)\s*\$\$/g
  const INLINE = /\$\s*([^$\n]+?)\s*\$/g

  const pieces: Piece[] = []
  let cursor = 0
  const ranges: Array<{ start: number; end: number; value: string; display: boolean }> = []

  for (const m of normalized.matchAll(DISPLAY)) {
    ranges.push({ start: m.index, end: m.index + m[0].length, value: m[1], display: true })
  }
  for (const m of normalized.matchAll(INLINE)) {
    ranges.push({ start: m.index, end: m.index + m[0].length, value: m[1], display: false })
  }
  ranges.sort((a, b) => a.start - b.start)

  const flush = (text: string) => {
    if (!text) return
    if (isBareFormula(text)) {
      pieces.push({ type: 'formula', value: text, display: isDisplayLike(text) })
    } else {
      pieces.push({ type: 'text', value: text })
    }
  }

  let anchor = 0
  for (const item of ranges) {
    if (item.start < cursor) continue
    if (item.start > anchor) {
      flush(normalized.slice(anchor, item.start))
    }
    pieces.push({ type: 'formula', value: item.value, display: item.display })
    anchor = item.end
    cursor = item.end
  }
  if (anchor < normalized.length) {
    flush(normalized.slice(anchor))
  }
  return pieces
}

export default function LatexText({ text, className }: { text: string; className?: string }) {
  const pieces = useMemo(() => splitLatexSegments(text), [text])

  return (
    <span className={className}>
      {pieces.map((piece, index) => {
        if (piece.type === 'text') {
          return <Fragment key={index}>{piece.value}</Fragment>
        }
        const html = renderFormula(piece.value, piece.display)
        if (!html) {
          return <Fragment key={index}>{piece.value}</Fragment>
        }
        return (
          <span
            key={index}
            className={piece.display ? 'latex-display' : 'latex-inline'}
            dangerouslySetInnerHTML={{ __html: html }}
          />
        )
      })}
    </span>
  )
}