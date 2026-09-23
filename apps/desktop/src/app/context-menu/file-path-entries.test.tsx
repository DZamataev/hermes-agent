import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { RESOLVED_DIR_ATTR, RESOLVED_PATH_ATTR } from '@/components/chat/file-path-candidate'
import { en } from '@/i18n/en'
import type * as ExternalLink from '@/lib/external-link'
import type * as FileManager from '@/lib/file-manager'
import { pickRevealLabel } from '@/lib/file-manager'
import { $previewTabs, closeRightRail } from '@/store/preview'

import { AppContextMenu } from './app-context-menu'
import { $contextMenu } from './store'
import { resolveDomTarget } from './target'

const opened: string[] = []
const { openedWith, revealed, state } = vi.hoisted(() => ({
  openedWith: [] as string[],
  revealed: [] as string[],
  state: { remote: false, win: false }
}))

vi.mock('@/lib/file-manager', async importOriginal => ({
  ...(await importOriginal<typeof FileManager>()),
  canOpenPathWith: () => state.win
}))

vi.mock('@/lib/external-link', async importOriginal => ({
  ...(await importOriginal<typeof ExternalLink>()),
  openExternalLink: (href: string) => opened.push(href)
}))

vi.mock('@/lib/desktop-fs', () => ({
  copyTextToClipboard: () => Promise.resolve(),
  isDesktopFsRemoteMode: () => state.remote,
  openWithDesktopPath: (path: string) => {
    openedWith.push(path)

    return Promise.resolve({ ok: true })
  },
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
  openedWith.length = 0
  revealed.length = 0
  state.remote = false
  state.win = false
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

it('offers a directory only what a directory can do', async () => {
  // A folder has nothing to render in the rail and nothing to hand a text
  // editor. Reveal is the verb the reference exists for, and Copy path still
  // applies — the other two would be dead entries.
  desktopWindow.hermesDesktop = {
    openExternal: vi.fn().mockResolvedValue(undefined),
    writeClipboard: vi.fn().mockResolvedValue(undefined)
  } as unknown as Window['hermesDesktop']

  render(
    <MemoryRouter>
      <AppContextMenu />
    </MemoryRouter>
  )

  const host = attach(
    `<span ${RESOLVED_PATH_ATTR}="/work/looky/app/features" ${RESOLVED_DIR_ATTR}>app/features/</span>`
  )

  fireEvent.contextMenu(host.querySelector('span')!)

  const reveal = pickRevealLabel(en.fileMenu.revealFinder, en.fileMenu.revealExplorer, en.fileMenu.revealFileManager)

  expect(await screen.findByText(reveal)).toBeTruthy()
  expect(screen.getByText('Copy file path')).toBeTruthy()
  expect(screen.queryByText('Open in Hermes preview')).toBeNull()
  expect(screen.queryByText('Open in editor')).toBeNull()
})

it('offers the OS application picker where the OS has one', async () => {
  state.win = true
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
  fireEvent.click(await screen.findByText('Open with…'))

  await waitFor(() => expect(openedWith).toEqual(['/work/looky/docs/plan.md']))
})

it.each([
  ['the host has no application picker', { win: false }],
  ['the file would be executed rather than opened', { path: '/work/looky/setup.command', win: true }],
  ['the reference is a directory', { dir: true, win: true }],
  ['the path lives on a remote gateway', { remote: true, win: true }]
])('withholds the picker when %s', async (_label, setup: Record<string, unknown>) => {
  // Same withholding rules as the editor entry — an entry that cannot do what
  // its label says is worse than no entry.
  state.win = Boolean(setup.win)
  state.remote = Boolean(setup.remote)
  desktopWindow.hermesDesktop = {
    openExternal: vi.fn().mockResolvedValue(undefined),
    writeClipboard: vi.fn().mockResolvedValue(undefined)
  } as unknown as Window['hermesDesktop']

  render(
    <MemoryRouter>
      <AppContextMenu />
    </MemoryRouter>
  )

  const path = (setup.path as string) ?? '/work/looky/docs/plan.md'
  const host = attach(
    `<span ${RESOLVED_PATH_ATTR}="${path}" ${setup.dir ? RESOLVED_DIR_ATTR : ''}>ref</span>`
  )

  fireEvent.contextMenu(host.querySelector('span')!)

  // Copy path is in every file section, so its presence proves the menu
  // painted and the absence below is a real withholding.
  expect(await screen.findByText('Copy file path')).toBeTruthy()
  expect(screen.queryByText('Open with…')).toBeNull()
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
