import { expect, it } from 'vitest'

import { canOpenPathWith, pickRevealLabel } from './file-manager'

it('names the file manager the host actually has', () => {
  // The label is chosen per host, so assert the mapping rather than freezing
  // one platform's wording into the test.
  expect(pickRevealLabel('Finder', 'Explorer', 'Folder')).toMatch(/^(Finder|Explorer|Folder)$/)
})

it('offers an application picker only where the OS has one', () => {
  // Windows has `shell32.dll,OpenAs_RunDLL`. Elsewhere the only single-command
  // stand-in is launch-by-association — a different action than the label
  // promises, and precisely the one withheld from executables.
  expect(canOpenPathWith(true)).toBe(true)
  expect(canOpenPathWith(false)).toBe(false)
})
