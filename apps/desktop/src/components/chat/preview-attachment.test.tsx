import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PRIMARY_SESSION_VIEW, SessionViewProvider } from '@/app/chat/session-view'
import { PreviewAttachment } from '@/components/chat/preview-attachment'
import type * as PreviewStore from '@/store/preview'
import { $currentCwd, $selectedStoredSessionId } from '@/store/session'

// #101683 regression coverage: the delivered-file card must branch on the
// target's LOCAL filesystem state — an existing directory opens natively with
// no Download and no broken preview, an existing local file reveals instead of
// re-downloading, a remote-backend file keeps the gateway-backed actions, and a
// missing path reports instead of fabricating a preview tab.

const { isDesktopFsRemoteMode, notifyError, openPreview } = vi.hoisted(() => ({
  isDesktopFsRemoteMode: vi.fn(() => false),
  notifyError: vi.fn(),
  openPreview: vi.fn()
}))

vi.mock('@/lib/desktop-fs', () => ({
  isDesktopFsRemoteMode,
  readDesktopDir: vi.fn(),
  readDesktopFileDataUrl: vi.fn(),
  readDesktopFileText: vi.fn()
}))

vi.mock('@/store/notifications', () => ({ notifyError }))

vi.mock('@/store/preview', async importOriginal => ({
  ...(await importOriginal<typeof PreviewStore>()),
  $previewTabSources: atom<string[]>([]),
  closePreviewForSource: vi.fn(),
  openPreview
}))

const previousDesktop = window.hermesDesktop

function fileTarget(path: string, previewKind = 'text') {
  return {
    kind: 'file' as const,
    label: path.split('/').filter(Boolean).pop() || path,
    path,
    previewKind,
    source: `file://${path}`,
    url: `file://${path}`
  }
}

function mountDesktopStub(normalizePreviewTarget: (target: string, baseDir?: string) => unknown) {
  const openDir = vi.fn(async () => ({ ok: true }))
  const revealPath = vi.fn(async () => true)
  const saveGatewayFile = vi.fn(async (_request: Record<string, unknown>) => ({
    path: '/Users/me/Downloads/report.md',
    saved: true
  }))

  window.hermesDesktop = {
    normalizePreviewTarget: vi.fn(normalizePreviewTarget),
    openDir,
    revealPath,
    saveGatewayFile
  } as never

  return { openDir, revealPath, saveGatewayFile }
}

async function buttons() {
  return (await screen.findAllByRole('button')).map(button => button.textContent)
}

describe('PreviewAttachment local target classification (#101683)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    isDesktopFsRemoteMode.mockReturnValue(false)
  })

  afterEach(() => {
    cleanup()
    $currentCwd.set('')
    $selectedStoredSessionId.set(null)
    window.hermesDesktop = previousDesktop
  })

  it('opens a Windows file:///C:/... directory link natively with no Download or preview action', async () => {
    const { openDir, revealPath } = mountDesktopStub(() => fileTarget('C:/Users/E/reports/historical', 'directory'))

    render(<PreviewAttachment target="file:///C:/Users/E/reports/historical" />)

    const open = await screen.findByRole('button', { name: 'Open containing folder' })
    expect(await buttons()).toEqual(['Open containing folder'])

    await act(async () => {
      fireEvent.click(open)
    })

    expect(openDir).toHaveBeenCalledWith('C:/Users/E/reports/historical')
    expect(revealPath).not.toHaveBeenCalled()
    expect(openPreview).not.toHaveBeenCalled()
  })

  it('opens a POSIX directory link through the native folder action', async () => {
    const { openDir } = mountDesktopStub(() => fileTarget('/work/reports/historical', 'directory'))

    render(<PreviewAttachment target="/work/reports/historical" />)

    const open = await screen.findByRole('button', { name: 'Open containing folder' })

    await act(async () => {
      fireEvent.click(open)
    })

    expect(openDir).toHaveBeenCalledWith('/work/reports/historical')
    expect(openPreview).not.toHaveBeenCalled()
  })

  it('reveals an existing local file in the file manager instead of offering Download', async () => {
    const { openDir, revealPath } = mountDesktopStub(() => fileTarget('/work/report.md'))

    render(<PreviewAttachment target="/work/report.md" />)

    await screen.findByRole('button', { name: 'Open containing folder' })
    expect(await buttons()).toEqual(['Open containing folder', 'Open preview'])

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Open containing folder' }))
    })

    expect(revealPath).toHaveBeenCalledWith('/work/report.md')
    expect(openDir).not.toHaveBeenCalled()
  })

  it('keeps Download and the preview action for a remote-backend file', async () => {
    isDesktopFsRemoteMode.mockReturnValue(true)
    const { openDir, revealPath } = mountDesktopStub(() => fileTarget('/srv/report.md'))

    render(<PreviewAttachment target="/srv/report.md" />)

    expect(await buttons()).toEqual(['Download', 'Open preview'])

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Open preview' }))
    })

    // The remote path is not on this machine: no native folder action fires.
    expect(openDir).not.toHaveBeenCalled()
    expect(revealPath).not.toHaveBeenCalled()
  })

  it('downloads a relative target joined to the session cwd, without a session id', async () => {
    isDesktopFsRemoteMode.mockReturnValue(true)
    const { saveGatewayFile } = mountDesktopStub(() => fileTarget('/srv/report.md'))
    // A stored id must not reach the request: the path alone selects the file.
    $selectedStoredSessionId.set('stored-1')
    $currentCwd.set('/panel/repo')

    render(<PreviewAttachment target="docs/report.md" />)

    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: 'Download' }))
    })

    expect(saveGatewayFile).toHaveBeenCalledWith(expect.objectContaining({ path: '/panel/repo/docs/report.md' }))
    expect(saveGatewayFile.mock.calls[0][0]).not.toHaveProperty('sessionId')
  })

  it('downloads an absolute target unchanged and without a session id', async () => {
    isDesktopFsRemoteMode.mockReturnValue(true)
    const { saveGatewayFile } = mountDesktopStub(() => fileTarget('/srv/report.md'))
    $selectedStoredSessionId.set('stored-1')
    $currentCwd.set('/panel/repo')

    render(<PreviewAttachment target="/srv/shared/report.md" />)

    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: 'Download' }))
    })

    expect(saveGatewayFile).toHaveBeenCalledWith(expect.objectContaining({ path: '/srv/shared/report.md' }))
    expect(saveGatewayFile.mock.calls[0][0]).not.toHaveProperty('sessionId')
  })

  it('joins a relative target to the cwd of its own session view', async () => {
    isDesktopFsRemoteMode.mockReturnValue(true)
    const { saveGatewayFile } = mountDesktopStub(() => fileTarget('/srv/report.md'))
    $currentCwd.set('/primary/repo')

    render(
      <SessionViewProvider value={{ ...PRIMARY_SESSION_VIEW, $cwd: atom('/tile/repo'), kind: 'tile' }}>
        <PreviewAttachment target="docs/report.md" />
      </SessionViewProvider>
    )

    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: 'Download' }))
    })

    expect(saveGatewayFile).toHaveBeenCalledWith(expect.objectContaining({ path: '/tile/repo/docs/report.md' }))
  })

  it('reports a missing path instead of opening a broken preview tab', async () => {
    mountDesktopStub(() => fileTarget('/work/gone.md', 'missing'))

    render(<PreviewAttachment target="/work/gone.md" />)

    const preview = await screen.findByRole('button', { name: 'Preview unavailable' })

    await act(async () => {
      fireEvent.click(preview)
    })

    expect(notifyError).toHaveBeenCalled()
    expect(openPreview).not.toHaveBeenCalled()
  })
})
