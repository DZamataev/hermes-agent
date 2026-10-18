import { openExternalLink } from '@/lib/external-link'
import { IS_MAC } from '@/lib/keybinds/combo'
import { $fileOpenApp, usesSystemEditor } from '@/store/file-open-prefs'

/**
 * Opening a resolved file path outside the preview rail.
 *
 * The rail renders a file; an editor edits it. By default which editor that is
 * belongs to the OS: a bare absolute path sent through `openExternal` reaches
 * Electron's `shell.openPath`, which dispatches to the user's own file
 * association. (A `file://` URL would not — main only REVEALS those in the
 * file manager.) That is the right default — it needs no setting and it
 * honours whatever the user configured system-wide.
 *
 * It is not sufficient, though, and the reason is not theoretical: on macOS
 * the association for source files is rewritten by installers and toolchain
 * updates, so a user who chose their editor finds "open" landing somewhere
 * else weeks later, with nothing in Hermes to correct it. So the OS stays the
 * default and Settings offers an explicit override (`$fileOpenApp`), which
 * names an application by catalog id — never a command to run.
 */

/**
 * True when a click asked for the external editor rather than the preview.
 *
 * Cmd/Ctrl alone already means "act on this path" (it is what armed the
 * reference); adding Alt is the second, deliberate step that sends it out of
 * the app. Right-click is deliberately NOT this gesture — it belongs to the
 * context menu, which offers both destinations by name.
 */
export function wantsExternalEditor(
  event: Pick<MouseEvent, 'altKey' | 'ctrlKey' | 'metaKey'>,
  isMac: boolean = IS_MAC
): boolean {
  return event.altKey && (isMac ? event.metaKey : event.ctrlKey)
}

/**
 * Absolute path → `file://` URL, percent-encoding each segment.
 *
 * Not what `openPathInEditor` sends: main reveals a `file://` URL in the file
 * manager rather than opening it.
 */
export function fileUrlForPath(path: string): string {
  const isWindowsUnc = path.startsWith('\\\\')
  const normalized = isWindowsUnc || /^[a-z]:[\\/]/i.test(path) ? path.replace(/\\/g, '/') : path

  const encoded = normalized
    .split('/')
    .map(part => encodeURIComponent(part))
    .join('/')

  if (isWindowsUnc) {
    return `file://${encoded.slice(2)}`
  }

  return `file://${encoded.startsWith('/') ? encoded : `/${encoded}`}`
}

/**
 * Extensions the OS launches as a PROGRAM rather than opening as a document.
 *
 * The paths in a transcript are written by the agent, and `shell.openPath`
 * dispatches by file association: for these, "open" means "run". A reference
 * to one stays a reference — it still previews and still reveals — but this
 * one exit out of the app is closed, so a plausible-looking path can never
 * become a click that executes something.
 */
const EXECUTABLE_EXTENSIONS = new Set([
  'app',
  'bat',
  'cmd',
  'com',
  'command',
  'desktop',
  'exe',
  'jar',
  'js',
  'lnk',
  'msi',
  'pif',
  'ps1',
  'scr',
  'sh',
  'vbs',
  'workflow',
  'zsh'
])

/** True when the OS may be handed *path* as a document to open. */
export function canOpenPathInEditor(path: string): boolean {
  const name = path.trim().split(/[\\/]/).pop() ?? ''
  const dot = name.lastIndexOf('.')
  // A leading dot is a dotfile, not an extension (`.bashrc`), and a name with
  // no dot at all (`Makefile`) is an ordinary document.
  const extension = dot > 0 ? name.slice(dot + 1).toLowerCase() : ''

  return !EXECUTABLE_EXTENSIONS.has(extension)
}

/**
 * Hand *path* to the chosen editor, or to the OS when none is chosen.
 *
 * Only absolute paths: a relative one would be resolved against whatever
 * directory Electron happens to have, which is never the session's.
 *
 * A picked editor that is gone (uninstalled since, or a remote connection with
 * no bridge) falls back to the OS association rather than failing the click —
 * the same read-falls-to-the-next-rung shape as the rest of the app.
 */
export function openPathInEditor(path: string): void {
  const absolute = path.trim()

  if (!absolute || !(absolute.startsWith('/') || /^[a-z]:[\\/]/i.test(absolute) || absolute.startsWith('\\\\'))) {
    return
  }

  if (!canOpenPathInEditor(absolute)) {
    return
  }

  // The bare path, not a `file://` URL: main opens the former with the OS
  // handler and only reveals the latter. A doubled leading slash is folded
  // because main reads `//host/…` as a protocol-relative web URL.
  const openWithSystem = () => openExternalLink(absolute.replace(/^\/{2,}/, '/'))
  const appId = $fileOpenApp.get()
  const bridge = window.hermesDesktop?.openInEditorApp

  if (usesSystemEditor(appId) || !bridge) {
    openWithSystem()

    return
  }

  // A rejected call (the handler is missing, the IPC channel died) is the
  // same outcome as an editor that is gone: the click still opens the file.
  void bridge(appId, absolute)
    .then(result => {
      if (!result?.ok) {
        openWithSystem()
      }
    })
    .catch(openWithSystem)
}
