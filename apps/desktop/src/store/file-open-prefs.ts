import { Codecs, persistentAtom } from '@/lib/persisted'

/**
 * Which application opens a file reference, when the user overrides the OS.
 *
 * `shell.openPath` follows the file association, and on macOS that association
 * is rewritten by installers and toolchain updates often enough that "open in
 * editor" quietly changes destination. This preference is the override.
 *
 * Scope is the DEVICE, not the profile or the connection: it names an
 * application installed on this machine, so it cannot travel with a profile to
 * a laptop where that editor does not exist. Hence plain localStorage rather
 * than `config.yaml` — the same reason the attachment byte cap and terminal
 * font live here.
 */
const EDITOR_APP_STORAGE_KEY = 'hermes.desktop.fileOpenApp'

/** The id meaning "let the OS decide" — the shipped default. */
export const SYSTEM_EDITOR_APP = 'system'

/**
 * Chosen application id, or `system`.
 *
 * Stored as a bare id and validated against the machine's detected set at use
 * time: an editor uninstalled after it was picked falls back to the OS rather
 * than failing the click.
 */
export const $fileOpenApp = persistentAtom(EDITOR_APP_STORAGE_KEY, SYSTEM_EDITOR_APP, Codecs.text)

export function setFileOpenApp(appId: string) {
  $fileOpenApp.set(appId || SYSTEM_EDITOR_APP)
}

/** True when the OS association should be used — no override in effect. */
export function usesSystemEditor(appId: string): boolean {
  return !appId || appId === SYSTEM_EDITOR_APP
}
