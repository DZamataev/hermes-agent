import type { PhrasingContent, Root, RootContent } from 'mdast'

// Deliberately conservative: a slash-qualified file, or a familiar standalone
// document name. Domains, commands, and incomplete streaming paths stay prose.
const FILE_PATH =
  /^(?:[\p{L}\p{N}_~./-]+\/)[\p{L}\p{N}_.-]+\.[a-z\d]{1,12}$|^[\p{L}\p{N}_-]+\.(?:md|markdown|mdown|txt|json|yaml|yml|toml|csv|pdf)$/iu

const FILE_TOKEN = /(^|[\s([“«])([\p{L}\p{N}_~./-]+)(?=$|[\s)\],;:!?»”])/gu

export const FILE_REFERENCE_PREFIX = '#file-ref/'

function link(path: string, children: PhrasingContent[]): PhrasingContent {
  return { type: 'link', url: FILE_REFERENCE_PREFIX + encodeURIComponent(path), children }
}

export function remarkFileReferences() {
  return (tree: Root) => {
    function transform(node: Root | RootContent): void {
      if (!('children' in node) || ['link', 'linkReference'].includes(node.type)) {
        return
      }

      const children: RootContent[] = []

      for (const child of node.children) {
        if (child.type === 'inlineCode' && FILE_PATH.test(child.value)) {
          children.push(link(child.value, [child]))
        } else if (child.type === 'text') {
          let cursor = 0

          for (const match of child.value.matchAll(FILE_TOKEN)) {
            const path = match[2].replace(/\.+$/, '')

            if (!FILE_PATH.test(path)) {
              continue
            }

            const start = match.index + match[1].length
            children.push({ type: 'text', value: child.value.slice(cursor, start) })
            children.push(link(path, [{ type: 'text', value: path }]))
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
