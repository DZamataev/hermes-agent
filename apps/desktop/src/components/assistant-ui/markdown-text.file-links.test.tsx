import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

import { $previewTabs, closeRightRail } from '@/store/preview'
import { $currentCwd } from '@/store/session'

import { MarkdownTextContent } from './markdown-text'

afterEach(() => {
  cleanup()
  closeRightRail()
  $currentCwd.set('')
})

it.each([false, true])(
  'opens prose and inline-code file references in the session preview (running=%s)',
  async isRunning => {
    $currentCwd.set('/work/looky')
    render(
      <MarkdownTextContent
        isRunning={isRunning}
        text={'План — `.planning/Looky-9039_contacts-search.md`: фаза P4b.\n\nБриф — .planning/Looky-9039_handoff.md.'}
      />
    )
    expect($previewTabs.get()).toHaveLength(0)
    const plan = await screen.findByRole('link', { name: '.planning/Looky-9039_contacts-search.md' })
    expect(plan.querySelector('code')).not.toBeNull()
    fireEvent.click(plan)
    await waitFor(() =>
      expect(
        $previewTabs.get().some(tab => tab.target.url === 'file:///work/looky/.planning/Looky-9039_contacts-search.md')
      ).toBe(true)
    )
    fireEvent.click(screen.getByRole('link', { name: '.planning/Looky-9039_handoff.md' }))
    await waitFor(() =>
      expect(
        $previewTabs.get().some(tab => tab.target.url === 'file:///work/looky/.planning/Looky-9039_handoff.md')
      ).toBe(true)
    )
  }
)

it('leaves code blocks, web links, and ordinary dotted text out of file autolinking', () => {
  const { container } = render(
    <MarkdownTextContent
      isRunning={false}
      text={'```sh\ncat docs/plan.md\n```\n\n[docs/plan.md](https://example.com/docs/plan.md)\n\nexample.com v1.2.3'}
    />
  )

  expect(container.querySelector('a[href^="#file-ref/"]')).toBeNull()
})
