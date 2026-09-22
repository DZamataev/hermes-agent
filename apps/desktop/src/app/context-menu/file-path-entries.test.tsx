import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { RESOLVED_PATH_ATTR } from '@/components/chat/file-path-candidate'
import { en } from '@/i18n/en'
import type * as ExternalLink from '@/lib/external-link'
import { pickRevealLabel } from '@/lib/file-manager'
import { $previewTabs, closeRightRail } from '@/store/preview'

import { AppContextMenu } from './app-context-menu'
import { $contextMenu } from './store'
import { resolveDomTarget } from './target'

const opened: string[] = []
const { revealed, state } = vi.hoisted(() => ({ revealed: [] as string[], state: { remote: false } }))

vi.mock('@/lib/external-link', async importOriginal => ({
  ...(await importOriginal<typeof ExternalLink>()),
  openExternalLink: (href: string) => opened.push(href)
}))

vi.mock('@/lib/desktop-fs', () => ({
  copyTextToClipboard: () => Promise.resolve(),
  isDesktopFsRemoteMode: () => state.remote,
  readDesktopFileText: () => Promise.resolve({ binary: false, language: 'markdown', text: '# plan' }),
  renameDesktopPath: () => Promise.resolve(''),
  revealDesktopPath: (path: string) => {
    revealed.push(path)

    return Promise.resolve()
  },
  trashDesktopPath: () => Promise.resolve()
}))

const desktopWindow = window as unknown as { hermesDesktop?: Window['hermesDesktop'] }

function attach(html: string): HTMLElement {
  const host = document.createElement('div')

  host.innerHTML = html
  document.body.appendChild(host)

  return host
}

afterEach(() => {
  opened.length = 0
  revealed.length = 0
  state.remote = false
  $contextMenu.set(null)
  closeRightRail()
  cleanup()
  vi.restoreAllMocks()
  document.body.innerHTML = ''
  delete desktopWindow.hermesDesktop
})

it('reads the proven path off the token a right-click landed on', () => {
  const host = attach(`<span ${RESOLVED_PATH_ATTR}="/work/looky/docs/plan.md">docs/plan.md</span>`)

  expect(resolveDomTarget(host.querySelector('span')).filePath).toBe('/work/looky/docs/plan.md')
})

it('reads no path from a token that never resolved', () => {
  // The attribute is attached only once a file is found, so an unresolved
  // candidate must not produce menu entries that cannot work.
  const host = attach('<span data-file-path="details/header/X.tsx">details/header/X.tsx</span>')

  expect(resolveDomTarget(host.querySelector('span')).filePath).toBe('')
})

it('offers both destinations for a resolved file, and the editor one opens the OS handler', async () => {
  desktopWindow.hermesDesktop = {
    openExternal: vi.fn().mockResolvedValue(undefined),
    writeClipboard: vi.fn().mockResolvedValue(undefined)
  } as unknown as Window['hermesDesktop']

  render(
    <MemoryRouter>
      <AppContextMenu />
    </MemoryRouter>
  )

  const host = attach(`<span ${RESOLVED_PATH_ATTR}="/work/looky/docs/plan.md">docs/plan.md</span>`)

  fireEvent.contextMenu(host.querySelector('span')!)

  expect(await screen.findByText('Open in Hermes preview')).toBeTruthy()
  fireEvent.click(await screen.findByText('Open in editor'))

  expect(opened).toEqual(['file:///work/looky/docs/plan.md'])
  expect($previewTabs.get()).toHaveLength(0)
})

it('reveals a resolved file in the OS file manager', async () => {
  desktopWindow.hermesDesktop = {
    openExternal: vi.fn().mockResolvedValue(undefined),
    writeClipboard: vi.fn().mockResolvedValue(undefined)
  } as unknown as Window['hermesDesktop']

  render(
    <MemoryRouter>
      <AppContextMenu />
    </MemoryRouter>
  )

  const host = attach(`<span ${RESOLVED_PATH_ATTR}="/work/looky/docs/plan.md">docs/plan.md</span>`)

  fireEvent.contextMenu(host.querySelector('span')!)

  // The label names the host's own file manager, so assert through the same
  // resolver the menu uses rather than freezing one platform's wording.
  const label = pickRevealLabel(en.fileMenu.revealFinder, en.fileMenu.revealExplorer, en.fileMenu.revealFileManager)

  fireEvent.click(await screen.findByText(label))

  await waitFor(() => expect(revealed).toEqual(['/work/looky/docs/plan.md']))
  expect(opened).toEqual([])
})

it('does not offer to reveal a path that lives on a remote gateway', async () => {
  // `showItemInFolder` acts on THIS machine; a remote path would select
  // nothing at all, so the entry must be absent rather than a silent no-op.
  state.remote = true
  desktopWindow.hermesDesktop = {
    openExternal: vi.fn().mockResolvedValue(undefined),
    writeClipboard: vi.fn().mockResolvedValue(undefined)
  } as unknown as Window['hermesDesktop']

  render(
    <MemoryRouter>
      <AppContextMenu />
    </MemoryRouter>
  )

  const host = attach(`<span ${RESOLVED_PATH_ATTR}="/work/looky/docs/plan.md">docs/plan.md</span>`)

  fireEvent.contextMenu(host.querySelector('span')!)

  // The rest of the file section still paints — this is a narrowing, not a
  // removal of the whole section.
  expect(await screen.findByText('Copy file path')).toBeTruthy()
  expect(
    screen.queryByText(
      pickRevealLabel(en.fileMenu.revealFinder, en.fileMenu.revealExplorer, en.fileMenu.revealFileManager)
    )
  ).toBeNull()
})

it('withholds the editor entry for a file the OS would execute', async () => {
  // `shell.openPath` launches by association, so "open" on an agent-written
  // `.command` means run it. The rest of the section stays: the file is still
  // previewable and still revealable.
  desktopWindow.hermesDesktop = {
    openExternal: vi.fn().mockResolvedValue(undefined),
    writeClipboard: vi.fn().mockResolvedValue(undefined)
  } as unknown as Window['hermesDesktop']

  render(
    <MemoryRouter>
      <AppContextMenu />
    </MemoryRouter>
  )

  const host = attach(`<span ${RESOLVED_PATH_ATTR}="/work/looky/setup.command">setup.command</span>`)

  fireEvent.contextMenu(host.querySelector('span')!)

  expect(await screen.findByText('Open in Hermes preview')).toBeTruthy()
  expect(screen.getByText('Copy file path')).toBeTruthy()
  expect(screen.queryByText('Open in editor')).toBeNull()
})

it('shows no file section when the click did not land on a resolved path', async () => {
  desktopWindow.hermesDesktop = {
    openExternal: vi.fn().mockResolvedValue(undefined),
    writeClipboard: vi.fn().mockResolvedValue(undefined)
  } as unknown as Window['hermesDesktop']

  render(
    <MemoryRouter>
      <AppContextMenu />
    </MemoryRouter>
  )

  const host = attach('<a href="https://example.com/x">link</a>')

  fireEvent.contextMenu(host.querySelector('a')!)

  expect(await screen.findByText('Copy URL')).toBeTruthy()
  expect(screen.queryByText('Open in editor')).toBeNull()
})
