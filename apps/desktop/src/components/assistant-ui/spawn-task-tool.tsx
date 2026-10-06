'use client'

import type { ToolCallMessagePartProps } from '@assistant-ui/react'
import { useStore } from '@nanostores/react'
import { useMemo, useState } from 'react'

import { useSessionView } from '@/app/chat/session-view'
import { ModelCatalogMenu, ModelMenuCloseContext, type ModelMenuController } from '@/app/shell/model-catalog-menu'
import { ToolFallback } from '@/components/assistant-ui/tool/fallback'
import { WIDGET_SHELL_CLASS } from '@/components/chat/widget-shell'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Codicon } from '@/components/ui/codicon'
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { useI18n } from '@/i18n'
import { displayModelName } from '@/lib/model-status-label'
import { reasoningEffortLabel } from '@/lib/reasoning-effort'
import { cn } from '@/lib/utils'
import { getModelPreset } from '@/store/model-presets'
import {
  $spawnTaskChips,
  $spawnTaskChoice,
  $spawnTaskLaunching,
  dismissSpawnTask,
  launchSpawnTask,
  spawnTaskChipKey,
  type SpawnTaskChoice,
  spawnTaskLauncherReady,
  type SpawnTaskMode
} from '@/store/spawn-task'

const SHELL_CLASS = `${WIDGET_SHELL_CLASS} text-[length:var(--conversation-text-font-size)] text-(--ui-text-primary)`
const CAPTION = 'text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height)'

const MODES: readonly SpawnTaskMode[] = ['tab', 'worktree']

const text = (value: unknown): string => (typeof value === 'string' ? value.trim() : '')

/** The chip's model controller: a detached value, like the kanban override.
 *  Picking a model seeds effort/fast from that model's remembered preset (what
 *  the composer would open it at); edits stay on the chip. */
function chipController(choice: SpawnTaskChoice, onChange: (next: SpawnTaskChoice) => void): ModelMenuController {
  return {
    applyPreset: (preset, row) =>
      onChange({
        ...choice,
        effort: preset.effort ?? '',
        fast: preset.fast ?? false,
        model: row.model,
        provider: row.provider
      }),
    current: { effort: choice.effort, fast: choice.fast, model: choice.model, provider: choice.provider },
    presetFor: (provider, model) => getModelPreset(provider, model),
    select: (model, provider) => onChange({ ...choice, model, provider }),
    setOptions: (patch, row) =>
      onChange({
        ...choice,
        ...(patch.effort !== undefined ? { effort: patch.effort } : {}),
        ...(patch.fast !== undefined ? { fast: patch.fast } : {}),
        model: row.model,
        provider: row.provider
      })
  }
}

/** `spawn_task`: a side task the agent offers. Nothing runs until the user
 *  picks a model and a launch mode here; the chip then opens the task as its
 *  own session (a new tab, optionally in a fresh worktree). */
export function SpawnTaskTool(props: ToolCallMessagePartProps) {
  const title = text(props.args?.title)
  const prompt = text(props.args?.prompt)

  // A refused call (no title/prompt) has nothing to launch — show the error row.
  if (props.isError || !title || !prompt) {
    return <ToolFallback {...props} />
  }

  return <SpawnTaskChip prompt={prompt} title={title} tldr={text(props.args?.tldr)} toolCallId={props.toolCallId} />
}

interface SpawnTaskChipProps {
  prompt: string
  title: string
  tldr: string
  toolCallId: string
}

function SpawnTaskChip({ prompt, title, tldr, toolCallId }: SpawnTaskChipProps) {
  const { t } = useI18n()
  const copy = t.assistant.spawnTask
  const view = useSessionView()
  const cwd = useStore(view.$cwd)
  const ownerStoredSessionId = useStore(view.$storedId)
  const chipKey = spawnTaskChipKey({ ownerStoredSessionId, toolCallId })
  const outcome = useStore($spawnTaskChips)[chipKey]
  const launching = useStore($spawnTaskLaunching).has(chipKey)
  const remembered = useStore($spawnTaskChoice)
  // The chip edits its own copy; the store remembers it only on launch, so
  // browsing models on one chip never rewrites what the next chip opens at.
  const [draft, setDraft] = useState<null | SpawnTaskChoice>(null)
  const choice = draft ?? remembered
  const [menuOpen, setMenuOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const controller = useMemo(() => chipController(choice, setDraft), [choice])

  if (outcome?.state === 'dismissed') {
    return (
      <div className={cn(CAPTION, 'my-1 text-(--ui-text-tertiary)')} data-spawn-task={toolCallId}>
        {copy.dismissed(title)}
      </div>
    )
  }

  const launch = (mode: SpawnTaskMode) =>
    void launchSpawnTask({ cwd, ownerStoredSessionId, prompt, title, toolCallId }, { ...choice, mode })

  const modelLabel = choice.model ? displayModelName(choice.model) : copy.defaultModel
  const effortLabel = choice.effort ? reasoningEffortLabel(choice.effort) : ''

  return (
    <div className={cn(SHELL_CLASS, 'my-1.5 grid max-w-lg min-w-0 gap-2')} data-spawn-task={toolCallId}>
      <div className="flex min-w-0 items-start gap-2.5">
        <Codicon className="mt-0.5 text-(--ui-text-tertiary)" name="layers" size="0.95rem" />
        <div className="grid min-w-0 flex-1 gap-0.5">
          <span className="font-medium leading-4.5 wrap-anywhere">{title}</span>
          {tldr ? <p className="leading-4.5 text-(--ui-text-secondary) wrap-anywhere">{tldr}</p> : null}
          <button
            className={cn(CAPTION, 'w-fit text-(--ui-text-tertiary) hover:text-(--ui-text-secondary)')}
            onClick={() => setExpanded(open => !open)}
            type="button"
          >
            {expanded ? copy.hidePrompt : copy.showPrompt}
          </button>
          {expanded ? (
            <p className={cn(CAPTION, 'whitespace-pre-wrap text-(--ui-text-secondary) wrap-anywhere')}>{prompt}</p>
          ) : null}
        </div>
      </div>

      {outcome?.state === 'launched' ? (
        <div className="flex min-w-0 items-center gap-2 pl-6.5">
          <span className={cn(CAPTION, 'text-(--ui-text-tertiary)')}>{copy.launched}</span>
          <Button
            onClick={() =>
              void import('@/app/open-session').then(({ openSession }) =>
                openSession(outcome.storedSessionId, () => undefined, 'tab')
              )
            }
            size="xs"
            variant="textStrong"
          >
            {copy.openSession}
          </Button>
        </div>
      ) : (
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1.5 pl-6.5">
          <DropdownMenu onOpenChange={setMenuOpen} open={menuOpen}>
            <DropdownMenuTrigger asChild>
              <Button
                aria-label={copy.modelLabel}
                className="max-w-56 gap-1 font-normal"
                disabled={launching}
                size="xs"
                variant="chip"
              >
                <span className="min-w-0 truncate">{modelLabel}</span>
                {effortLabel ? <span className="shrink-0 text-(--ui-text-tertiary)">{effortLabel}</span> : null}
                <Codicon className="opacity-50" name="chevron-down" size="0.65rem" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-72 p-0">
              <ModelMenuCloseContext.Provider value={() => setMenuOpen(false)}>
                <ModelCatalogMenu controller={controller} />
              </ModelMenuCloseContext.Provider>
            </DropdownMenuContent>
          </DropdownMenu>

          <label className={cn(CAPTION, 'flex cursor-pointer items-center gap-1.5 text-(--ui-text-secondary)')}>
            <Checkbox
              checked={choice.pin}
              className="size-3.5"
              disabled={launching}
              onCheckedChange={value => setDraft({ ...choice, pin: value === true })}
            />
            {copy.pin}
          </label>

          {MODES.map(mode => (
            <Button
              disabled={launching || !spawnTaskLauncherReady()}
              key={mode}
              onClick={() => launch(mode)}
              size="xs"
              variant={mode === choice.mode ? 'default' : 'secondary'}
            >
              {copy.mode[mode]}
            </Button>
          ))}

          <Button disabled={launching} onClick={() => dismissSpawnTask(chipKey)} size="xs" variant="text">
            {copy.dismiss}
          </Button>
        </div>
      )}
    </div>
  )
}
