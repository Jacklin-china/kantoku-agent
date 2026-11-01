import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { compileStyle, compileTemplate, parse } from '@vue/compiler-sfc'
import ts from 'typescript'

const source = readFileSync(new URL('../src/components/layout/timeOfDay.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022 } }).outputText
  .replace("from 'vue'", `from '${import.meta.resolve('vue')}'`)
const clock = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)

test('时段在原型约定的本地时间边界切换', () => {
  const at = (hour, minute) => new Date(2026, 9, 30, hour, minute)
  for (const [hour, minute, expected] of [
    [4, 59, 'night'], [5, 0, 'dawn'], [7, 29, 'dawn'], [7, 30, 'day'],
    [16, 29, 'day'], [16, 30, 'dusk'], [18, 59, 'dusk'], [19, 0, 'night'],
  ]) assert.equal(clock.todOfNow(at(hour, minute)), expected)
})

test('自动模式随本地时间更新，手动时段立即生效并保存', () => {
  const original = { Date: globalThis.Date, window: globalThis.window, document: globalThis.document, localStorage: globalThis.localStorage }
  const RealDate = original.Date
  let now = new RealDate(2026, 9, 31, 0, 39)
  let tick
  const saved = new Map()
  globalThis.Date = class extends RealDate { constructor(...args) { super(...(args.length ? args : [now.getTime()])) } }
  globalThis.document = { documentElement: { dataset: {} } }
  globalThis.window = { setInterval(callback) { tick = callback; return 1 }, clearInterval() {} }
  globalThis.localStorage = { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value) }
  try {
    clock.setTodMode('auto')
    clock.startTimeOfDay()
    assert.equal(document.documentElement.dataset.tod, 'night')
    now = new RealDate(2026, 9, 31, 5, 0)
    tick()
    assert.equal(document.documentElement.dataset.tod, 'dawn')
    clock.setTodMode('dusk')
    assert.equal(document.documentElement.dataset.tod, 'dusk')
    assert.equal(saved.get('kantoku-time-of-day'), 'dusk')
    now = new RealDate(2026, 9, 31, 12, 0)
    tick()
    assert.equal(document.documentElement.dataset.tod, 'dusk')
    clock.setTodMode('auto')
    assert.equal(document.documentElement.dataset.tod, 'day')
  } finally {
    clock.stopTimeOfDay()
    Object.assign(globalThis, original)
  }
})

test('天空原型可以编译且包含日夜所需的场景', () => {
  const component = readFileSync(new URL('../src/components/layout/PixelSky.vue', import.meta.url), 'utf8')
  const { descriptor, errors } = parse(component)
  assert.deepEqual(errors, [])
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'PixelSky.vue', id: 'sky' }).errors, [])
  for (const layer of ['stars', 'sun', 'moon', 'clouds', 'castle', 'aGround']) {
    assert.match(descriptor.template.content, new RegExp(`class="${layer}"`))
  }
})

test('编译后的时段调色板作用于天空组件而非仅作用于 html', () => {
  const component = readFileSync(new URL('../src/components/layout/PixelSky.vue', import.meta.url), 'utf8')
  const { descriptor } = parse(component)
  const style = compileStyle({ source: descriptor.styles[0].content, filename: 'PixelSky.vue', id: 'data-v-test', scoped: true })
  assert.deepEqual(style.errors, [])
  for (const period of ['dawn', 'dusk', 'night']) {
    assert.match(style.code, new RegExp(`html\\[data-tod="${period}"\\]\\s+\\.px-sky\\s*\\{`))
  }
  assert.match(style.code, /html\[data-tod="night"\]\s+\.px-sky\s*\{[^}]*--moonOp:1/s)
})
