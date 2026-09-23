import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useI18n } from '@/i18n'
import { $fileOpenApp, setFileOpenApp, SYSTEM_EDITOR_APP } from '@/store/file-open-prefs'

import { ListRow } from './primitives'

interface DetectedEditorApp {
  id: string
  label: string
}

/**
 * Which application opens a file sent out of Hermes.
 *
 * The OS association is the default and stays the first row. The rest are the
 * editors actually detected on this machine — an install the user does not
 * have is never offered, so every row in the list works when chosen.
 *
 * Device-scoped (localStorage, not `config.yaml`): it names software installed
 * here, so carrying it to another machine with a profile would point at an
 * application that isn't there.
 */
export function FileOpenAppSetting() {
  const { t } = useI18n()
  const copy = t.settings.config
  const selected = useStore($fileOpenApp)
  const [apps, setApps] = useState<DetectedEditorApp[] | null>(null)

  useEffect(() => {
    const detect = window.hermesDesktop?.editorApps

    // No bridge (browser dev, remote-only shell): the OS row is the only
    // honest option, so the picker stays collapsed to it.
    if (!detect) {
      setApps([])

      return
    }

    let live = true

    void detect().then(found => {
      if (live) {
        setApps(found.map(app => ({ id: app.id, label: app.label })))
      }
    })

    return () => {
      live = false
    }
  }, [])

  // A previously picked editor that has since been uninstalled would otherwise
  // render as an empty trigger; show it as the system default, which is what
  // `openPathInEditor` will actually do.
  const known = apps?.some(app => app.id === selected) ?? false
  const value = selected === SYSTEM_EDITOR_APP || known ? selected : SYSTEM_EDITOR_APP

  return (
    <ListRow
      action={
        <Select onValueChange={setFileOpenApp} value={value}>
          <SelectTrigger aria-label={copy.fileOpenAppLabel} className="min-w-56">
            <SelectValue />
          </SelectTrigger>

          <SelectContent>
            <SelectItem value={SYSTEM_EDITOR_APP}>{copy.fileOpenAppSystem}</SelectItem>
            {(apps ?? []).map(app => (
              <SelectItem key={app.id} value={app.id}>
                {app.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      }
      description={apps?.length === 0 ? `${copy.fileOpenAppDesc} ${copy.fileOpenAppEmpty}` : copy.fileOpenAppDesc}
      title={copy.fileOpenAppTitle}
    />
  )
}
