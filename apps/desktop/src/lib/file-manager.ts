import { IS_MAC } from '@/lib/keybinds/combo'

/**
 * Naming the OS file manager.
 *
 * "Reveal in Finder" is meaningless on the two other platforms, and Linux has
 * no single manager to name at all, so the label is chosen per host and every
 * menu that reveals a path reads the same way.
 */

export const IS_WIN = typeof navigator !== 'undefined' && /win/i.test(navigator.platform || navigator.userAgent || '')

/** The platform-appropriate "reveal in file manager" label (Finder / Explorer
 *  / containing folder). Shared so every file menu reads consistently. */
export function pickRevealLabel(finder: string, explorer: string, fileManager: string): string {
  return IS_MAC ? finder : IS_WIN ? explorer : fileManager
}

/**
 * True where the OS offers an application PICKER for a file.
 *
 * Windows has one (`shell32.dll,OpenAs_RunDLL`). macOS and Linux do not: the
 * only single-command stand-in is "open with the default app", which is launch
 * by association — a different action than the label promises, and precisely
 * the one withheld from executables. So the entry is absent there rather than
 * quietly doing something else.
 */
export function canOpenPathWith(isWin: boolean = IS_WIN): boolean {
  return isWin
}
