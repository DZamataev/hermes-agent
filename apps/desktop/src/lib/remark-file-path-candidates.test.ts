import type { Root } from 'mdast'
import { expect, it } from 'vitest'

import { FILE_PATH_NODE_DATA, remarkFilePathCandidates } from './remark-file-path-candidates'

/**
 * The marker decides which tokens ever get a chance to resolve, so its scope is
 * tested directly rather than only through a rendered transcript: a shape that
 * never becomes a candidate is invisible downstream — the token simply stays
 * prose, which is also what a correctly rejected token does.
 */

interface Candidate {
  /** The path handed to the resolver. */
  path: string
  /** The text the reader sees and clicks. */
  token: string
}

function paragraph(text: string): Root {
  return { children: [{ children: [{ type: 'text', value: text }], type: 'paragraph' }], type: 'root' }
}

function inlineCode(value: string): Root {
  return { children: [{ children: [{ type: 'inlineCode', value }], type: 'paragraph' }], type: 'root' }
}

function candidatesOf(tree: Root): Candidate[] {
  remarkFilePathCandidates()(tree)

  const found: Candidate[] = []

  function walk(node: { children?: unknown[]; data?: Record<string, unknown>; type: string; value?: string }) {
    const path = node.data?.[FILE_PATH_NODE_DATA]

    if (typeof path === 'string') {
      found.push({ path, token: text(node) })

      return
    }

    for (const child of (node.children ?? []) as typeof node[]) {
      walk(child)
    }
  }

  function text(node: { children?: unknown[]; value?: string }): string {
    return node.value ?? ((node.children ?? []) as typeof node[]).map(text).join('')
  }

  walk(tree as never)

  return found
}

function candidates(text: string): Candidate[] {
  return candidatesOf(paragraph(text))
}

it('marks a slash-qualified path in prose', () => {
  expect(candidates('План — docs/plan.md на сегодня.')).toEqual([{ path: 'docs/plan.md', token: 'docs/plan.md' }])
})

it('keeps a cited line number in the token and out of the path', () => {
  expect(candidates('см. app/VideoMessageBubble.tsx:141 тут')).toEqual([
    { path: 'app/VideoMessageBubble.tsx', token: 'app/VideoMessageBubble.tsx:141' }
  ])
  expect(candidates('см. app/x.ts:141:12 тут')).toEqual([{ path: 'app/x.ts', token: 'app/x.ts:141:12' }])
})

it('marks a Windows drive path, in either slash direction', () => {
  // The resolver treats these as absolute and the file:// encoder already
  // normalizes them; a token that never becomes a candidate is the only
  // reason the platform stayed dark.
  expect(candidates('см. C:\\Users\\me\\out\\report.tsx:12 тут')).toEqual([
    { path: 'C:\\Users\\me\\out\\report.tsx', token: 'C:\\Users\\me\\out\\report.tsx:12' }
  ])
  expect(candidates('см. C:/Users/me/report.tsx тут')).toEqual([
    { path: 'C:/Users/me/report.tsx', token: 'C:/Users/me/report.tsx' }
  ])
})

it('marks a UNC share path', () => {
  expect(candidates('см. \\\\server\\share\\file.txt тут')).toEqual([
    { path: '\\\\server\\share\\file.txt', token: '\\\\server\\share\\file.txt' }
  ])
})

it('marks the long extensions real projects carry', () => {
  // `.xcworkspacedata` is 15 characters: a 12-char ceiling silently drops the
  // platform files an agent names most often.
  expect(candidates('правь ios/App.xcworkspacedata тут')).toEqual([
    { path: 'ios/App.xcworkspacedata', token: 'ios/App.xcworkspacedata' }
  ])
})

it('marks a path written in inline code, line suffix included', () => {
  expect(candidatesOf(inlineCode('docs/plan.md:7'))).toEqual([{ path: 'docs/plan.md', token: 'docs/plan.md:7' }])
})

it('marks a directory only when the trailing slash says it is one', () => {
  // A directory has no extension, so nothing about its SHAPE separates it from
  // ordinary prose — `and/or` and `w/o` are the same shape. The trailing slash
  // is the author stating the intent, and it is the only admission ticket.
  expect(candidates('лежит в ~/.hermes/desktop-plugins/ вот')).toEqual([
    { path: '~/.hermes/desktop-plugins', token: '~/.hermes/desktop-plugins/' }
  ])
  expect(candidates('правь src/app/features/ тут')).toEqual([
    { path: 'src/app/features', token: 'src/app/features/' }
  ])
})

it('keeps slash-joined prose out even though a directory has the same shape', () => {
  expect(candidates('and/or, w/o, 12:30, example.com:8080')).toEqual([])
  // No trailing slash: not offered as a directory, however plausible.
  expect(candidates('смотри в src/app/features тут')).toEqual([])
})

it('leaves prose that merely looks path-like alone', () => {
  // Widening the matcher must not start claiming clock times, host:port pairs,
  // or the slashes of ordinary writing.
  expect(candidates('в 12:30 на example.com:8080 — and/or, w/o')).toEqual([])
})

it('leaves an existing markdown link alone', () => {
  const tree: Root = {
    children: [
      {
        children: [{ children: [{ type: 'text', value: 'docs/plan.md' }], type: 'link', url: '#x' }],
        type: 'paragraph'
      }
    ],
    type: 'root'
  }

  expect(candidatesOf(tree)).toEqual([])
})
