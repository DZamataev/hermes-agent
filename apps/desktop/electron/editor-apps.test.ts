import { describe, expect, it } from 'vitest'

import {
  detectEditorApps,
  EDITOR_APPS,
  editorLaunchFor,
  editorLaunchForId,
  type EditorProbeEnvironment,
  resolveEditorApp
} from './editor-apps'

/** A machine where exactly *present* exists. */
function machine(
  platform: string,
  present: string[],
  env: Record<string, string | undefined> = {}
): EditorProbeEnvironment {
  const set = new Set(present)

  return { platform, env, exists: path => set.has(path), macRoots: ['/Applications'] }
}

function spec(id: string) {
  const found = EDITOR_APPS.find(app => app.id === id)

  if (!found) {
    throw new Error(`no catalog entry ${id}`)
  }

  return found
}

describe('resolveEditorApp', () => {
  it('finds a macOS bundle under an applications root', () => {
    const app = resolveEditorApp(spec('zed'), machine('darwin', ['/Applications/Zed.app']))

    expect(app).toEqual({ id: 'zed', label: 'Zed', target: '/Applications/Zed.app' })
  })

  it('reports nothing when the application is not installed', () => {
    expect(resolveEditorApp(spec('zed'), machine('darwin', []))).toBeNull()
  })

  it('prefers the bundle over a PATH shim that may dangle', () => {
    // Both exist: `open -a` reaches the running instance, while `zed` on PATH
    // can be a stale symlink into a bundle that has since moved.
    const app = resolveEditorApp(
      spec('zed'),
      machine('darwin', ['/Applications/Zed.app', '/usr/local/bin/zed'], { PATH: '/usr/local/bin' })
    )

    expect(app?.target).toBe('/Applications/Zed.app')
  })

  it('falls back to PATH when no bundle is installed', () => {
    const app = resolveEditorApp(spec('zed'), machine('darwin', ['/usr/local/bin/zed'], { PATH: '/usr/local/bin' }))

    expect(app?.target).toBe('/usr/local/bin/zed')
  })

  it('resolves a Windows install under its environment root', () => {
    const app = resolveEditorApp(
      spec('vscode'),
      machine('win32', ['C:\\Users\\me\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe'], {
        LOCALAPPDATA: 'C:\\Users\\me\\AppData\\Local'
      })
    )

    expect(app?.target).toContain('Code.exe')
  })

  it('appends Windows executable suffixes when searching PATH', () => {
    // `code` on Windows PATH is `code.cmd`; searching the bare name finds nothing.
    const app = resolveEditorApp(spec('vscode'), machine('win32', ['C:\\tools\\code.cmd'], { PATH: 'C:\\tools' }))

    expect(app?.target).toBe('C:\\tools\\code.cmd')
  })

  it('does not offer a macOS bundle to a Linux machine', () => {
    // The bundle path could exist on a shared mount; it is still unlaunchable here.
    expect(resolveEditorApp(spec('xcode'), machine('linux', ['/Applications/Xcode.app']))).toBeNull()
  })
})

describe('detectEditorApps', () => {
  it('lists only installed applications, in catalog order', () => {
    const apps = detectEditorApps(machine('darwin', ['/Applications/Zed.app', '/Applications/Cursor.app']))

    expect(apps.map(app => app.id)).toEqual(['cursor', 'zed'])
  })

  it('finds nothing on a machine with no editors', () => {
    expect(detectEditorApps(machine('darwin', []))).toEqual([])
  })
})

describe('editorLaunchFor', () => {
  it('opens a macOS bundle through `open -a`, passing the path as its own argument', () => {
    const launch = editorLaunchFor(
      { id: 'zed', label: 'Zed', target: '/Applications/Zed.app' },
      '/tmp/a b;rm -rf x.ts',
      'darwin'
    )

    // argv, never a shell string: the path stays one argument whatever it contains.
    expect(launch).toEqual({ command: 'open', args: ['-a', '/Applications/Zed.app', '/tmp/a b;rm -rf x.ts'] })
  })

  it('runs a plain executable with the file as its argument', () => {
    const launch = editorLaunchFor({ id: 'vscode', label: 'VS Code', target: 'C:\\tools\\code.cmd' }, 'C:\\x.ts', 'win32')

    expect(launch).toEqual({ command: 'C:\\tools\\code.cmd', args: ['C:\\x.ts'] })
  })
})

describe('editorLaunchForId — the id gate', () => {
  it('launches the editor named by a known id', () => {
    const launch = editorLaunchForId('zed', '/w/App.tsx', machine('darwin', ['/Applications/Zed.app']))

    expect(launch).toEqual({ command: 'open', args: ['-a', '/Applications/Zed.app', '/w/App.tsx'] })
  })

  it('refuses an id this machine has no install for', () => {
    // Uninstalled after it was picked: null means the caller falls back to the
    // OS association. Answering "ok" here would report a launch that never
    // happened, and the click would do nothing at all.
    expect(editorLaunchForId('zed', '/w/App.tsx', machine('darwin', []))).toBeNull()
  })

  it('refuses an id no catalog entry defines', () => {
    // The renderer sends a stored string; a stale or tampered value must not
    // reach `spawn`. Only ids this build detected can launch.
    expect(editorLaunchForId('totally-made-up', '/w/App.tsx', machine('darwin', ['/Applications/Zed.app']))).toBeNull()
  })

  it('never treats a path as a command', () => {
    // An id that looks like a shell command is just an unknown id.
    expect(
      editorLaunchForId('/bin/sh -c "rm -rf ~"', '/w/App.tsx', machine('darwin', ['/Applications/Zed.app']))
    ).toBeNull()
  })
})

describe('the catalog itself', () => {
  it('gives every entry a unique id and a way to be found', () => {
    const ids = EDITOR_APPS.map(app => app.id)

    expect(new Set(ids).size).toBe(ids.length)

    for (const app of EDITOR_APPS) {
      // An entry with no probe of any kind can never resolve — it would sit in
      // the picker forever as an option that does nothing.
      expect(
        (app.macApps?.length ?? 0) + (app.winPaths?.length ?? 0) + (app.bins?.length ?? 0),
        `${app.id} has no probe`
      ).toBeGreaterThan(0)
    }
  })
})
