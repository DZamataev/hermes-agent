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
