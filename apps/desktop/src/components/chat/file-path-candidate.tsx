import { useStore } from '@nanostores/react'
import { type MouseEvent as ReactMouseEvent, type ReactNode, useEffect, useState } from 'react'

import { useSessionView } from '@/app/chat/session-view'
import { useI18n } from '@/i18n'
import { desktopFsCacheKey, desktopGitRoot, readDesktopDir } from '@/lib/desktop-fs'
import { type ResolvedFilePath, resolveFilePath } from '@/lib/file-path-resolve'
import { normalizeOrLocalPreviewTarget } from '@/lib/local-preview'
import { canOpenPathInEditor, openPathInEditor, wantsExternalEditor } from '@/lib/open-in-editor'
import { cn } from '@/lib/utils'
import { notifyError } from '@/store/notifications'
import { $pathModifierHeld } from '@/store/path-modifier'
import { openPreview } from '@/store/preview'
import { $connection } from '@/store/session'

interface FilePathCandidateProps {
  children: ReactNode
  path: string
}

/** Carries the PROVEN absolute path to the context menu (see `target.ts`). */
export const RESOLVED_PATH_ATTR = 'data-file-resolved'

/** Present on a resolved reference the filesystem reports as a directory. */
export const RESOLVED_DIR_ATTR = 'data-file-directory'

/** Hover long enough to mean it. Sweeping the pointer across a paragraph
 *  crosses many tokens; only a rest probes the filesystem. */
const HOVER_INTENT_MS = 200

/** How long "nothing there" is believed. A miss is a fact about a moment —
 *  the agent names files it is about to write — so it only spares the disk a
 *  burst of repeat hovers and then has to be asked again. */
const MISS_TTL_MS = 3_000

/**
 * Answers already paid for, keyed by the backend and cwd they were taken
 * against.
 *
 * Module scope on purpose: a transcript names the same file repeatedly and
 * remounts rows as it virtualizes, so a per-component cache would re-probe the
 * disk on every scroll. Keying by connection and cwd is what makes an answer
 * from another machine or working directory unreachable rather than something
 * to invalidate. A hit is kept; a miss expires (`MISS_TTL_MS`).
 */
const probed = new Map<string, { expires: number; match: ResolvedFilePath | null }>()

function probeKey(connectionKey: string, cwd: string, path: string) {
  return `${connectionKey}\u0000${cwd}\u0000${path}`
}

/** The remembered answer for *key*, or undefined when there is none to trust. */
function recall(key: string): ResolvedFilePath | null | undefined {
  const known = probed.get(key)

  if (known && known.expires <= Date.now()) {
    probed.delete(key)

    return undefined
  }

  return known?.match
}

/**
 * Ask the filesystem about *path* and remember what it said.
 *
 * A miss is remembered only when every listing actually answered: a directory
 * that could not be read (a dropped connection, a folder not created yet)
 * proves nothing about the file, so that outcome is asked again next time.
 */
async function probe(key: string, path: string, cwd: string): Promise<ResolvedFilePath | null> {
  let unreadable = false

  const match = await resolveFilePath(path, cwd, {
    gitRoot: dir =>
      desktopGitRoot(dir).catch(() => {
        unreadable = true

        return null
      }),
    listDir: async dir => {
      const result = await readDesktopDir(dir).catch(() => null)

      if (!result || result.error) {
        unreadable = true

        return null
      }

      return result.entries.map(entry => ({ isDirectory: entry.isDirectory, name: entry.name, path: entry.path }))
    }
  })

  if (match) {
    probed.set(key, { expires: Infinity, match })
  } else if (!unreadable) {
    probed.set(key, { expires: Date.now() + MISS_TTL_MS, match: null })
  }

  return match
}

/**
 * A file path named in prose, openable once it is known to exist.
 *
 * Renders as ordinary text until the path resolves against this session's cwd
 * AND the user holds the platform modifier — the underline is a statement that
 * a file was found, not a guess from the token's shape. Resolution therefore
 * happens on hover rather than on click: an affordance that opens an error
 * dialog is worse than no affordance.
 *
 * Resting on a token resolves it whether or not the modifier is down, because
 * the context menu has to know what it is offering BEFORE it paints, and a
 * right-click is not preceded by a modifier. The probe is debounced and
 * cached, so reading a paragraph costs nothing and the modifier path finds its
 * answer already waiting.
 */
export function FilePathCandidate({ children, path }: FilePathCandidateProps) {
  const { t } = useI18n()
  // This token lives in one session's transcript; resolve it against THAT
  // session's cwd, not the primary chat's.
  const cwd = useStore(useSessionView().$cwd)
  const armed = useStore($pathModifierHeld)
  // Subscribed, not read: switching backend must drop what this token learned
  // from the previous one's filesystem.
  const key = probeKey(desktopFsCacheKey(useStore($connection)), cwd, path)
  const [resolved, setResolved] = useState<ResolvedFilePath | null>(() => recall(key) ?? null)
  const [hovered, setHovered] = useState(false)

  useEffect(() => {
    const known = recall(key)

    if (known !== undefined) {
      setResolved(known)

      return
    }

    // Nothing is known about this (backend, cwd, path) right now, so the token
    // must not claim anything either until a hover pays for the answer.
    setResolved(null)

    if (!hovered) {
      return
    }

    let cancelled = false

    const timer = setTimeout(() => {
      void probe(key, path, cwd).then(match => {
        if (!cancelled) {
          setResolved(match)
        }
      })
    }, HOVER_INTENT_MS)

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [hovered, key, cwd, path])

  const target = resolved?.path ?? null
  // A directory has nothing to render in the rail, so the click gesture is not
  // offered for one — the context menu still reveals it in the file manager.
  const openable = armed && target !== null && !resolved?.isDirectory

  async function openPreviewTab() {
    if (!target) {
      return
    }

    try {
      const preview = await normalizeOrLocalPreviewTarget(target, cwd || undefined)

      if (!preview) {
        throw new Error(`Could not open preview target: ${target}`)
      }

      openPreview(preview)
    } catch (error) {
      notifyError(error, t.preview.unavailable)
    }
  }

  function activate(event: ReactMouseEvent) {
    if (!target) {
      return
    }

    // Alt sends the path out of the app entirely; without it the rail keeps
    // the file in view beside the conversation. An executable has no way out
    // — `shell.openPath` would run it — so the gesture falls back to the
    // preview rather than doing nothing at all.
    if (wantsExternalEditor(event.nativeEvent) && canOpenPathInEditor(target)) {
      openPathInEditor(target)

      return
    }

    void openPreviewTab()
  }

  return (
    <span
      className={cn(openable && 'cursor-pointer underline decoration-dotted underline-offset-2')}
      data-file-path={path}
      // Present only once the entry is proven to exist: the context menu builds
      // its entries from this, so an unresolved token offers nothing. The
      // directory marker rides along so the menu can drop what a folder cannot
      // do without probing the filesystem a second time.
      {...(target ? { [RESOLVED_PATH_ATTR]: target } : {})}
      {...(target && resolved?.isDirectory ? { [RESOLVED_DIR_ATTR]: '' } : {})}
      onClick={openable ? activate : undefined}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      title={openable ? target || undefined : undefined}
    >
      {children}
    </span>
  )
}
