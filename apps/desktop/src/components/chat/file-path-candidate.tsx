import { useStore } from '@nanostores/react'
import { type MouseEvent as ReactMouseEvent, type ReactNode, useEffect, useState } from 'react'

import { useSessionView } from '@/app/chat/session-view'
import { useI18n } from '@/i18n'
import { desktopGitRoot, readDesktopDir } from '@/lib/desktop-fs'
import { resolveFilePath } from '@/lib/file-path-resolve'
import { normalizeOrLocalPreviewTarget } from '@/lib/local-preview'
import { openPathInEditor, wantsExternalEditor } from '@/lib/open-in-editor'
import { cn } from '@/lib/utils'
import { notifyError } from '@/store/notifications'
import { $pathModifierHeld } from '@/store/path-modifier'
import { openPreview } from '@/store/preview'

interface FilePathCandidateProps {
  children: ReactNode
  path: string
}

/** Carries the PROVEN absolute path to the context menu (see `target.ts`). */
export const RESOLVED_PATH_ATTR = 'data-file-resolved'

/** Hover long enough to mean it. Sweeping the pointer across a paragraph
 *  crosses many tokens; only a rest probes the filesystem. */
const HOVER_INTENT_MS = 200

const io = {
  gitRoot: (path: string) => desktopGitRoot(path),
  listDir: async (dir: string) => {
    const result = await readDesktopDir(dir)

    return result.error ? null : result.entries.map(entry => entry.name)
  }
}

/**
 * Answers already paid for, keyed by the cwd they were taken against.
 *
 * Module scope on purpose: a transcript names the same file repeatedly and
 * remounts rows as it virtualizes, so a per-component cache would re-probe the
 * disk on every scroll. Keying by cwd is what makes an answer from another
 * working directory unreachable rather than something to invalidate.
 */
const probed = new Map<string, string | null>()

function probeKey(cwd: string, path: string) {
  return `${cwd}\u0000${path}`
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
  const [target, setTarget] = useState<string | null>(() => probed.get(probeKey(cwd, path)) ?? null)
  const [hovered, setHovered] = useState(false)

  useEffect(() => {
    const key = probeKey(cwd, path)
    const known = probed.get(key)

    if (known !== undefined) {
      setTarget(known)

      return
    }

    // Nothing is known about this (cwd, path) yet, so the token must not claim
    // anything either until a hover pays for the answer.
    setTarget(null)

    if (!hovered) {
      return
    }

    let cancelled = false

    const timer = setTimeout(() => {
      void resolveFilePath(path, cwd, io).then(match => {
        probed.set(key, match?.path ?? null)

        if (!cancelled) {
          setTarget(match?.path ?? null)
        }
      })
    }, HOVER_INTENT_MS)

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [hovered, cwd, path])

  const openable = armed && target !== null

  async function openPreviewTab() {
    if (!target) {
      return
    }

    try {
      const preview = await normalizeOrLocalPreviewTarget(target, cwd || undefined)

      if (!preview) {
        throw new Error(`Could not open preview target: ${target}`)
      }

      openPreview(preview, 'explicit-link')
    } catch (error) {
      notifyError(error, t.preview.unavailable)
    }
  }

  function activate(event: ReactMouseEvent) {
    if (!target) {
      return
    }

    // Alt sends the path out of the app entirely; without it the rail keeps
    // the file in view beside the conversation.
    if (wantsExternalEditor(event.nativeEvent)) {
      openPathInEditor(target)

      return
    }

    void openPreviewTab()
  }

  return (
    <span
      className={cn(openable && 'cursor-pointer underline decoration-dotted underline-offset-2')}
      data-file-path={path}
      // Present only once the file is proven to exist: the context menu builds
      // its entries from this, so an unresolved token offers nothing.
      {...(target ? { [RESOLVED_PATH_ATTR]: target } : {})}
      onClick={openable ? activate : undefined}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      title={openable ? target || undefined : undefined}
    >
      {children}
    </span>
  )
}
