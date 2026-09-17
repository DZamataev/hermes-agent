import { openExternalLink } from '@/lib/external-link'
import { IS_MAC } from '@/lib/keybinds/combo'

/**
 * Opening a resolved file path outside the preview rail.
 *
 * The rail renders a file; an editor edits it, and which editor that is
 * belongs to the OS, not to Hermes. `file://` already routes through
 * Electron's `shell.openPath`, which dispatches to the user's own file
 * association and reveals the file in the file manager when nothing claims
 * it — so a path opens in whatever the user picked for that type, and
 * Hermes ships no editor setting to keep in sync with reality.
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

/** Absolute path → `file://` URL, percent-encoding each segment. */
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
 * Hand *path* to the OS, which opens it in the associated application.
 *
 * Only absolute paths: a relative one would be resolved against whatever
 * directory Electron happens to have, which is never the session's.
 */
export function openPathInEditor(path: string): void {
  const absolute = path.trim()

  if (!absolute || !(absolute.startsWith('/') || /^[a-z]:[\\/]/i.test(absolute) || absolute.startsWith('\\\\'))) {
    return
  }

  openExternalLink(fileUrlForPath(absolute))
}
