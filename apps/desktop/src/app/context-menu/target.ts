/**
 * What a right-click landed on, resolved from the DOM.
 *
 * One resolver so every surface agrees on ownership. Order encodes priority:
 * an editable wins over the link wrapping it (the caret is where the user is
 * working), a link wins over the image inside it for the LINK section — the
 * image section still appears because the target carries both.
 */

import { RESOLVED_PATH_ATTR } from '@/components/chat/file-path-candidate'

export interface ContextMenuDomTarget {
  /** The enclosing dialog content node, when the click landed inside one. */
  dialogPortalContainer: HTMLElement | null
  /** The clicked editable, when the click landed in one. */
  editable: HTMLElement | null
  /** Absolute path of the resolved file reference under the cursor, when the
   *  click landed on one. Only paths already PROVEN to exist carry it, so a
   *  menu entry built from this never offers a file that cannot open. */
  filePath: string
  /** `href` of the enclosing anchor, as written (never absolutized). */
  linkUrl: string
  /** Source URL of the clicked image, when the click landed on one. */
  imageUrl: string
  /** True when the click landed on an `<img>` (imageUrl may still be empty
   *  for a broken image; Copy image works through coordinates either way). */
  onImage: boolean
  /** The live selection's text at the moment of the click. */
  selectionText: string
}

/** Form fields and `contenteditable` hosts. Mirrors the keybind helper, but
 *  returns the element so the menu can act on it. */
function editableFrom(element: Element | null): HTMLElement | null {
  if (!element) {
    return null
  }

  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
    return element.disabled || element.readOnly ? null : element
  }

  const host = element.closest('[contenteditable]')

  return host instanceof HTMLElement && host.isContentEditable ? host : null
}

export function resolveDomTarget(element: Element | null): ContextMenuDomTarget {
  const anchor = element?.closest('a[href]')
  const dialogContent = element?.closest('[data-slot="dialog-content"]')
  const image = element?.closest('img')
  const linkUrl = anchor?.getAttribute('href')?.trim() ?? ''
  const fileRef = element?.closest(`[${RESOLVED_PATH_ATTR}]`)

  return {
    dialogPortalContainer: dialogContent instanceof HTMLElement ? dialogContent : null,
    editable: editableFrom(element),
    filePath: fileRef?.getAttribute(RESOLVED_PATH_ATTR)?.trim() ?? '',
    // A placeholder anchor is not a link the menu can act on.
    linkUrl: linkUrl === '#' ? '' : linkUrl,
    imageUrl: image instanceof HTMLImageElement ? image.currentSrc || image.src : '',
    onImage: Boolean(image),
    selectionText: window.getSelection()?.toString().trim() ?? ''
  }
}

/** True when `url` is something the in-app browser can render. */
export function isWebUrl(url: string): boolean {
  return /^https?:\/\//i.test(url)
}
