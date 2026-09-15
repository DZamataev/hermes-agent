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
const FILE_PATH =
  /^(?:[\p{L}\p{N}_~./-]+\/)[\p{L}\p{N}_.-]+\.[a-z\d]{1,12}$|^[\p{L}\p{N}_-]+\.(?:md|markdown|mdown|txt|json|yaml|yml|toml|csv|pdf)$/iu

const FILE_TOKEN = /(^|[\s([“«])([\p{L}\p{N}_~./-]+)(?=$|[\s)\],;:!?»”])/gu

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
        if (child.type === 'inlineCode' && FILE_PATH.test(child.value)) {
          children.push(candidate(child.value, [child]))
        } else if (child.type === 'text') {
          let cursor = 0

          for (const match of child.value.matchAll(FILE_TOKEN)) {
            const path = match[2].replace(/\.+$/, '')

            if (!FILE_PATH.test(path)) {
              continue
            }

            const start = match.index + match[1].length
            children.push({ type: 'text', value: child.value.slice(cursor, start) })
            children.push(candidate(path, [{ type: 'text', value: path }]))
            cursor = start + path.length
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
