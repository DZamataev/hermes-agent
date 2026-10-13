import { useStore } from '@nanostores/react'
import { type MouseEvent as ReactMouseEvent, type ReactNode, useCallback, useEffect, useRef, useState } from 'react'

import { useSessionView } from '@/app/chat/session-view'
import { useI18n } from '@/i18n'
import { desktopFsCacheKey, desktopGitRoot, isDesktopFsRemoteMode, readDesktopDir } from '@/lib/desktop-fs'
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

/** On every candidate token, proven or not: the path as the prose wrote it. */
export const CANDIDATE_PATH_ATTR = 'data-file-path'

/** Carries the PROVEN absolute path to the context menu (see `target.ts`). */
export const RESOLVED_PATH_ATTR = 'data-file-resolved'

/** Present on a resolved reference the filesystem reports as a directory. */
export const RESOLVED_DIR_ATTR = 'data-file-directory'

/** Hover long enough to mean it. Sweeping the pointer across a paragraph
 *  crosses many tokens; only a rest probes the filesystem. A gesture that has
 *  already stated its intent — the modifier held, a button pressed — does not
 *  wait this out. */
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

/** Probes on their way, so a click that lands mid-probe waits for the same
 *  answer instead of asking the disk a second time. */
const inflight = new Map<string, Promise<ResolvedFilePath | null>>()

/** The answer for *key*: remembered, already on its way, or asked for now. */
function ensureProbe(key: string, path: string, cwd: string): Promise<ResolvedFilePath | null> {
  const known = recall(key)

  if (known !== undefined) {
    return Promise.resolve(known)
  }

  let asking = inflight.get(key)

  if (!asking) {
    asking = probe(key, path, cwd)
      .catch(() => null)
      .finally(() => inflight.delete(key))
    inflight.set(key, asking)
  }

  return asking
}

/** Mounted tokens → how to get their answer, for a gesture that reaches the
 *  context menu before the token itself has one (see below). */
const references = new WeakMap<Element, () => Promise<ResolvedFilePath | null> | null>()

/**
 * The answer for the candidate token under *element*, asked for now if need be.
 *
 * Null when there is nothing to wait for: no token there, or one whose path
 * was just found missing. The context menu calls this for a right-click that
 * beat the hover probe — the DOM carries no verdict yet, so the menu waits on
 * this instead of painting as though the path were plain prose.
 */
export function probeFileReferenceAt(element: Element | null): Promise<ResolvedFilePath | null> | null {
  const token = element?.closest(`[${CANDIDATE_PATH_ATTR}]`)

  return (token && references.get(token)?.()) ?? null
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
 *
 * A quick hand beats that rest, so the gestures themselves also ask: pressing
 * a button probes at once, and a click or a context menu that arrives before
 * the answer waits for it rather than doing nothing.
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
  // Tagged with the key it answers, so an answer for a previous cwd or backend
  // is simply not this token's answer — nothing to reset when either changes.
  const [answer, setAnswer] = useState<{ key: string; match: ResolvedFilePath | null } | null>(null)
  const [hovered, setHovered] = useState(false)
  const ref = useRef<HTMLSpanElement>(null)
  const resolved = answer?.key === key ? answer.match : (recall(key) ?? null)

  const ask = useCallback(() => {
    const asking = ensureProbe(key, path, cwd)

    void asking.then(match =>
      setAnswer(current => (current?.key === key && current.match === match ? current : { key, match }))
    )

    return asking
  }, [key, path, cwd])

  useEffect(() => {
    if (!hovered) {
      return
    }

    // With the modifier already down the pointer is looking for something to
    // open; making it rest first is what let a quick click land on nothing.
    if (armed) {
      void ask()

      return
    }

    const timer = setTimeout(() => void ask(), HOVER_INTENT_MS)

    return () => clearTimeout(timer)
  }, [hovered, armed, ask])

  useEffect(() => {
    const node = ref.current

    if (!node) {
      return
    }

    // A remembered miss is an answer already; only an unknown is worth a wait.
    references.set(node, () => (recall(key) === null ? null : ask()))

    return () => void references.delete(node)
  }, [key, ask])

  const target = resolved?.path ?? null
  // A directory has nothing to render in the rail, so the click gesture is not
  // offered for one — the context menu still reveals it in the file manager.
  const openable = armed && target !== null && !resolved?.isDirectory

  async function openPreviewTab(file: string) {
    try {
      const preview = await normalizeOrLocalPreviewTarget(file, cwd || undefined)

      if (!preview) {
        throw new Error(`Could not open preview target: ${file}`)
      }

      openPreview(preview)
    } catch (error) {
      notifyError(error, t.preview.unavailable)
    }
  }

  function open(match: ResolvedFilePath, external: boolean) {
    if (match.isDirectory) {
      return
    }

    // Alt sends the path out of the app entirely; without it the rail keeps
    // the file in view beside the conversation. An executable has no way out
    // — `shell.openPath` would run it — and neither has a path on a remote
    // gateway, which this machine's editor cannot reach; both fall back to
    // the preview rather than doing nothing at all.
    if (external && !isDesktopFsRemoteMode() && canOpenPathInEditor(match.path)) {
      openPathInEditor(match.path)

      return
    }

    void openPreviewTab(match.path)
  }

  function activate(event: ReactMouseEvent) {
    // The store, not the last paint: the gesture is the modifier being down
    // NOW. Without it a click on a path is a click on prose.
    if (!$pathModifierHeld.get()) {
      return
    }

    // Read before any await — the event does not outlive the handler.
    const external = wantsExternalEditor(event.nativeEvent)

    if (resolved) {
      open(resolved, external)

      return
    }

    // Clicked before the probe answered (or before it was even asked): wait
    // for the verdict and act on it, instead of dropping the click.
    void ask().then(match => {
      if (match) {
        open(match, external)
      }
    })
  }

  // A press that can end in "act on this path" — a secondary button, or the
  // primary one with the modifier down — asks immediately, so the click or
  // menu that follows a few milliseconds later has the least left to wait for.
  function prime(event: ReactMouseEvent) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey) {
      void ask()
    }
  }

  return (
    <span
      className={cn(openable && 'cursor-pointer underline decoration-dotted underline-offset-2')}
      {...{ [CANDIDATE_PATH_ATTR]: path }}
      // Present only once the entry is proven to exist: the context menu builds
      // its entries from this, so an unresolved token offers nothing. The
      // directory marker rides along so the menu can drop what a folder cannot
      // do without probing the filesystem a second time.
      {...(target ? { [RESOLVED_PATH_ATTR]: target } : {})}
      {...(target && resolved?.isDirectory ? { [RESOLVED_DIR_ATTR]: '' } : {})}
      onClick={activate}
      onMouseDown={prime}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      ref={ref}
      title={openable ? target || undefined : undefined}
    >
      {children}
    </span>
  )
}
