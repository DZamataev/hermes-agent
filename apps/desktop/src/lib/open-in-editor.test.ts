import { expect, it, vi } from 'vitest'

import { fileUrlForPath, openPathInEditor, wantsExternalEditor } from './open-in-editor'

const opened: string[] = []

vi.mock('@/lib/external-link', () => ({
  openExternalLink: (href: string) => opened.push(href)
}))

const CLICK = { altKey: false, ctrlKey: false, metaKey: false }

it.each([
  ['macOS', true, 'metaKey' as const, 'ctrlKey' as const],
  ['elsewhere', false, 'ctrlKey' as const, 'metaKey' as const]
])('on %s asks for the editor only when the accelerator is joined by alt', (_label, isMac, accel, other) => {
  // Cmd/Ctrl alone is what arms and opens the preview; alt is the second step.
  expect(wantsExternalEditor({ ...CLICK, altKey: true, [accel]: true }, isMac)).toBe(true)
  expect(wantsExternalEditor({ ...CLICK, [accel]: true }, isMac)).toBe(false)
  expect(wantsExternalEditor({ ...CLICK, altKey: true }, isMac)).toBe(false)
  expect(wantsExternalEditor({ ...CLICK, altKey: true, [other]: true }, isMac)).toBe(false)
  expect(wantsExternalEditor(CLICK, isMac)).toBe(false)
})

it('percent-encodes each path segment so spaces and non-latin names survive', () => {
  expect(fileUrlForPath('/work/my notes/план.md')).toBe('file:///work/my%20notes/%D0%BF%D0%BB%D0%B0%D0%BD.md')
  // The separators stay separators — encoding the whole string would not.
  expect(fileUrlForPath('/a/b/c.md')).toBe('file:///a/b/c.md')
})

it('hands an absolute path to the OS as a file URL', () => {
  opened.length = 0
  openPathInEditor('/work/looky/docs/plan.md')
  expect(opened).toEqual(['file:///work/looky/docs/plan.md'])
})

it('refuses a relative path rather than resolving it against the wrong directory', () => {
  // Electron's cwd is not the session's, so a relative path would open a
  // different file — or none — with no indication that it did.
  opened.length = 0
  openPathInEditor('docs/plan.md')
  openPathInEditor('   ')
  expect(opened).toEqual([])
})
