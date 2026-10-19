// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { en } from '@/i18n/en'
import { setComposerPredictionsFromConfig } from '@/store/composer-ghost'

import { useComposerPlaceholder } from './use-composer-placeholder'

const resting = (sessionId: null | string) =>
  renderHook(() => useComposerPlaceholder({ disabled: false, reconnecting: false, sessionId }))

describe('useComposerPlaceholder with composer predictions', () => {
  afterEach(() => setComposerPredictionsFromConfig(undefined))

  it('leaves a session with history empty for the prediction instead of a stock follow-up', () => {
    const { result } = resting('session-A')

    expect(result.current).toBe('')

    act(() => setComposerPredictionsFromConfig(false))
    expect(en.composer.followUpPlaceholders).toContain(result.current)
  })

  it('keeps the starter in a new chat, where no prediction comes', () => {
    expect(en.composer.newSessionPlaceholders).toContain(resting(null).result.current)
  })
})
