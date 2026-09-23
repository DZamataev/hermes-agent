// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { setFileOpenApp, SYSTEM_EDITOR_APP } from '@/store/file-open-prefs'

import { FileOpenAppSetting } from './file-open-app-setting'

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      settings: {
        config: {
          fileOpenAppTitle: 'Default file open destination',
          fileOpenAppDesc: 'Where files open by default.',
          fileOpenAppSystem: 'System default',
          fileOpenAppLabel: 'Default app for opening files',
          fileOpenAppEmpty: 'No supported editors found on this computer.'
        }
      }
    }
  })
}))

const desktopWindow = window as unknown as { hermesDesktop?: Record<string, unknown> }

afterEach(() => {
  cleanup()
  delete desktopWindow.hermesDesktop
  setFileOpenApp(SYSTEM_EDITOR_APP)
})

it('offers the system default plus the editors found on this machine', async () => {
  desktopWindow.hermesDesktop = {
    editorApps: () =>
      Promise.resolve([
        { id: 'zed', label: 'Zed', target: '/Applications/Zed.app' },
        { id: 'vscode', label: 'VS Code', target: '/Applications/Visual Studio Code.app' }
      ])
  }

  render(<FileOpenAppSetting />)

  // The trigger shows the current choice; the OS default is what ships.
  await waitFor(() => expect(screen.getByLabelText('Default app for opening files').textContent).toContain('System default'))
})

it('says so when the machine has no supported editor', async () => {
  // An empty picker with no explanation reads as a broken control.
  desktopWindow.hermesDesktop = { editorApps: () => Promise.resolve([]) }

  render(<FileOpenAppSetting />)

  await waitFor(() => expect(screen.getByText(/No supported editors found/)).toBeTruthy())
})

it('shows the system default when the chosen editor is no longer installed', async () => {
  // Picked once, uninstalled since: the trigger must not render blank, and it
  // must say what will actually happen on a click — the OS association.
  desktopWindow.hermesDesktop = {
    editorApps: () => Promise.resolve([{ id: 'vscode', label: 'VS Code', target: '/Applications/Visual Studio Code.app' }])
  }
  setFileOpenApp('zed')

  render(<FileOpenAppSetting />)

  await waitFor(() => expect(screen.getByLabelText('Default app for opening files').textContent).toContain('System default'))
})

it('keeps the chosen editor selected when it is installed', async () => {
  desktopWindow.hermesDesktop = {
    editorApps: () => Promise.resolve([{ id: 'zed', label: 'Zed', target: '/Applications/Zed.app' }])
  }
  setFileOpenApp('zed')

  render(<FileOpenAppSetting />)

  await waitFor(() => expect(screen.getByLabelText('Default app for opening files').textContent).toContain('Zed'))
})

it('collapses to the system default with no Electron bridge', async () => {
  // Browser dev or a remote-only shell: there is nothing to enumerate.
  render(<FileOpenAppSetting />)

  await waitFor(() => expect(screen.getByText(/No supported editors found/)).toBeTruthy())
})
