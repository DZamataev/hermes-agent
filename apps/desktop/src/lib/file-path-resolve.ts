/**
 * Resolve a file path mentioned in chat prose to a real file on disk.
 *
 * The agent names paths the way a human would — relative to the repository
 * root, to a submodule, or to whatever directory it happened to be talking
 * about. Only some of those resolve against the session's cwd, so a path is
 * treated as a candidate and PROVEN before the UI offers it: the hover gesture
 * underlines a token only once a real file has been found behind it.
 *
 * Existence is probed with a directory listing rather than a stat call: that
 * is the one filesystem read the desktop bridge exposes in both local and
 * remote mode, and one listing answers for every candidate in that directory.
 */

export interface FilePathResolverIo {
  /** Names of the entries in *dir*, or null when the directory cannot be read. */
  listDir: (dir: string) => Promise<string[] | null>
  /** Enclosing git repository root of *path*, or null when there is none. */
  gitRoot: (path: string) => Promise<string | null>
}

export interface ResolvedFilePath {
  /** Absolute path of the file that was found. */
  path: string
  /** Which base the candidate resolved against — for tests and diagnostics. */
  base: 'cwd' | 'git-root'
}

function trimTrailingSlashes(value: string) {
  return value.replace(/[/\\]+$/, '')
}

function isAbsolute(path: string) {
  return path.startsWith('/') || /^[a-z]:[\\/]/i.test(path) || path.startsWith('\\\\')
}

function joinPath(base: string, relative: string) {
  const cleanBase = trimTrailingSlashes(base)
  const cleanRelative = relative.replace(/^\.?[/\\]+/, '')

  return cleanBase ? `${cleanBase}/${cleanRelative}` : cleanRelative
}

function splitParent(path: string): { dir: string; name: string } {
  const normalized = path.replace(/\\/g, '/')
  const cut = normalized.lastIndexOf('/')

  return cut <= 0
    ? { dir: cut === 0 ? '/' : '', name: normalized.slice(cut + 1) }
    : { dir: normalized.slice(0, cut), name: normalized.slice(cut + 1) }
}

/**
 * Candidate absolute paths for *rawPath*, nearest-context first.
 *
 * An absolute path is its own only candidate. A relative one is tried against
 * the session's cwd and then against the enclosing git root, which is what
 * makes a repository-root-relative path resolve from a session working inside
 * a subdirectory.
 */
export async function filePathCandidates(
  rawPath: string,
  cwd: string | null | undefined,
  io: Pick<FilePathResolverIo, 'gitRoot'>
): Promise<Array<{ base: ResolvedFilePath['base']; path: string }>> {
  const path = rawPath.trim()

  if (!path) {
    return []
  }

  if (isAbsolute(path)) {
    return [{ base: 'cwd', path }]
  }

  const base = cwd?.trim()

  if (!base) {
    return []
  }

  const candidates: Array<{ base: ResolvedFilePath['base']; path: string }> = [
    { base: 'cwd', path: joinPath(base, path) }
  ]

  const root = await io.gitRoot(base).catch(() => null)

  if (root && trimTrailingSlashes(root) !== trimTrailingSlashes(base)) {
    candidates.push({ base: 'git-root', path: joinPath(root, path) })
  }

  return candidates
}

/**
 * First candidate for *rawPath* that exists as a file, or null.
 *
 * Returning null is the ordinary outcome for a path the agent got wrong, and
 * the caller renders that as plain prose — the token only becomes an
 * affordance once this resolves.
 */
export async function resolveFilePath(
  rawPath: string,
  cwd: string | null | undefined,
  io: FilePathResolverIo
): Promise<ResolvedFilePath | null> {
  const candidates = await filePathCandidates(rawPath, cwd, io)
  const listings = new Map<string, Promise<string[] | null>>()

  for (const candidate of candidates) {
    const { dir, name } = splitParent(candidate.path)

    if (!name) {
      continue
    }

    let listing = listings.get(dir)

    if (!listing) {
      listing = io.listDir(dir).catch(() => null)
      listings.set(dir, listing)
    }

    if ((await listing)?.includes(name)) {
      return { base: candidate.base, path: candidate.path }
    }
  }

  return null
}
