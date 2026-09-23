import { posix as pathPosix, win32 as pathWin32 } from 'node:path'

/** Path flavour for the platform being probed, not for the host running this. */
function flavour(platform: string) {
  return platform === 'win32' ? pathWin32 : pathPosix
}

/**
 * Which application opens a file the agent referenced.
 *
 * `shell.openPath` dispatches through the OS file association, which sounds
 * like the right answer until you watch it in practice: on macOS the
 * association for a source file is rewritten by every installer and Xcode
 * update, so "open" silently changes destination and the user's editor stops
 * being the one they chose. This module is the opt-in override — a picked
 * application, remembered on the device.
 *
 * The security shape matters more than the feature. The renderer never names
 * a command: it sends an **id from this catalog**, and only main turns an id
 * into an executable. A free-text "editor command" setting would be a stored
 * shell string that every future file click executes — the `$EDITOR` RCE
 * shape the repository already refuses (`hermes_cli/config.py` keeps EDITOR
 * out of `env_passthrough` for exactly this reason). An unknown id resolves to
 * nothing and the caller falls back to the OS.
 */

/** A GUI editor Hermes can hand a file to, and how to find it per platform. */
export interface EditorAppSpec {
  /** Stable id: what the renderer stores and sends. Never a path. */
  id: string
  label: string
  /** macOS bundle names, tried under each applications root in order. */
  macApps?: readonly string[]
  /** Windows executables, each relative to an environment root. */
  winPaths?: readonly { env: string; path: string }[]
  /** Executables looked up on PATH (Linux, and Homebrew CLI shims). */
  bins?: readonly string[]
}

/**
 * The offered applications. Deliberately a closed list of editors people
 * actually point Hermes at — not an enumeration of everything installed,
 * which would turn the picker into a launcher and invite "open this .sh in
 * Terminal" back in through the front door.
 */
export const EDITOR_APPS: readonly EditorAppSpec[] = [
  {
    id: 'vscode',
    label: 'VS Code',
    macApps: ['Visual Studio Code.app'],
    winPaths: [
      { env: 'LOCALAPPDATA', path: 'Programs/Microsoft VS Code/Code.exe' },
      { env: 'PROGRAMFILES', path: 'Microsoft VS Code/Code.exe' }
    ],
    bins: ['code']
  },
  {
    id: 'cursor',
    label: 'Cursor',
    macApps: ['Cursor.app'],
    winPaths: [{ env: 'LOCALAPPDATA', path: 'Programs/cursor/Cursor.exe' }],
    bins: ['cursor']
  },
  { id: 'zed', label: 'Zed', macApps: ['Zed.app'], bins: ['zed'] },
  {
    id: 'windsurf',
    label: 'Windsurf',
    macApps: ['Windsurf.app'],
    winPaths: [{ env: 'LOCALAPPDATA', path: 'Programs/Windsurf/Windsurf.exe' }],
    bins: ['windsurf']
  },
  {
    id: 'sublime',
    label: 'Sublime Text',
    macApps: ['Sublime Text.app'],
    winPaths: [{ env: 'PROGRAMFILES', path: 'Sublime Text/sublime_text.exe' }],
    bins: ['subl']
  },
  {
    id: 'webstorm',
    label: 'WebStorm',
    macApps: ['WebStorm.app'],
    bins: ['webstorm']
  },
  {
    id: 'pycharm',
    label: 'PyCharm',
    macApps: ['PyCharm.app', 'PyCharm CE.app'],
    bins: ['pycharm', 'charm']
  },
  {
    id: 'intellij',
    label: 'IntelliJ IDEA',
    macApps: ['IntelliJ IDEA.app', 'IntelliJ IDEA CE.app'],
    bins: ['idea']
  },
  {
    id: 'notepadpp',
    label: 'Notepad++',
    winPaths: [
      { env: 'PROGRAMFILES', path: 'Notepad++/notepad++.exe' },
      { env: 'PROGRAMFILES(X86)', path: 'Notepad++/notepad++.exe' }
    ]
  },
  { id: 'xcode', label: 'Xcode', macApps: ['Xcode.app'] },
  { id: 'androidstudio', label: 'Android Studio', macApps: ['Android Studio.app'], bins: ['studio'] }
]

/** The id meaning "whatever the OS associates" — the shipped default. */
export const SYSTEM_EDITOR_ID = 'system'

/** What a detected application is, and how to start it. */
export interface ResolvedEditorApp {
  id: string
  label: string
  /** Absolute path to the bundle (macOS) or executable (Windows/Linux). */
  target: string
}

export interface EditorProbeEnvironment {
  platform: string
  /** Environment used for Windows roots and PATH. */
  env: Record<string, string | undefined>
  /** True when the path exists. Injected so detection is testable. */
  exists: (path: string) => boolean
  /** Extra macOS applications roots; defaults cover system and per-user. */
  macRoots?: readonly string[]
}

function macApplicationRoots(env: EditorProbeEnvironment): readonly string[] {
  if (env.macRoots) {
    return env.macRoots
  }

  const home = env.env.HOME

  return home ? ['/Applications', pathPosix.join(home, 'Applications')] : ['/Applications']
}

/** First existing PATH entry for *bin*, or null. Windows gets `.exe`/`.cmd`. */
function findOnPath(bin: string, env: EditorProbeEnvironment): null | string {
  const raw = env.env.PATH ?? env.env.Path

  if (!raw) {
    return null
  }

  const suffixes = env.platform === 'win32' ? ['.exe', '.cmd', '.bat', ''] : ['']
  const p = flavour(env.platform)

  for (const dir of raw.split(env.platform === 'win32' ? ';' : ':')) {
    if (!dir) {
      continue
    }

    for (const suffix of suffixes) {
      const candidate = p.join(dir, `${bin}${suffix}`)

      if (env.exists(candidate)) {
        return candidate
      }
    }
  }

  return null
}

/**
 * Locate one catalog entry on this machine, or null when it is not installed.
 *
 * A macOS bundle is preferred over a PATH shim: `open -a` hands the file to
 * the running instance, while a CLI shim (`code`) may be a stale symlink into
 * a bundle that was moved or deleted.
 */
export function resolveEditorApp(spec: EditorAppSpec, env: EditorProbeEnvironment): null | ResolvedEditorApp {
  const found = (target: string): ResolvedEditorApp => ({ id: spec.id, label: spec.label, target })

  if (env.platform === 'darwin') {
    for (const root of macApplicationRoots(env)) {
      for (const app of spec.macApps ?? []) {
        const candidate = pathPosix.join(root, app)

        if (env.exists(candidate)) {
          return found(candidate)
        }
      }
    }
  }

  if (env.platform === 'win32') {
    for (const entry of spec.winPaths ?? []) {
      const root = env.env[entry.env]

      if (!root) {
        continue
      }

      const candidate = pathWin32.join(root, entry.path)

      if (env.exists(candidate)) {
        return found(candidate)
      }
    }
  }

  for (const bin of spec.bins ?? []) {
    const candidate = findOnPath(bin, env)

    if (candidate) {
      return found(candidate)
    }
  }

  return null
}

/** Every catalog application installed on this machine, in catalog order. */
export function detectEditorApps(env: EditorProbeEnvironment): ResolvedEditorApp[] {
  return EDITOR_APPS.map(spec => resolveEditorApp(spec, env)).filter(
    (app): app is ResolvedEditorApp => app !== null
  )
}

/** How to launch a resolved application with a file. */
export interface EditorLaunch {
  command: string
  args: string[]
}

/**
 * The launch for *appId* on this machine, or null when it cannot be honoured.
 *
 * This is the id gate, kept as a pure function so it is tested directly: an id
 * that is not in the detected set resolves to nothing, and the caller falls
 * back to the OS association instead of launching something else. Returning a
 * launch for an unknown id would mean the renderer's string decided what runs.
 */
export function editorLaunchForId(
  appId: string,
  filePath: string,
  env: EditorProbeEnvironment
): null | EditorLaunch {
  const app = detectEditorApps(env).find(candidate => candidate.id === appId)

  if (!app) {
    return null
  }

  return editorLaunchFor(app, filePath, env.platform)
}

/**
 * The command that opens *filePath* in *app*.
 *
 * macOS goes through `open -a`, which reuses a running instance and does not
 * tie the editor's lifetime to ours; elsewhere the executable takes the path
 * directly. Arguments are passed as an argv array — never a shell string — so
 * a path containing spaces, quotes or `;` cannot become another command.
 */
export function editorLaunchFor(app: ResolvedEditorApp, filePath: string, platform: string): EditorLaunch {
  if (platform === 'darwin' && app.target.endsWith('.app')) {
    return { command: 'open', args: ['-a', app.target, filePath] }
  }

  return { command: app.target, args: [filePath] }
}
