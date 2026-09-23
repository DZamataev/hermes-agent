import type { PhrasingContent, Root, RootContent } from 'mdast'

/**
 * Mark file-path-looking tokens in prose so the transcript can offer them.
 *
 * This marks CANDIDATES only: the token keeps rendering as ordinary text, and
 * nothing about the paragraph's appearance changes. Whether a candidate is a
 * real file is decided later, against the filesystem, and only then does the
 * UI offer it (see `file-path-resolve.ts`). Marking and proving are separate
 * because the agent names paths from whatever directory it had in mind, so a
 * token that looks like a path frequently is not one that exists.
 */

// Deliberately conservative: a slash-qualified file, or a familiar standalone
// document name. Domains, commands, and incomplete streaming paths stay prose.
// The extension ceiling is 16 because real project files reach it —
// `.xcworkspacedata` is 15, `.entitlements` 12.
const POSIX_PATH = String.raw`(?:[\p{L}\p{N}_~./-]+\/)[\p{L}\p{N}_.-]+\.[a-z\d]{1,16}`
// A drive path (`C:\dir\file.ts`, `C:/dir/file.ts`) or a UNC share
// (`\\server\share\file.txt`). Both are absolute to the resolver and are
// normalized by the `file://` encoder, so only the marker was keeping Windows
// paths from ever becoming candidates.
const WINDOWS_PATH = String.raw`(?:[a-z]:[\\/]|\\\\[\p{L}\p{N}_.-]+\\)[\p{L}\p{N}_.\\/-]*[\p{L}\p{N}_.-]+\.[a-z\d]{1,16}`
const DOCUMENT_NAME = String.raw`[\p{L}\p{N}_-]+\.(?:md|markdown|mdown|txt|json|yaml|yml|toml|csv|pdf)`

const FILE_PATH = new RegExp(`^(?:${WINDOWS_PATH}|${POSIX_PATH}|${DOCUMENT_NAME})$`, 'iu')

// A path is routinely cited with the line (and column) it was read at —
// `…/VideoMessageBubble.tsx:141`. That suffix belongs to the reference, not to
// the filename, so the token carries it and the candidate does not.
const LINE_SUFFIX = /:\d+(?::\d+)?$/

// Backslashes and the drive colon join the token body for Windows' sake. The
// FILE_PATH test above is what still rejects a `host:port` or a clock time —
// this only decides where a token starts and ends.
const FILE_TOKEN =
  /(^|[\s([“«])([\p{L}\p{N}_~./\\-]+(?::[\\/][\p{L}\p{N}_.\\/-]+)?(?::\d+(?::\d+)?)?\.*)(?=$|[\s)\],;:!?»”])/gu

/** The filename inside a `path:line[:col]` reference. */
function withoutLineSuffix(token: string) {
  return token.replace(LINE_SUFFIX, '')
}

/** Marks the mdast node carrying a candidate path; read by the renderer. */
export const FILE_PATH_NODE_DATA = 'hermesFilePath'

/**
 * The candidate path's carrier, under BOTH names the pipeline gives it.
 *
 * hast names properties in camelCase and only serializes them to `data-*`, so
 * `rehype-raw`'s reparse drops a hyphenated key and the sanitizer's allow-list
 * must be keyed camelCase too. By the time the renderer sees the element it is
 * a React prop again, hyphenated. Both spellings are exported because getting
 * either one wrong makes the marker vanish with no error anywhere.
 */
export const FILE_PATH_PROPERTY = 'dataFilePath'
export const FILE_PATH_ATTR = 'data-file-path'

function candidate(path: string, children: PhrasingContent[]): PhrasingContent {
  return {
    type: 'emphasis',
    // `hName`/`hProperties` retarget the hast element, so this renders as a
    // plain span carrying the path — emphasis is only the mdast carrier and
    // never reaches the DOM as <em>.
    data: {
      [FILE_PATH_NODE_DATA]: path,
      hName: 'span',
      hProperties: { [FILE_PATH_PROPERTY]: path }
    } as PhrasingContent['data'],
    children
  }
}

export function remarkFilePathCandidates() {
  return (tree: Root) => {
    function transform(node: Root | RootContent): void {
      if (!('children' in node) || ['link', 'linkReference'].includes(node.type)) {
        return
      }

      const children: RootContent[] = []

      for (const child of node.children) {
        if (child.type === 'inlineCode' && FILE_PATH.test(withoutLineSuffix(child.value))) {
          children.push(candidate(withoutLineSuffix(child.value), [child]))
        } else if (child.type === 'text') {
          let cursor = 0

          for (const match of child.value.matchAll(FILE_TOKEN)) {
            // The token is what the reader sees and clicks — line suffix
            // included; the candidate is the filename inside it.
            const token = match[2].replace(/\.+$/, '')
            const path = withoutLineSuffix(token)

            if (!FILE_PATH.test(path)) {
              continue
            }

            const start = match.index + match[1].length
            children.push({ type: 'text', value: child.value.slice(cursor, start) })
            children.push(candidate(path, [{ type: 'text', value: token }]))
            cursor = start + token.length
          }

          children.push({ ...child, value: child.value.slice(cursor) })
        } else {
          transform(child)
          children.push(child)
        }
      }

      // All replacements are phrasing nodes at existing phrasing positions.
      node.children = children as typeof node.children
    }

    transform(tree)
  }
}
