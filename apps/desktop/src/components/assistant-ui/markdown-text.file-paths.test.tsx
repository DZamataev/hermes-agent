import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { $pathModifierHeld, watchPathModifier } from '@/store/path-modifier'
import { $previewTabs, closeRightRail } from '@/store/preview'
import { $currentCwd } from '@/store/session'

import { MarkdownTextContent } from './markdown-text'

const TREE: Record<string, string[]> = {
  '/work/looky/docs': [{ name: 'plan.md' }].map(entry => entry.name)
}

vi.mock('@/lib/desktop-fs', () => ({
  // Only paths inside the repo have this root; /elsewhere is outside it.
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

let stopModifier: () => void

beforeEach(() => {
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

it('leaves a path as plain prose until the modifier is held', async () => {
  const { container } = render(<MarkdownTextContent isRunning={false} text="План — docs/plan.md на сегодня." />)

  const token = container.querySelector('[data-file-path="docs/plan.md"]')

  expect(token).not.toBeNull()
  expect(container.querySelector('a')).toBeNull()
  fireEvent.mouseEnter(token as Element)
  // Nothing resolves and nothing is offered while the modifier is up.
  await waitFor(() => expect(token?.className ?? '').not.toContain('underline'))
  fireEvent.click(token as Element)
  expect($previewTabs.get()).toHaveLength(0)
})

it('offers and opens a path that exists once the modifier is held', async () => {
  const { container } = render(<MarkdownTextContent isRunning={false} text="План — docs/plan.md на сегодня." />)
  const token = container.querySelector('[data-file-path="docs/plan.md"]') as Element

  hold()
  fireEvent.mouseEnter(token)
  await waitFor(() => expect(token.className).toContain('underline'))
  fireEvent.click(token)
  await waitFor(() =>
    expect($previewTabs.get().some(tab => tab.target.url === 'file:///work/looky/docs/plan.md')).toBe(true)
  )
})

it('never offers a path that does not exist, however plausible it looks', async () => {
  // The exact shape that shipped broken links before: a real file named from
  // the wrong directory. Hovering it with the modifier down must change
  // nothing, and clicking must not open an error.
  const { container } = render(
    <MarkdownTextContent
      isRunning={false}
      text={'Заголовок — details/header/ChatMessagesHeader.tsx, план — docs/plan.md.'}
    />
  )

  const missing = container.querySelector('[data-file-path="details/header/ChatMessagesHeader.tsx"]') as Element
  const present = container.querySelector('[data-file-path="docs/plan.md"]') as Element

  hold()
  fireEvent.mouseEnter(missing)
  fireEvent.mouseEnter(present)
  // Asserting an absence has to wait for something observable, or it passes
  // before the resolver has run and would stay green however broken it is.
  // The neighbouring real path resolving is that clock.
  await waitFor(() => expect(present.className).toContain('underline'))
  expect(missing.className).not.toContain('underline')
  fireEvent.click(missing)
  expect($previewTabs.get()).toHaveLength(0)
})

it('keeps GFM tables rendering after the plugin list is extended', async () => {
  // The candidate plugin is appended to streamdown's defaults; replacing them
  // would silently drop table support from every transcript.
  render(<MarkdownTextContent isRunning={false} text={'| a | b |\n| --- | --- |\n| 1 | 2 |'} />)

  expect(await screen.findByRole('table')).toBeTruthy()
})

it('does not carry one session cwd answer into another', async () => {
  // Resolutions are cached across mounts; a cache that ignored the cwd would
  // offer a file from the previous session's directory.
  const text = 'План — docs/plan.md.'
  const first = render(<MarkdownTextContent isRunning={false} text={text} />)

  hold()
  fireEvent.mouseEnter(first.container.querySelector('[data-file-path]') as Element)
  await waitFor(() => expect((first.container.querySelector('[data-file-path]') as Element).className).toContain('underline'))
  cleanup()

  // Same path, different working directory: nothing exists under it.
  $currentCwd.set('/elsewhere')

  const second = render(<MarkdownTextContent isRunning={false} text={text} />)
  const token = second.container.querySelector('[data-file-path]') as Element

  expect(token.className).not.toContain('underline')
  fireEvent.mouseEnter(token)
  await waitFor(() => expect($pathModifierHeld.get()).toBe(true))
  expect(token.className).not.toContain('underline')
})
