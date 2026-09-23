import { expect, it } from 'vitest'

import { $pathModifierHeld, watchPathModifier } from './path-modifier'

function press(key: string, init: Partial<KeyboardEventInit> = {}) {
  window.dispatchEvent(new KeyboardEvent('keydown', { key, ...init }))
}

function release(key: string, init: Partial<KeyboardEventInit> = {}) {
  window.dispatchEvent(new KeyboardEvent('keyup', { key, ...init }))
}

it('arms while the modifier is held and disarms when it is released', () => {
  const stop = watchPathModifier()

  press('Meta', { metaKey: true })
  expect($pathModifierHeld.get()).toBe(true)
  release('Meta', { metaKey: false })
  expect($pathModifierHeld.get()).toBe(false)
  stop()
})

it('stays armed while another key is typed with the modifier down', () => {
  const stop = watchPathModifier()

  press('Meta', { metaKey: true })
  press('a', { metaKey: true })
  release('a', { metaKey: true })
  expect($pathModifierHeld.get()).toBe(true)
  stop()
})

it('disarms when the window loses focus mid-chord', () => {
  // Cmd+Tab delivers keydown and never the matching keyup, so without the
  // blur reset the transcript would stay armed for the rest of the session.
  const stop = watchPathModifier()

  press('Meta', { metaKey: true })
  window.dispatchEvent(new Event('blur'))
  expect($pathModifierHeld.get()).toBe(false)
  stop()
})

it('leaves nothing armed and no listeners behind after it is stopped', () => {
  const stop = watchPathModifier()

  press('Meta', { metaKey: true })
  stop()
  expect($pathModifierHeld.get()).toBe(false)

  press('Meta', { metaKey: true })
  expect($pathModifierHeld.get()).toBe(false)
})
