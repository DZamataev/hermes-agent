import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { RESOLVED_PATH_ATTR } from '@/components/chat/file-path-candidate'
import type * as ExternalLink from '@/lib/external-link'
import { $pathModifierHeld, watchPathModifier } from '@/store/path-modifier'
import { $previewTabs, closeRightRail } from '@/store/preview'
import { $currentCwd } from '@/store/session'

import { MarkdownTextContent } from './markdown-text'

const TREE: Record<string, string[]> = {
  '/work/looky/docs': ['plan.md']
}

const opened: string[] = []

vi.mock('@/lib/desktop-fs', () => ({
  desktopGitRoot: (path: string) => Promise.resolve(path.startsWith('/work/looky') ? '/work/looky' : null),
  isDesktopFsRemoteMode: () => false,
  readDesktopDir: (dir: string) =>
    Promise.resolve(
      TREE[dir]
        ? { entries: TREE[dir].map(name => ({ isDirectory: false, name, path: `${dir}/${name}` })) }
        : { entries: [], error: 'ENOENT' }
    ),
  readDesktopFileDataUrl: () => Promise.resolve(''),
  readDesktopFileText: () => Promise.resolve({ binary: false, language: 'markdown', text: '# plan' })
}))

// Partial: the renderer pulls several helpers from this module, and a bare
// factory would silently remove the rest.
vi.mock('@/lib/external-link', async importOriginal => ({
  ...(await importOriginal<typeof ExternalLink>()),
  openExternalLink: (href: string) => opened.push(href)
}))

let stopModifier: () => void

beforeEach(() => {
  opened.length = 0
  stopModifier = watchPathModifier()
  $currentCwd.set('/work/looky')
})

afterEach(() => {
  stopModifier()
  cleanup()
  closeRightRail()
  $currentCwd.set('')
  $pathModifierHeld.set(false)
})

function hold() {
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Meta', metaKey: true }))
}

async function renderResolved() {
  const { container } = render(<MarkdownTextContent isRunning={false} text="План — docs/plan.md." />)
  const token = container.querySelector('[data-file-path]') as HTMLElement

  fireEvent.mouseEnter(token)
  await waitFor(() => expect(token.getAttribute(RESOLVED_PATH_ATTR)).toBe('/work/looky/docs/plan.md'))

  return token
}

it('sends the file to the OS when alt joins the modifier, instead of the preview rail', async () => {
  const token = await renderResolved()

  hold()
  await waitFor(() => expect(token.className).toContain('underline'))
  // Both accelerators: jsdom reports no platform, and the gesture is
  // Cmd+Alt on macOS / Ctrl+Alt elsewhere. Which of the two applies is
  // covered per-platform in lib/open-in-editor.test.ts.
  fireEvent.click(token, { altKey: true, ctrlKey: true, metaKey: true })

  await waitFor(() => expect(opened).toEqual(['file:///work/looky/docs/plan.md']))
  expect($previewTabs.get()).toHaveLength(0)
})

it('keeps the plain modifier click on the preview rail', async () => {
  const token = await renderResolved()

  hold()
  await waitFor(() => expect(token.className).toContain('underline'))
  fireEvent.click(token, { metaKey: true })

  await waitFor(() =>
    expect($previewTabs.get().some(tab => tab.target.url === 'file:///work/looky/docs/plan.md')).toBe(true)
  )
  expect(opened).toEqual([])
})

it('resolves on hover without the modifier so a right-click knows what it offers', async () => {
  // The context menu paints from the DOM at click time and no modifier
  // precedes a right-click, so the proven path must already be attached.
  const token = await renderResolved()

  expect($pathModifierHeld.get()).toBe(false)
  expect(token.className).not.toContain('underline')
  expect(token.getAttribute(RESOLVED_PATH_ATTR)).toBe('/work/looky/docs/plan.md')
})

it('attaches nothing for a path that does not resolve', async () => {
  const { container } = render(
    <MarkdownTextContent isRunning={false} text="Заголовок — details/header/ChatMessagesHeader.tsx." />
  )

  const missing = container.querySelector('[data-file-path]') as HTMLElement
  const { container: other } = render(<MarkdownTextContent isRunning={false} text="План — docs/plan.md." />)
  const present = other.querySelector('[data-file-path]') as HTMLElement

  fireEvent.mouseEnter(missing)
  fireEvent.mouseEnter(present)
  await waitFor(() => expect(present.getAttribute(RESOLVED_PATH_ATTR)).toBeTruthy())
  expect(missing.hasAttribute(RESOLVED_PATH_ATTR)).toBe(false)
})
