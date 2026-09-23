import { afterEach, describe, expect, it, vi } from 'vitest'

import { setFileOpenApp, SYSTEM_EDITOR_APP } from '@/store/file-open-prefs'

import { canOpenPathInEditor, fileUrlForPath, openPathInEditor, wantsExternalEditor } from './open-in-editor'

const opened: string[] = []
const launched: [string, string][] = []

vi.mock('@/lib/external-link', () => ({
  openExternalLink: (href: string) => opened.push(href)
}))

const CLICK = { altKey: false, ctrlKey: false, metaKey: false }

it.each([
  ['macOS', true, 'metaKey' as const, 'ctrlKey' as const],
  ['elsewhere', false, 'ctrlKey' as const, 'metaKey' as const]
])('on %s asks for the editor only when the accelerator is joined by alt', (_label, isMac, accel, other) => {
  // Cmd/Ctrl alone is what arms and opens the preview; alt is the second step.
  expect(wantsExternalEditor({ ...CLICK, altKey: true, [accel]: true }, isMac)).toBe(true)
  expect(wantsExternalEditor({ ...CLICK, [accel]: true }, isMac)).toBe(false)
  expect(wantsExternalEditor({ ...CLICK, altKey: true }, isMac)).toBe(false)
  expect(wantsExternalEditor({ ...CLICK, altKey: true, [other]: true }, isMac)).toBe(false)
  expect(wantsExternalEditor(CLICK, isMac)).toBe(false)
})

it('percent-encodes each path segment so spaces and non-latin names survive', () => {
  expect(fileUrlForPath('/work/my notes/план.md')).toBe('file:///work/my%20notes/%D0%BF%D0%BB%D0%B0%D0%BD.md')
  // The separators stay separators — encoding the whole string would not.
  expect(fileUrlForPath('/a/b/c.md')).toBe('file:///a/b/c.md')
})

it('hands an absolute path to the OS as a file URL', () => {
  opened.length = 0
  openPathInEditor('/work/looky/docs/plan.md')
  expect(opened).toEqual(['file:///work/looky/docs/plan.md'])
})

it('refuses a relative path rather than resolving it against the wrong directory', () => {
  // Electron's cwd is not the session's, so a relative path would open a
  // different file — or none — with no indication that it did.
  opened.length = 0
  openPathInEditor('docs/plan.md')
  openPathInEditor('   ')
  expect(opened).toEqual([])
})

it.each([
  '/work/looky/setup.command',
  '/work/looky/deploy.sh',
  '/work/looky/Tool.app',
  '/work/looky/run.bat',
  '/work/looky/go.lnk',
  '/work/looky/thing.desktop',
  '/work/looky/LOUD.SH'
])('never hands %s to the OS, which would run it', path => {
  // The transcript's paths are written by the agent, and `shell.openPath`
  // launches by file association — for these extensions that is execution,
  // not opening. The reference stays previewable; only this exit is closed.
  opened.length = 0
  expect(canOpenPathInEditor(path)).toBe(false)
  openPathInEditor(path)
  expect(opened).toEqual([])
})

it('still opens the documents and sources a transcript is actually about', () => {
  for (const path of ['/w/plan.md', '/w/App.tsx', '/w/data.json', '/w/shot.png', '/w/Makefile']) {
    expect(canOpenPathInEditor(path)).toBe(true)
  }
})

describe('with an editor chosen in Settings', () => {
  const desktopWindow = window as unknown as { hermesDesktop?: Record<string, unknown> }

  afterEach(() => {
    opened.length = 0
    launched.length = 0
    delete desktopWindow.hermesDesktop
    setFileOpenApp(SYSTEM_EDITOR_APP)
  })

  it('sends the file to that editor instead of the OS association', async () => {
    // The whole point of the setting: macOS rewrites the association, so a
    // chosen editor must win over whatever the OS currently thinks.
    desktopWindow.hermesDesktop = {
      openInEditorApp: (appId: string, path: string) => {
        launched.push([appId, path])

        return Promise.resolve({ ok: true })
      }
    }
    setFileOpenApp('zed')

    openPathInEditor('/work/App.tsx')
    await vi.waitFor(() => expect(launched).toEqual([['zed', '/work/App.tsx']]))

    expect(opened).toEqual([])
  })

  it('falls back to the OS when the chosen editor is gone', async () => {
    // Uninstalled after it was picked: main answers `unavailable`, and the
    // click must still open the file rather than silently doing nothing.
    desktopWindow.hermesDesktop = {
      openInEditorApp: (appId: string, path: string) => {
        launched.push([appId, path])

        return Promise.resolve({ ok: false, error: 'unavailable' })
      }
    }
    setFileOpenApp('zed')

    openPathInEditor('/work/App.tsx')
    await vi.waitFor(() => expect(opened).toEqual(['file:///work/App.tsx']))
  })

  it('uses the OS association when no bridge exists', () => {
    // A remote/browser shell has no Electron bridge; the setting cannot apply.
    setFileOpenApp('zed')

    openPathInEditor('/work/App.tsx')

    expect(opened).toEqual(['file:///work/App.tsx'])
    expect(launched).toEqual([])
  })

  it('never routes an executable to the chosen editor either', () => {
    // The executable gate runs BEFORE the editor branch, so picking an editor
    // must not reopen the path that "open" means "run".
    desktopWindow.hermesDesktop = {
      openInEditorApp: (appId: string, path: string) => {
        launched.push([appId, path])

        return Promise.resolve({ ok: true })
      }
    }
    setFileOpenApp('zed')

    openPathInEditor('/work/deploy.sh')

    expect(launched).toEqual([])
    expect(opened).toEqual([])
  })
})
