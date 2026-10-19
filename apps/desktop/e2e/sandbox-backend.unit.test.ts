import { spawn } from 'node:child_process'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { afterEach, expect, test } from 'vitest'

import { stopSandboxBackend } from './sandbox-backend'

const alive = (pid: number) => {
  try {
    process.kill(pid, 0)

    return true
  } catch {
    return false
  }
}

const children: number[] = []

afterEach(() => {
  for (const pid of children.splice(0)) {
    if (alive(pid)) { process.kill(pid, 'SIGKILL') }
  }
})

function fakeBackend(trapTerm = false): number {
  // Detached like the real one, so the test runner is not its parent group.
  const script = `${trapTerm ? "process.on('SIGTERM', () => {});" : ''}setInterval(() => {}, 1000)`
  const child = spawn(process.execPath, ['-e', script], { detached: true, stdio: 'ignore' })
  child.unref()
  children.push(child.pid!)

  return child.pid!
}

function home(record: Record<string, unknown>): string {
  const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'sandbox-backend-')))
  fs.writeFileSync(path.join(dir, 'gateway.pid'), JSON.stringify({ kind: 'hermes-gateway', hermes_home: dir, ...record }))

  return dir
}

const exited = async (pid: number) => {
  for (let i = 0; i < 50 && alive(pid); i++) { await new Promise(resolve => setTimeout(resolve, 20)) }

  return !alive(pid)
}

test('the sandbox backend named by its gateway.pid is stopped, SIGKILL after the grace period', async () => {
  const polite = fakeBackend()
  stopSandboxBackend(home({ pid: polite }), 300) // short grace: this runner reaps its fake only after the call
  expect(await exited(polite)).toBe(true)

  const stubborn = fakeBackend(true)
  await new Promise(resolve => setTimeout(resolve, 200)) // let it install the SIGTERM trap
  stopSandboxBackend(home({ pid: stubborn }), 300)
  expect(await exited(stubborn)).toBe(true)
})

test('a pid file of another home or kind is left alone', async () => {
  const other = fakeBackend()
  stopSandboxBackend(home({ pid: other, hermes_home: '/elsewhere' }))
  stopSandboxBackend(home({ pid: other, kind: 'something-else' }))
  // The fake is this runner's child: give the runtime a moment to reap it, or a killed one still
  // answers signal 0 as a zombie.
  await new Promise(resolve => setTimeout(resolve, 300))
  expect(alive(other)).toBe(true)
  // No pid file at all: nothing to do, no throw.
  stopSandboxBackend(fs.mkdtempSync(path.join(os.tmpdir(), 'sandbox-backend-empty-')))
})
