import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/settingsDefaults.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022 } })
const { DEFAULT_HEALERS, migrateRepeatableHealers } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('previous default is promoted even in a different order', () => {
  assert.deepEqual(migrateRepeatableHealers(['白芷', '卜灵', '莫宁', '维里奈', '守岸人'], true), DEFAULT_HEALERS)
})
test('custom and explicitly empty whitelists are preserved', () => {
  assert.deepEqual(migrateRepeatableHealers(['守岸人'], true), ['守岸人'])
  assert.deepEqual(migrateRepeatableHealers([], true), [])
})
test('a current-version five-character choice is not re-promoted', () => {
  const chosen = DEFAULT_HEALERS.slice(0, -1)
  assert.deepEqual(migrateRepeatableHealers(chosen, false), chosen)
})
test('fresh defaults are independent copies and include Suisui', () => {
  const defaults = migrateRepeatableHealers(undefined, false)
  assert.ok(defaults.includes('穗穗'))
  defaults.pop()
  assert.ok(DEFAULT_HEALERS.includes('穗穗'))
})
