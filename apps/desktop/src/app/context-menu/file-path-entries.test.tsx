import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { RESOLVED_PATH_ATTR } from '@/components/chat/file-path-candidate'
import type * as ExternalLink from '@/lib/external-link'
import { $previewTabs, closeRightRail } from '@/store/preview'

import { AppContextMenu } from './app-context-menu'
import { $contextMenu } from './store'
import { resolveDomTarget } from './target'

const opened: string[] = []

vi.mock('@/lib/external-link', async importOriginal => ({
  ...(await importOriginal<typeof ExternalLink>()),
  openExternalLink: (href: string) => opened.push(href)
}))

vi.mock('@/lib/desktop-fs', () => ({
  isDesktopFsRemoteMode: () => false,
  readDesktopFileText: () => Promise.resolve({ binary: false, language: 'markdown', text: '# plan' })
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
