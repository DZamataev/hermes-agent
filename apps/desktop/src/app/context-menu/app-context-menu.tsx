import { useStore } from '@nanostores/react'
import type { ReactNode } from 'react'
import { useEffect } from 'react'

import { terminalMenuHandleFor } from '@/app/right-sidebar/terminal/terminal-context-menu'
import { openStarMapNodeMenuFor } from '@/app/starmap/context-menu-handle'
import { probeFileReferenceAt } from '@/components/chat/file-path-candidate'
import { DROPDOWN_KIT } from '@/components/ui/actions-menu'
import { Codicon } from '@/components/ui/codicon'
import { HERMES_CONTEXT_MENU_TRIGGER_ATTR } from '@/components/ui/context-menu'
import { writeClipboardText } from '@/components/ui/copy-button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { type Translations, useI18n } from '@/i18n'
import { isDesktopFsRemoteMode } from '@/lib/desktop-fs'
import { hostPathLabel, hudForcesNativeLinks, normalizeExternalUrl, openExternalLink } from '@/lib/external-link'
import { canOpenPathWith, pickRevealLabel } from '@/lib/file-manager'
import { formatCombo } from '@/lib/keybinds/combo'
import { normalizeOrLocalPreviewTarget } from '@/lib/local-preview'
import { isRemoteGateway } from '@/lib/media'
import { canOpenPathInEditor, openPathInEditor } from '@/lib/open-in-editor'
import { reachablePreviewUrl } from '@/lib/preview-reach'
import { openFileWith, revealFile } from '@/store/file-actions'
import { openPreview } from '@/store/preview'

import { ShellMenuItems } from './shell-menu-items'
import {
  $contextMenu,
  augmentSpellcheck,
  closeContextMenu,
  type OpenContextMenu,
  openDomContextMenu,
  openTerminalContextMenu
} from './store'
import { isWebUrl, resolveDomTarget } from './target'

/** Marks a surface that owns PLAIN right-clicks itself (the user-message
 *  reaction bubble). Owned targets inside it — links, images, editables,
 *  selections — still get the app menu. */
export const CONTEXT_MENU_SKIP_ATTR = 'data-context-menu-skip'

/** How long a right-click waits to learn whether the path under it exists.
 *  A local listing answers in milliseconds; past this (a slow gateway) the
 *  ordinary menu opens rather than leaving the click visibly unanswered. */
const FILE_PROBE_WAIT_MS = 1_000

const LOOPBACK_HOST_RE = /^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[?::1\]?)$/i

// Accelerators shown beside the edit verbs. Display only — Chromium's
// before-input-event handling already executes the chords; the menu just
// advertises them the way the native menu did. `formatCombo` renders
// mod as ⌘ on macOS and Ctrl elsewhere.
const EDIT_SHORTCUTS = {
  copy: formatCombo('mod+c'),
  cut: formatCombo('mod+x'),
  paste: formatCombo('mod+v'),
  selectAll: formatCombo('mod+a')
} as const

function isLoopbackUrl(url: string): boolean {
  try {
    return LOOPBACK_HOST_RE.test(new URL(url).hostname)
  } catch {
    return false
  }
}

/** An item: codicon + label, or label + faded right-aligned shortcut. Edit
 *  verbs (cut/copy/paste/select all) drop the icon and show the accelerator
 *  instead, like the native menus they replaced. */
function Item({
  disabled,
  icon,
  label,
  onSelect,
  shortcut
}: {
  disabled?: boolean
  icon?: string
  label: string
  onSelect: () => void
  shortcut?: string
}) {
  return (
    <DropdownMenuItem disabled={disabled} onSelect={onSelect}>
      {icon ? <Codicon name={icon} size="0.875rem" /> : null}
      <span>{label}</span>
      {shortcut ? <DropdownMenuShortcut>{shortcut}</DropdownMenuShortcut> : null}
    </DropdownMenuItem>
  )
}

function terminalSections(open: Extract<OpenContextMenu, { kind: 'terminal' }>, t: Translations): ReactNode[][] {
  const { terminal } = open
  const selection = terminal.getSelection()

  return [
    [
      selection ? (
        <Item
          icon="copy"
          key="terminal-copy"
          label={t.common.copy}
          onSelect={() => void writeClipboardText(selection)}
        />
      ) : null,
      terminal.paste ? (
        <Item
          disabled={!open.clipboardHasText}
          icon="clippy"
          key="terminal-paste"
          label={t.contextMenu.edit.paste}
          onSelect={() =>
            void window.hermesDesktop?.readClipboard().then(text => (text ? terminal.paste?.(text) : undefined))
          }
        />
      ) : null,
      <Item
        icon="list-selection"
        key="terminal-select-all"
        label={t.contextMenu.edit.selectAll}
        onSelect={() => terminal.selectAll()}
      />
    ].filter(Boolean)
  ]
}

function domSections(open: Extract<OpenContextMenu, { kind: 'dom' }>, t: Translations): ReactNode[][] {
  const copy = t.contextMenu
  const { spellcheck, target } = open
  const sections: ReactNode[][] = []
  const linkUrl = target.linkUrl ? normalizeExternalUrl(target.linkUrl) : ''
  const linkIsWeb = isWebUrl(linkUrl)
  const imageIsWeb = isWebUrl(target.imageUrl)
  const openInApp = !hudForcesNativeLinks()
  const showResolvedCopy = linkIsWeb && isRemoteGateway() && isLoopbackUrl(linkUrl)

  // The edit verbs and spell-check actions act on the sender's FOCUSED
  // element in main. Focus cannot be restored while the menu is open: the
  // radix content is a focus trap, so a focus() here is immediately stolen
  // back, the command then runs against `body`, and select-all grabs the
  // WHOLE transcript instead of the field. Close the menu first, then focus
  // and dispatch on the next frame — after the trap is unmounted.
  const withEditableFocus = (action: () => void) => {
    const editable = target.editable

    closeContextMenu()
    requestAnimationFrame(() => {
      editable?.focus()
      action()
    })
  }

  const editableCommand = (command: 'copy' | 'cut' | 'paste') => {
    withEditableFocus(() => void window.hermesDesktop?.contextMenuEdit?.(command))
  }

  // Select all runs entirely in the renderer, scoped to the editable itself.
  // Main's selectAll acts on whatever the FOCUSED FRAME considers "all" and
  // has no notion of the field the menu was opened on — with any focus slip
  // (the edit composer re-parents focus on blur) it selected the whole
  // transcript. A renderer range cannot escape the field.
  const selectAllInEditable = () => {
    withEditableFocus(() => {
      const editable = target.editable

      if (editable instanceof HTMLInputElement || editable instanceof HTMLTextAreaElement) {
        editable.select()

        return
      }

      if (editable) {
        const range = document.createRange()

        range.selectNodeContents(editable)

        const selection = window.getSelection()

        selection?.removeAllRanges()
        selection?.addRange(range)
      }
    })
  }

  const spellcheckAction = (action: { kind: 'add' | 'replace'; word: string }) => {
    withEditableFocus(() => void window.hermesDesktop?.contextMenuSpellcheck?.(action))
  }

  // A resolved file reference outranks the link section below it: the token is
  // not an anchor, so the two never both appear, and this is the only menu
  // entry that can act on a path the transcript merely mentioned.
  if (target.filePath) {
    sections.push(
      [
        // A directory has nothing to render in the rail and nothing to hand a
        // text editor, so a folder reference offers only the two verbs that
        // still mean something: show it, and copy it.
        target.filePathIsDirectory ? null : (
          <Item
            icon="preview"
            key="file-open-preview"
            label={copy.file.openInHermes}
            onSelect={() =>
              void normalizeOrLocalPreviewTarget(target.filePath).then(preview => {
                if (preview) {
                  openPreview(preview)
                }
              })
            }
          />
        ),
        // Executables are deliberately absent: `shell.openPath` would RUN an
        // agent-written `.command`/`.sh`. The file stays previewable and
        // revealable — only the exit that executes is withheld. Local-only
        // for the reason Reveal is: the editor runs on THIS machine, and a
        // gateway path names either nothing here or a different file.
        target.filePathIsDirectory || isDesktopFsRemoteMode() || !canOpenPathInEditor(target.filePath) ? null : (
          <Item
            icon="edit"
            key="file-open-editor"
            label={copy.file.openInEditor}
            onSelect={() => openPathInEditor(target.filePath)}
          />
        ),
        // Revealing goes through Electron's `shell.showItemInFolder`, which
        // acts on THIS machine's filesystem — a path that lives on a remote
        // gateway would silently select nothing, so the entry is local-only
        // (the same rule the file trees apply).
        isDesktopFsRemoteMode() ? null : (
          <Item
            icon="folder-opened"
            key="file-reveal"
            label={pickRevealLabel(t.fileMenu.revealFinder, t.fileMenu.revealExplorer, t.fileMenu.revealFileManager)}
            onSelect={() => void revealFile(target.filePath)}
          />
        ),
        // "Open with…" asks the OS which application to use. It sits beside
        // the editor entry and is withheld in the same cases: a directory has
        // no application to pick, and an executable must not gain a second
        // route to being launched. Local-only and Windows-only — no other
        // platform has a picker (`canOpenPathWith`).
        target.filePathIsDirectory ||
        isDesktopFsRemoteMode() ||
        !canOpenPathWith() ||
        !canOpenPathInEditor(target.filePath) ? null : (
          <Item
            icon="window"
            key="file-open-with"
            label={copy.file.openWith}
            onSelect={() => void openFileWith(target.filePath)}
          />
        ),
        <Item
          icon="copy"
          key="file-copy-path"
          label={copy.file.copyPath}
          onSelect={() => void writeClipboardText(target.filePath)}
        />
      ].filter(Boolean)
    )
  }

  if (linkUrl) {
    sections.push(
      [
        linkIsWeb && openInApp ? (
          <Item
            icon="globe"
            key="link-open-app"
            label={copy.link.openInApp}
            onSelect={() => openPreview({ kind: 'url', label: hostPathLabel(linkUrl), source: linkUrl, url: linkUrl })}
          />
        ) : null,
        <Item
          icon="link-external"
          key="link-open-external"
          label={copy.link.openExternal}
          onSelect={() => openExternalLink(linkUrl)}
        />,
        <Item
          icon="copy"
          key="link-copy"
          label={copy.link.copyUrl}
          onSelect={() => void writeClipboardText(linkUrl)}
        />,
        showResolvedCopy ? (
          <Item
            icon="copy"
            key="link-copy-resolved"
            label={copy.link.copyResolvedUrl}
            onSelect={() => void reachablePreviewUrl(linkUrl).then(writeClipboardText)}
          />
        ) : null
      ].filter(Boolean)
    )
  }

  if (target.onImage) {
    sections.push(
      [
        imageIsWeb && openInApp ? (
          <Item
            icon="globe"
            key="image-open-app"
            label={copy.link.openInApp}
            onSelect={() =>
              openPreview({
                kind: 'url',
                label: hostPathLabel(target.imageUrl),
                source: target.imageUrl,
                url: target.imageUrl
              })
            }
          />
        ) : null,
        imageIsWeb ? (
          <Item
            icon="link-external"
            key="image-open-external"
            label={copy.link.openExternal}
            onSelect={() => openExternalLink(target.imageUrl)}
          />
        ) : null,
        <Item
          icon="file-media"
          key="image-copy"
          label={copy.image.copyImage}
          onSelect={() => void window.hermesDesktop?.contextMenuCopyImage?.()}
        />,
        target.imageUrl ? (
          <Item
            icon="copy"
            key="image-copy-address"
            label={copy.image.copyImageAddress}
            onSelect={() => void writeClipboardText(target.imageUrl)}
          />
        ) : null,
        target.imageUrl ? (
          <Item
            icon="save"
            key="image-save"
            label={copy.image.saveImageAs}
            onSelect={() => void window.hermesDesktop?.saveImageFromUrl?.(target.imageUrl)}
          />
        ) : null
      ].filter(Boolean)
    )
  }

  if (target.editable) {
    if (spellcheck) {
      sections.push([
        ...spellcheck.suggestions
          .slice(0, 5)
          .map(suggestion => (
            <Item
              icon="edit"
              key={`spell-${suggestion}`}
              label={suggestion}
              onSelect={() => spellcheckAction({ kind: 'replace', word: suggestion })}
            />
          )),
        <Item
          icon="book"
          key="spell-add"
          label={copy.edit.addToDictionary}
          onSelect={() => spellcheckAction({ kind: 'add', word: spellcheck.misspelledWord })}
        />
      ])
    }

    // Verb availability mirrors the native menu: cut/copy act on the
    // SELECTION, so they need selected text — not just field content.
    // Inputs and textareas carry their selection on the element (Chrome
    // never reflects it into window.getSelection()); contenteditable uses
    // the document selection the resolver captured. Select all needs the
    // field to hold anything. Paste is intentionally NOT gated on a
    // clipboard probe: its action is webContents.paste() in main — the
    // same path Ctrl+V takes — which resolves the system clipboard itself,
    // while the renderer-side readClipboard probe can report empty on
    // Windows even when that path succeeds (#91553). Pasting with an
    // empty clipboard is a harmless no-op, so the item fails open.
    const formField =
      target.editable instanceof HTMLInputElement || target.editable instanceof HTMLTextAreaElement
        ? target.editable
        : null

    const fieldText = formField ? formField.value : (target.editable?.textContent ?? '')

    const hasFieldText = fieldText.length > 0

    const canCutCopy = formField
      ? (formField.selectionStart ?? 0) !== (formField.selectionEnd ?? 0)
      : target.selectionText.length > 0

    sections.push([
      <Item
        disabled={!canCutCopy}
        key="edit-cut"
        label={copy.edit.cut}
        onSelect={() => editableCommand('cut')}
        shortcut={EDIT_SHORTCUTS.cut}
      />,
      <Item
        disabled={!canCutCopy}
        key="edit-copy"
        label={t.common.copy}
        onSelect={() => editableCommand('copy')}
        shortcut={EDIT_SHORTCUTS.copy}
      />,
      <Item
        key="edit-paste"
        label={copy.edit.paste}
        onSelect={() => editableCommand('paste')}
        shortcut={EDIT_SHORTCUTS.paste}
      />
    ])
    sections.push([
      <Item
        disabled={!hasFieldText}
        key="edit-select-all"
        label={copy.edit.selectAll}
        onSelect={selectAllInEditable}
        shortcut={EDIT_SHORTCUTS.selectAll}
      />
    ])
  } else if (target.selectionText) {
    sections.push([
      <Item
        icon="copy"
        key="selection-copy"
        label={t.common.copy}
        onSelect={() => void writeClipboardText(target.selectionText)}
      />
    ])
  }

  return sections
}

/** The guest (in-app browser) menu: link/image/selection/editable sections
 *  from the Chromium params, plus select all for the page, closed by
 *  Inspect element. The page-level verbs (copy URL, open externally,
 *  console) live on the browser bar, not here. */
function guestSections(open: Extract<OpenContextMenu, { kind: 'guest' }>, t: Translations): ReactNode[][] {
  const copy = t.contextMenu
  const { guest, params } = open
  const sections: ReactNode[][] = []
  const linkUrl = params.linkURL
  const imageUrl = params.srcURL
  const openInApp = !hudForcesNativeLinks()

  // Same trap-timing rule as the dom side: dispatch AFTER the menu closes,
  // so the webview's focus() is not stolen back by the radix content.
  const guestEdit = (command: 'copy' | 'cut' | 'paste' | 'selectAll') => {
    closeContextMenu()
    requestAnimationFrame(() => guest.editCommand(command))
  }

  if (linkUrl) {
    sections.push(
      [
        isWebUrl(linkUrl) && openInApp ? (
          <Item
            icon="globe"
            key="guest-link-open-app"
            label={copy.link.openInApp}
            onSelect={() => openPreview({ kind: 'url', label: hostPathLabel(linkUrl), source: linkUrl, url: linkUrl })}
          />
        ) : null,
        <Item
          icon="link-external"
          key="guest-link-open-external"
          label={copy.link.openExternal}
          onSelect={() => openExternalLink(linkUrl)}
        />,
        <Item
          icon="copy"
          key="guest-link-copy"
          label={copy.link.copyUrl}
          onSelect={() => void writeClipboardText(linkUrl)}
        />
      ].filter(Boolean)
    )
  }

  if (params.hasImageContents || imageUrl) {
    sections.push(
      [
        isWebUrl(imageUrl) ? (
          <Item
            icon="link-external"
            key="guest-image-open-external"
            label={copy.link.openExternal}
            onSelect={() => openExternalLink(imageUrl)}
          />
        ) : null,
        params.hasImageContents ? (
          <Item icon="file-media" key="guest-image-copy" label={copy.image.copyImage} onSelect={guest.copyImage} />
        ) : null,
        imageUrl ? (
          <Item
            icon="copy"
            key="guest-image-copy-address"
            label={copy.image.copyImageAddress}
            onSelect={() => void writeClipboardText(imageUrl)}
          />
        ) : null,
        imageUrl ? (
          <Item
            icon="save"
            key="guest-image-save"
            label={copy.image.saveImageAs}
            onSelect={() => void window.hermesDesktop?.saveImageFromUrl?.(imageUrl)}
          />
        ) : null
      ].filter(Boolean)
    )
  }

  if (params.isEditable) {
    if (params.misspelledWord && params.dictionarySuggestions.length > 0) {
      sections.push([
        ...params.dictionarySuggestions
          .slice(0, 5)
          .map(suggestion => (
            <Item
              icon="edit"
              key={`guest-spell-${suggestion}`}
              label={suggestion}
              onSelect={() => guest.replaceMisspelling(suggestion)}
            />
          )),
        <Item
          icon="book"
          key="guest-spell-add"
          label={copy.edit.addToDictionary}
          onSelect={() => guest.addToDictionary(params.misspelledWord)}
        />
      ])
    }

    // Chromium's editFlags gate the verbs — the same availability verdict
    // the native menu showed (empty field → no cut/copy/select-all, empty
    // clipboard → no paste).
    sections.push([
      <Item
        disabled={!params.editFlags.canCut}
        key="guest-edit-cut"
        label={copy.edit.cut}
        onSelect={() => guestEdit('cut')}
        shortcut={EDIT_SHORTCUTS.cut}
      />,
      <Item
        disabled={!params.editFlags.canCopy}
        key="guest-edit-copy"
        label={t.common.copy}
        onSelect={() => guestEdit('copy')}
        shortcut={EDIT_SHORTCUTS.copy}
      />,
      <Item
        disabled={!params.editFlags.canPaste}
        key="guest-edit-paste"
        label={copy.edit.paste}
        onSelect={() => guestEdit('paste')}
        shortcut={EDIT_SHORTCUTS.paste}
      />
    ])
    sections.push([
      <Item
        disabled={!params.editFlags.canSelectAll}
        key="guest-edit-select-all"
        label={copy.edit.selectAll}
        onSelect={() => guestEdit('selectAll')}
        shortcut={EDIT_SHORTCUTS.selectAll}
      />
    ])
  } else if (params.selectionText.trim()) {
    sections.push([
      <Item icon="copy" key="guest-selection-copy" label={t.common.copy} onSelect={() => guestEdit('copy')} />
    ])
  }

  // Text tool for the page itself: select all works everywhere Chromium
  // says it can (a bare page has no field to scope to, so it selects the
  // page content).
  if (!params.isEditable) {
    sections.push([
      <Item
        disabled={!params.editFlags.canSelectAll}
        key="guest-select-all"
        label={copy.edit.selectAll}
        onSelect={() => guestEdit('selectAll')}
        shortcut={EDIT_SHORTCUTS.selectAll}
      />
    ])
  }

  // Inspect element closes every guest menu — the one page tool that earns
  // its place on any click. The other page verbs (copy URL, open
  // externally, console) live on the browser bar only.
  sections.push([
    <Item icon="inspect" key="guest-inspect" label={copy.page.inspectElement} onSelect={guest.inspectElement} />
  ])

  return sections
}

/**
 * THE app context menu: one capture-phase listener, one store, one menu.
 *
 * Every right-click in the app resolves here first. Radix-owned surfaces
 * (session rows and other `context-menu-trigger` wrappers) keep their own
 * menus; the reaction bubble keeps plain right-clicks; terminals and the
 * Star Map answer through registered handles; everything else gets a menu
 * assembled from what the click landed on — link, image, editable,
 * selection — with the window verbs as the empty-target fallback. Replaced
 * both the native Electron menu and the shell fallback wrapper, so labels
 * come from the locale files like every other surface.
 */
export function AppContextMenu() {
  const { t } = useI18n()
  const open = useStore($contextMenu)

  useEffect(() => {
    // Counts gestures, so a file probe that answers after the user has moved
    // on opens nothing; `replayed` marks the events this listener re-sent.
    let gesture = 0
    const replayed = new WeakSet<Event>()
    const onPointerDown = () => void (gesture += 1)

    // stopPropagation beats other renderer handlers; preventDefault is never
    // called because Chromium emits the main-process context-menu event (the
    // spellcheck + image-coordinate source) only for unprevented gestures —
    // and with no Menu.popup anywhere, "default" means no menu at all.
    const onContextMenu = (event: MouseEvent) => {
      const element = event.target instanceof Element ? event.target : null

      const trigger = element?.closest(`[${HERMES_CONTEXT_MENU_TRIGGER_ATTR}], [data-slot="context-menu-trigger"]`)

      // Only the pane-body wrapper is a fallback menu. Explicit row, tab and
      // status-bar menus still own their whole gesture, even inside a pane.
      if (trigger && !trigger.hasAttribute('data-zone-body')) {
        return
      }

      // The Star Map owns node hits, but empty canvas space still reaches the
      // shell fallback below. A canvas-wide opt-out would lose that fallback.
      if (openStarMapNodeMenuFor(element, event.clientX, event.clientY)) {
        event.preventDefault()
        event.stopPropagation()

        return
      }

      // A terminal's canvas has no DOM to resolve; its registered handle
      // carries the xterm selection and paste path instead.
      const terminal = terminalMenuHandleFor(element)

      if (terminal) {
        event.stopPropagation()
        openTerminalContextMenu(event.clientX, event.clientY, terminal)

        return
      }

      const target = resolveDomTarget(element)

      // A path the pointer never rested on has no verdict in the DOM yet. Hold
      // the gesture until the filesystem answers: a hit opens the file menu,
      // anything else re-sends the click so it resolves exactly as it would
      // have — including to a pane's or the reaction bubble's own menu, which
      // this listener can only yield to synchronously.
      const probing = element && !target.filePath && !replayed.has(event) ? probeFileReferenceAt(element) : null

      if (element && probing) {
        const turn = (gesture += 1)
        const { clientX, clientY } = event

        event.stopPropagation()

        void Promise.race([
          probing,
          new Promise<null>(resolve => setTimeout(() => resolve(null), FILE_PROBE_WAIT_MS))
        ]).then(match => {
          if (turn !== gesture || !element.isConnected) {
            return
          }

          if (match) {
            openDomContextMenu(clientX, clientY, {
              ...resolveDomTarget(element),
              filePath: match.path,
              filePathIsDirectory: match.isDirectory
            })

            return
          }

          const replay = new MouseEvent('contextmenu', {
            bubbles: true,
            button: 2,
            cancelable: true,
            clientX,
            clientY,
            composed: true
          })

          replayed.add(replay)
          element.dispatchEvent(replay)
        })

        return
      }

      const selection = window.getSelection()

      const selected = Boolean(
        target.selectionText &&
        element &&
        selection &&
        Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index)).some(range =>
          range.intersectsNode(element)
        )
      )

      // A resolved file reference is owned like a link: without it the pane
      // body's fallback menu below would take the click on a path in a chat.
      const owned = Boolean(target.filePath || target.linkUrl || target.onImage || target.editable || selected)

      // A selection elsewhere in the same pane must not take its bare menu.
      if (!owned && trigger) {
        return
      }

      // The reaction bubble owns bare right-clicks; a link inside it still
      // opens the link menu.
      if (!owned && element?.closest(`[${CONTEXT_MENU_SKIP_ATTR}]`)) {
        return
      }

      event.stopPropagation()
      openDomContextMenu(event.clientX, event.clientY, target)
    }

    window.addEventListener('contextmenu', onContextMenu, true)
    window.addEventListener('pointerdown', onPointerDown, true)

    return () => {
      window.removeEventListener('contextmenu', onContextMenu, true)
      window.removeEventListener('pointerdown', onPointerDown, true)
    }
  }, [])

  // Spell-check facts arrive from main after the menu opens (Chromium reports
  // them on its own context-menu event); attach them to the open menu.
  useEffect(() => window.hermesDesktop?.onContextMenuSpellcheck?.(augmentSpellcheck), [])

  if (!open) {
    return null
  }

  const sections =
    open.kind === 'terminal'
      ? terminalSections(open, t)
      : open.kind === 'guest'
        ? guestSections(open, t)
        : (list => (list.length ? list : [[<ShellMenuItems key="shell" kit={DROPDOWN_KIT} />]]))(domSections(open, t))

  return (
    <DropdownMenu
      onOpenChange={openState => {
        if (!openState) {
          closeContextMenu()
        }
      }}
      open
    >
      <DropdownMenuTrigger asChild>
        {/* A zero-size anchor at the click point: the menu positions against
            it exactly like a real trigger. */}
        <span aria-hidden style={{ left: open.x, position: 'fixed', top: open.y }} />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-56"
        onCloseAutoFocus={event => event.preventDefault()}
        portalContainer={open.kind === 'dom' ? open.target.dialogPortalContainer : undefined}
        side="bottom"
      >
        {sections.map((section, index) => (
          // Sections are positional by construction, so the index IS the key.
          <div className="contents" key={index}>
            {index > 0 && <DropdownMenuSeparator />}
            {section}
          </div>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
