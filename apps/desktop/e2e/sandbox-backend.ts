import * as fs from 'node:fs'
import * as path from 'node:path'

/**
 * Stop the backend a sandbox's app started. Desktop's local `gateway run` is detached on purpose (it
 * outlives the app's quit), so `app.close()` leaves it running against a home the sandbox is about to
 * delete; left alone, every spec run leaks one. Its own `gateway.pid` names it.
 */
export function stopSandboxBackend(hermesHome: string, graceMs = 10_000): void {
  let record: { hermes_home?: unknown; kind?: unknown; pid?: unknown }

  try {
    record = JSON.parse(fs.readFileSync(path.join(hermesHome, 'gateway.pid'), 'utf8'))
  } catch {
    return // never started, or already gone
  }

  const { pid } = record

  // A pid file another home wrote, or a recycled pid, is not ours to kill.
  if (record.kind !== 'hermes-gateway' || typeof pid !== 'number' || record.hermes_home !== hermesHome) {
    return
  }

  if (!signal(pid, 'SIGTERM')) {
    return
  }

  const pause = new Int32Array(new SharedArrayBuffer(4))

  for (let waited = 0; waited < graceMs; waited += 100) {
    Atomics.wait(pause, 0, 0, 100)

    if (!signal(pid, 0)) {
      return
    }
  }

  signal(pid, 'SIGKILL')
}

function signal(pid: number, sig: NodeJS.Signals | 0): boolean {
  try {
    process.kill(pid, sig)

    return true
  } catch {
    return false // ESRCH: already exited
  }
}
