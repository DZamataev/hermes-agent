import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useState } from 'react'

import { useSessionView } from '@/app/chat/session-view'
import { useI18n } from '@/i18n'
import { desktopGitRoot, readDesktopDir } from '@/lib/desktop-fs'
import { resolveFilePath } from '@/lib/file-path-resolve'
import { normalizeOrLocalPreviewTarget } from '@/lib/local-preview'
import { cn } from '@/lib/utils'
import { notifyError } from '@/store/notifications'
import { $pathModifierHeld } from '@/store/path-modifier'
import { openPreview } from '@/store/preview'

interface FilePathCandidateProps {
  children: ReactNode
  path: string
}

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
 * Renders as ordinary text until the user holds the platform modifier AND the
 * path resolves against this session's cwd — the underline is a statement
 * that a file was found, not a guess from the token's shape. Resolution
 * therefore happens on hover rather than on click: an affordance that opens an
 * error dialog is worse than no affordance.
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
    // anything either until a hover with the modifier pays for the answer.
    setTarget(null)

    if (!armed || !hovered) {
      return
    }

    let cancelled = false

    void resolveFilePath(path, cwd, io).then(match => {
      probed.set(key, match?.path ?? null)

      if (!cancelled) {
        setTarget(match?.path ?? null)
      }
    })

    return () => {
      cancelled = true
    }
  }, [armed, hovered, cwd, path])

  const openable = armed && target !== null

  async function open() {
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

  return (
    <span
      className={cn(openable && 'cursor-pointer underline decoration-dotted underline-offset-2')}
      data-file-path={path}
      onClick={openable ? () => void open() : undefined}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      title={openable ? target || undefined : undefined}
    >
      {children}
    </span>
  )
}
