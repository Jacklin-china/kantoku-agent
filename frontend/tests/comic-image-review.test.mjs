import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
import { compileScript, parse } from '@vue/compiler-sfc'
import { computed, createRenderer, createSSRApp, h, nextTick, ref } from 'vue'
import { renderToString } from '@vue/server-renderer'

const transpile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText
// Minimal node renderer has no browser realm; Vue's input directive checks these types.
globalThis.Document ??= class TestDocument {}
globalThis.ShadowRoot ??= class TestShadowRoot {}
globalThis.ResizeObserver ??= class TestResizeObserver { observe() {} disconnect() {} }
const moduleUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const geometryUrl = moduleUrl(transpile(readFileSync(new URL('../src/components/ui/imageViewport.ts', import.meta.url), 'utf8')))
const presentationUrl = moduleUrl(transpile(readFileSync(new URL('../src/domains/comic/shotGeneration.ts', import.meta.url), 'utf8')))
const workbenchMethods = ['analyzeComicImage', 'compareComicImages', 'confirmComicEditInstruction', 'createComicEditInstruction',
  'getArtifactContentUrl', 'getComicImageReport', 'getComicImageReports', 'getComicImages', 'getComicImageVersions',
  'getComicProject', 'getComicImageRequests', 'getComicReviewDraft', 'getComicWorkbench', 'importComicImage', 'restoreComicImageFile',
  'saveComicReviewDraft', 'saveComicWorkbench', 'selectComicImage', 'deleteComicImage']
const workbenchApiUrl = moduleUrl(workbenchMethods.map(name => `export const ${name} = (...args) => globalThis.workbenchAPI.${name}(...args);`).join('\n'))
const { reviewImageSources, finalImagePrompt } = await import(presentationUrl)
const { fittedImage, boundImagePan, zoomImage } = await import(geometryUrl)
test('landscape and portrait fitted frames never expose blank pixels at any drag or zoom boundary', () => {
  for (const source of [{ width: 2400, height: 800 }, { width: 800, height: 2400 }]) {
    for (const space of [{ width: 600, height: 500 }, { width: 240, height: 700 }]) {
      const fit = fittedImage(source, space)
      assert.ok(fit.width <= space.width && fit.height <= space.height)
      for (const zoom of [1, 1.01, 2, 16]) {
        const pan = boundImagePan({ x: 1e6, y: -1e6 }, fit, zoom)
        assert.ok(Math.abs(pan.x) <= fit.width * (zoom - 1) / 2)
        assert.ok(Math.abs(pan.y) <= fit.height * (zoom - 1) / 2)
        const view = zoomImage(pan, zoom, .0001, { x: 100, y: -100 }, fit)
        assert.equal(view.zoom, 1); assert.equal(view.pan.x, 0); assert.equal(view.pan.y, 0)
      }
    }
  }
})
function viewUrl(name) {
  let code = transpile(compileScript(parse(readFileSync(new URL(`../src/components/${name === 'ImageViewport' ? 'ui' : 'comic'}/${name}.vue`, import.meta.url), 'utf8')).descriptor,
    { id: name, inlineTemplate: true }).content)
    .replace(/from (["'])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace(/from (["'])lucide-vue-next\1/g, `from ${JSON.stringify(import.meta.resolve('lucide-vue-next'))}`)
    .replace(/from (["'])(?:\.\.\/ui\/|\.\/)imageViewport\1/g, `from ${JSON.stringify(geometryUrl)}`)
    .replace(/from (["'])\.\.\/\.\.\/domains\/comic\/shotGeneration\1/, `from ${JSON.stringify(presentationUrl)}`)
    .replace(/from (["'])\.\.\/\.\.\/services\/core\1/, `from ${JSON.stringify(workbenchApiUrl)}`)
  if (name !== 'ComicImageImport' && name !== 'ImageViewport') code = code.replace(/from (["'])\.\/ComicImageImport.vue\1/, `from ${JSON.stringify(viewUrl('ComicImageImport'))}`)
    .replace(/from (["'])\.\.\/ui\/ImageViewport.vue\1/, `from ${JSON.stringify(viewUrl('ImageViewport'))}`)
  return moduleUrl(code)
}
const { default: Importer } = await import(viewUrl('ComicImageImport'))
const { default: Optimize } = await import(viewUrl('ImageOptimizeStage'))
const image = { image_id: 'legacy-image', version: 1, artifact_id: 'legacy-image', filename: 'original.png', project_id: 'project', conversation_id: 'conversation', sha256: 'mine' }
const basis = { source_image: { image_id: image.image_id, version: 1 }, project_id: 'project', conversation_id: 'conversation', task_context: { task: '检查当前画面' } }
const analysis = { artifact_id: 'analysis', basis, output: { summary: '离线图片观察', findings: [
  { finding_id: 'edge', observation: '边缘模糊', suggested_change: '改善选中边缘', confidence: 0.7, region: [0.1, 0.2, 0.6, 0.8] },
], protected_features: ['原有山体轮廓'], limitations: ['离线替身，不证明真实视觉质量'] } }
const instruction = { artifact_id: 'edit', basis, analysis_artifact_id: analysis.artifact_id, requires_confirmation: false,
  scope: { user_instruction: '柔化光线', selected_findings: analysis.output.findings, protected_features: analysis.output.protected_features },
  output: { positive_prompt: '仅柔化光线并改善选中边缘，保留山体轮廓。', negative_prompt: '不要增加人物', summary: '局部修改' } }

function node(tag) { return { tag, tagName: tag.toUpperCase(), props: {}, children: [], parent: null, value: '', listeners: {}, style: {},
  get options() { return this.children.filter(item => item.tag === 'option') },
  getRootNode() { return { activeElement: null } },
  getBoundingClientRect() { return { left: 0, top: 0, width: 1400, height: 800 } },
  addEventListener(name, action) { this.listeners[name] = action }, removeEventListener() {},
  click() { this.clicked = true } } }
const renderer = createRenderer({
  createElement: node, createText: text => ({ ...node('#text'), text }), createComment: text => ({ ...node('#comment'), text }),
  setText: (el, text) => { el.text = text }, setElementText: (el, text) => { el.text = text; el.children = [] },
  patchProp: (el, key, _previous, value) => { el.props[key] = value }, parentNode: el => el.parent,
  nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1] ?? null,
  insert(el, parent, anchor) {
    if (el.parent) { const old = el.parent.children; old.splice(old.indexOf(el), 1) }
    el.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    parent.children.splice(index < 0 ? parent.children.length : index, 0, el)
  },
  remove(el) { const list = el.parent?.children; if (list) list.splice(list.indexOf(el), 1) },
})
function find(el, predicate) {
  if (predicate(el)) return el
  for (const child of el.children) { const found = find(child, predicate); if (found) return found }
  return null
}
async function openSupplement(host) {
  const card = find(host, el => el.props['data-requirement'] === 'supplement')
  find(card, el => el.props.class === 'wb-card-title').props.onClick()
  await nextTick()
  return find(host, el => el.props['aria-label'] === '本轮补充要求')
}

test('file picker and drop emit the same File; invalid, multiple and busy drops never upload', async () => {
  const host = node('root'), uploads = [], busy = ref(false)
  const app = renderer.createApp({ render: () => h(Importer, { disabled: busy.value, onUpload: file => uploads.push(file) }) })
  app.mount(host)
  try {
    const file = { name: 'image.png', type: 'image/png', size: 128 }
    const input = find(host, el => el.tag === 'input')
    input.value = 'selected'; input.props.onChange({ target: { files: [file], value: 'selected' } })
    host.children[0].props.onDrop({ preventDefault() {}, dataTransfer: { files: [file] } })
    assert.deepEqual(uploads, [file, file])
    for (const files of [[{ ...file, type: 'text/plain' }], [{ ...file, size: 5 * 1024 * 1024 + 1 }], [file, file]]) {
      host.children[0].props.onDrop({ preventDefault() {}, dataTransfer: { files } })
      await nextTick()
      assert.ok(find(host, el => el.props.role === 'alert'))
    }
    busy.value = true; await nextTick()
    host.children[0].props.onDrop({ preventDefault() {}, dataTransfer: { files: [file] } })
    assert.equal(uploads.length, 2)
  } finally { app.unmount() }
})

test('old Shot images and direct uploads share real sources without duplicate previews or other-chat media', () => {
  const prompt = { project_id: 'project', shot_id: 'shot', shot_sequence_number: 3, version: 4,
    external_image: { artifact_id: image.artifact_id, filename: image.filename } }
  assert.equal(reviewImageSources([prompt], [], 'conversation')[0].version, 1)
  const sources = reviewImageSources([prompt], [image, { ...image, artifact_id: 'foreign', conversation_id: 'other' }], 'conversation')
  assert.deepEqual(sources, [image])
})

const flush = async () => { for (let i = 0; i < 25; i++) { await Promise.resolve(); await nextTick() } }
const content = el => (el.text ?? '') + el.children.map(content).join('')
const draftKey = source => source.image_id + ':' + source.version
function offlineWorkbench(report = analysis) {
  const calls = [], stored = new Map()
  const second = { ...image, version: 2, artifact_id: 'result', parent: { image_id: image.image_id, version: 1 } }
  let savedInstruction = null
  let workspace = { version: 0, state: { image: basis.source_image, edit_image: basis.source_image, auto_analyze: false } }
  const api = {
    getComicImages: async () => [second],
    getComicWorkbench: async () => workspace,
    getComicImageVersions: async (...args) => {
      calls.push({ action: 'versions', args, versions: [second.version, image.version] })
      return { versions: [second, image], next_before: null, selection: null }
    },
    getArtifactContentUrl: async id => 'blob:' + id,
    getComicReviewDraft: async source => stored.get(draftKey(source)) ?? { version: 0, state: null },
    getComicImageReports: async (_pid, _cid, kind, source) => ({ items: kind === 'image-analyses' && source.version === 1 ? [report] : kind === 'edit-instructions' && savedInstruction ? [savedInstruction] : [], next_cursor: null }),
    getComicImageReport: async () => savedInstruction,
    saveComicWorkbench: async (_pid, _cid, version, state) => {
      calls.push({ action: 'workspace', state })
      workspace = { version: version + 1, state }; return workspace
    },
    saveComicReviewDraft: async (_cid, version, value) => {
      const saved = { version: version + 1, state: JSON.parse(JSON.stringify(value)) }
      calls.push({ action: 'save', body: saved.state }); stored.set(draftKey(value.image), saved); return saved
    },
    getComicProject: async () => ({ project: { current_version: 4 } }),
    getComicImageRequests: async () => [],
    createComicEditInstruction: async (_pid, request) => {
      calls.push({ action: 'edit', body: request })
      savedInstruction = { ...instruction, round_id: request.round_id, requires_confirmation: true,
        scope: { ...instruction.scope, selected_findings: report.output.findings.filter(item => request.selected_finding_ids.includes(item.finding_id)),
          user_instruction: request.instruction, edit_scope: request.edit_scope } }
      return savedInstruction
    },
    analyzeComicImage: async () => { calls.push({ action: 'analysis' }); return report },
    selectComicImage: async (_id, request) => { calls.push({ action: 'adopt', body: request }) },
    deleteComicImage: async (projectId, imageId, request) => {
      calls.push({ action: 'delete', projectId, imageId, body: request })
      return { image_id: imageId, deleted_versions: [1], project_version: 5 }
    },
    confirmComicEditInstruction: async () => { calls.push({ action: 'confirm' }) },
  }
  return { api, calls, stored }
}

test('adoption sends only explicitly selected object attributes and clears the extra permission', async () => {
  const report = { ...analysis, basis: { ...basis, references: { established_elements: [
    { entity_id: 'person-a', attribute: 'face', target: '甲' },
    { entity_id: 'person-b', attribute: 'clothing', target: '乙' },
  ] } } }
  const fixture = offlineWorkbench(report), previous = globalThis.workbenchAPI
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    const photo = find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image')
    photo.props.onLoad({ target: { naturalWidth: 800, naturalHeight: 600 } })
    await flush()
    const checkbox = find(host, el => el.tag === 'input' && el.props.value === 'person-a:face')
    assert.ok(checkbox)
    checkbox.props['onUpdate:modelValue'](['person-a:face'])
    await flush()
    await find(host, el => el.tag === 'button' && content(el).includes('采用此版本')).props.onClick()
    await flush()
    const sent = fixture.calls.find(item => item.action === 'adopt').body
    assert.deepEqual(sent.accepted_visual_changes, [{ entity_id: 'person-a', target: '甲', attributes: ['face'], quote: '甲 · 面容按当前图片更新', operation: 'change' }])
    assert.equal(fixture.calls.filter(item => item.action === 'analysis').length, 0)
  } finally { app.unmount(); globalThis.workbenchAPI = previous }
})

test('fixed viewport bounds zoom and pan while normalized selections stay stable', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI, priorObserver = globalThis.ResizeObserver
  let measure
  globalThis.ResizeObserver = class { constructor(callback) { measure = callback } observe() {} disconnect() {} }
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    const canvas = find(host, el => String(el.props.class).split(' ').includes('wb-canvas'))
    canvas.getBoundingClientRect = () => ({ left: 100, top: 50, width: 600, height: 500 })
    canvas.setPointerCapture = canvas.releasePointerCapture = () => {}
    measure()
    const img = find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image')
    img.props.onLoad({ target: { naturalWidth: 1000, naturalHeight: 800 } }); await flush()
    const bounds = find(host, el => el.props.class === 'wb-image-bounds')
    const frame = { ...bounds.props.style }
    bounds.setPointerCapture = bounds.releasePointerCapture = () => {}
    const viewport = find(host, el => el.props.class === 'wb-viewport'), fittedFrame = { ...viewport.props.style }
    bounds.getBoundingClientRect = () => ({ left: 100 + parseFloat(viewport.props.style.left) + parseFloat(bounds.props.style.left),
      top: 50 + parseFloat(viewport.props.style.top) + parseFloat(bounds.props.style.top),
      width: parseFloat(bounds.props.style.width), height: parseFloat(bounds.props.style.height) })
    const relativePoint = (x, y) => { const rect = bounds.getBoundingClientRect(); return [(x - rect.left) / rect.width, (y - rect.top) / rect.height] }
    const anchor = relativePoint(450, 250)
    let prevented = 0
    canvas.props.onWheel({ deltaY: -160, deltaMode: 0, clientX: 450, clientY: 250, ctrlKey: false, preventDefault() { prevented++ } })
    await nextTick()
    assert.equal(prevented, 1)
    assert.ok(parseFloat(bounds.props.style.width) > parseFloat(frame.width))
    relativePoint(450, 250).forEach((value, index) => assert.ok(Math.abs(value - anchor[index]) < 1e-10))
    assert.deepEqual(viewport.props.style, fittedFrame)
    const before = { ...bounds.props.style }, pointer = { pointerId: 7, button: 0, clientX: 400, clientY: 300, preventDefault() {} }
    canvas.props.onPointerdown(pointer)
    canvas.props.onPointermove({ ...pointer, clientX: 440, clientY: 320 })
    canvas.props.onPointerup({ ...pointer, clientX: 440, clientY: 320 }); await nextTick()
    assert.ok(Math.abs(parseFloat(bounds.props.style.left) - parseFloat(before.left) - 40) < 1e-10)
    assert.ok(Math.abs(parseFloat(bounds.props.style.top) - parseFloat(before.top) - 20) < 1e-10)
    assert.deepEqual(viewport.props.style, fittedFrame)
    await find(host, el => el.tag === 'button' && content(el) === '框选区域').props.onClick(); await flush()
    assert.equal(bounds.props.style.width, before.width)
    const rect = bounds.getBoundingClientRect()
    const drawPoint = (x, y) => ({ pointerId: 8, button: 0, clientX: rect.left + rect.width * x, clientY: rect.top + rect.height * y, preventDefault() {} })
    bounds.props.onPointerdown(drawPoint(.4, .4))
    bounds.props.onPointermove(drawPoint(.6, .6))
    bounds.props.onPointerup(drawPoint(.6, .6)); await flush()
    const demand = find(host, el => el.props['aria-label'] === '手动1的修改要求')
    demand.props['onUpdate:modelValue']('调整框内边缘'); demand.props.onInput(); await nextTick()
    await find(host, el => el.tag === 'button' && content(el) === '整理改图指令').props.onClick(); await flush()
    const submitted = fixture.calls.find(item => item.action === 'edit').body
    submitted.edit_scope.regions[0].bounds.forEach((value, index) => assert.ok(Math.abs(value - [.4, .4, .6, .6][index]) < 1e-10))
    // Fit is the minimum. Dragging cannot move a fitted image away from the frame.
    canvas.props.onWheel({ deltaY: 700, deltaMode: 0, clientX: 450, clientY: 250, preventDefault() {} })
    await nextTick()
    assert.equal(bounds.props.style.width, frame.width)
    const small = { ...bounds.props.style }
    canvas.props.onPointerdown(pointer)
    canvas.props.onPointermove({ ...pointer, clientX: 370, clientY: 280 })
    canvas.props.onPointerup({ ...pointer, clientX: 370, clientY: 280 }); await nextTick()
    assert.deepEqual(bounds.props.style, small)
    assert.deepEqual(viewport.props.style, fittedFrame)
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous; globalThis.ResizeObserver = priorObserver }
})

test('drawing hint leaves the picture alone and the panel heading stops repeating the filename', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image').props.onLoad()
    await nextTick()
    const heading = find(host, el => el.props.class === 'wb-panel-heading')
    assert.equal(content(heading), '本轮修改')
    await find(host, el => el.tag === 'button' && content(el) === '框选区域').props.onClick()
    await flush()
    const canvas = find(host, el => String(el.props.class).split(' ').includes('wb-canvas'))
    // 提示必须落在画布之外：画布里既没有旧浮层，也没有新状态行。
    assert.equal(find(host, el => el.props.class === 'wb-drawing-hint'), null)
    assert.equal(find(canvas, el => el.props.class === 'wb-draw-status'), null)
    assert.ok(find(host, el => el.props.class === 'wb-draw-status'))
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('switching to another picture loads that picture own draft and analysis', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  globalThis.workbenchAPI = fixture.api
  const other = { ...image, image_id: 'other-image', artifact_id: 'other-artifact', filename: 'other.png', sha256: 'other-bytes' }
  fixture.api.getComicImages = async () => [image, other]
  fixture.api.getComicImageVersions = async id => ({ versions: id === 'other-image' ? [other] : [image],
    next_before: null, selection: null })
  fixture.api.getComicImageReports = async (_pid, _cid, kind, source) => ({
    items: kind === 'image-analyses' && source.image_id === 'other-image'
      ? [{ ...analysis, artifact_id: 'other-analysis', basis: { ...basis, source_image: { image_id: 'other-image', version: 1 },
          images: [{ image_id: 'other-image', version: 1, artifact_id: 'other-artifact', sha256: 'other-bytes' }] },
          output: { ...analysis.output, summary: '另一张图自己的分析',
            findings: [...analysis.output.findings, { ...analysis.output.findings[0], finding_id: 'other-extra' }] } }]
      : kind === 'image-analyses' ? [analysis] : [],
    next_cursor: null })
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    assert.ok(content(host).includes('分析建议（1）'))
    // 切换「图片」下拉：右侧必须换成那张图自己的草稿与分析，而不是沿用上一张。
    find(host, el => el.tag === 'select').props.onChange({ target: { value: 'other-image' } })
    await flush()
    assert.ok(content(host).includes('分析建议（2）'))
    assert.equal(content(host).includes('分析建议（1）'), false)
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('a report computed from other bytes is never shown as this image analysis', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  globalThis.workbenchAPI = fixture.api
  // 旧版本号的分析（删除后重新导入会复用版本号）：版本号相同但内容指纹不同。
  const stale = { artifact_id: 'stale-analysis', basis: { ...basis,
      images: [{ image_id: image.image_id, version: 1, artifact_id: 'other', sha256: 'other-bytes' }] },
    output: { summary: '另一张图的分析', findings: [analysis.output.findings[0]],
      protected_features: [], limitations: [] } }
  fixture.api.getComicImageReports = async (_pid, _cid, kind) =>
    ({ items: kind === 'image-analyses' ? [stale] : [], next_cursor: null })
  fixture.api.getComicImageReport = async () => stale
  fixture.stored.set('legacy-image:1', { version: 7, state: {
    image: { image_id: image.image_id, version: 1 }, round_id: 'round-1', task: '检查当前画面',
    analysis_artifact_id: 'stale-analysis', selected_finding_ids: [], edit_scope: null,
    instruction: '', instruction_artifact_id: null, phase: 'editing',
    dismissed_finding_region_ids: [] } })
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image' && typeof el.props.onLoad === 'function').props.onLoad()
    await flush()
    assert.equal(content(host).includes('另一张图的分析'), false)
    assert.ok(content(host).includes('分析建议（0）'))
    // 指向旧内容的指针要就地清掉，避免每次打开都加载错报告。
    assert.equal(fixture.stored.get('legacy-image:1').state.analysis_artifact_id, null)
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('framing re-bases to the viewed version instead of turning into a dead button', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  globalThis.workbenchAPI = fixture.api
  // 复现线上状态：中央在看 v2，修改基础还停在 v1。
  fixture.api.getComicWorkbench = async () => ({ version: 9, state: {
    image: { image_id: image.image_id, version: 2 },
    edit_image: { image_id: image.image_id, version: 1 }, auto_analyze: false } })
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    // 版本条里的缩略图也带同一个 src，只有画布里的 <img> 有 onLoad。
    const canvasImage = src => find(host, el => el.tag === 'img' && el.props.src === src && typeof el.props.onLoad === 'function')
    // 恢复现场是"看 v2、改 v1"，所以画布里只有 v2 这一张。
    canvasImage('blob:result').props.onLoad()
    await nextTick()
    // 版本关系在视图栏说明，不再用面板里那行带文件名的句子。
    assert.ok(content(host).includes('修改基础 v1'))
    const frame = () => find(host, el => el.tag === 'button' && content(el) === '框选区域')
    assert.equal(frame().props.disabled, false)
    await frame().props.onClick(); await flush()
    const saved = fixture.calls.filter(item => item.action === 'workspace').at(-1)
    assert.equal(saved.state.edit_image.version, 2)
    assert.ok(find(host, el => el.props.class === 'wb-draw-status'))
    await frame().props.onClick(); await flush()
    assert.equal(find(host, el => el.props.class === 'wb-draw-status'), null)
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('import row deletes the viewed image only after the sheet confirms it', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image').props.onLoad()
    await nextTick()
    // 删除入口和导入按钮同在一行：先弹确认，确认前不发请求。
    await find(host, el => el.tag === 'button' && content(el) === '删除图片').props.onClick()
    await flush()
    assert.equal(fixture.calls.some(item => item.action === 'delete'), false)
    assert.ok(find(host, el => el.props['aria-label'] === '删除图片确认'))
    await find(host, el => el.tag === 'button' && content(el) === '确认删除').props.onClick()
    await flush()
    const call = fixture.calls.find(item => item.action === 'delete')
    assert.equal(call.imageId, 'legacy-image')
    assert.equal(call.body.conversation_id, 'conversation')
    assert.equal(call.body.expected_project_version, 4)
    assert.equal(find(host, el => el.props['aria-label'] === '删除图片确认'), null)
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('workbench separates locating, selected scope, browsing and adoption; compiled draft survives reopening', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  globalThis.workbenchAPI = fixture.api
  const props = { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] }
  const host = node('root'), app = renderer.createApp(Optimize, props)
  app.mount(host)
  try {
    await flush()
    find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image').props.onLoad()
    await nextTick()
    const locate = find(host, el => el.tag === 'button' && content(el) === '定位')
    locate.props.onClick(); await flush()
    assert.equal(fixture.calls.filter(item => item.action === 'edit' || item.action === 'analysis').length, 0)
    const checkbox = find(host, el => el.tag === 'input' && el.props.type === 'checkbox' && el.props.checked === false)
    checkbox.props.onChange(); await nextTick()
    const region = find(host, el => el.props['aria-label'] === '区域 1')
    region.props.onKeydown({ key: 'ArrowRight', shiftKey: false, preventDefault() {} })
    const requirement = await openSupplement(host)
    requirement.props['onUpdate:modelValue']('只修改已选区域'); requirement.props.onInput()
    await nextTick()
    await find(host, el => el.tag === 'button' && content(el) === '整理改图指令').props.onClick()
    await flush()
    const submitted = fixture.calls.find(item => item.action === 'edit').body
    assert.deepEqual(submitted.selected_finding_ids, ['edge'])
    assert.ok(Math.abs(submitted.edit_scope.regions[0].bounds[0] - 0.102) < 1e-8)
    assert.equal(submitted.image.version, 1)
    assert.equal('phase' in submitted, false)
    assert.ok(find(host, el => el.tag === 'pre' && content(el).includes('仅柔化光线')))
    assert.ok(find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image'))
    const newer = find(host, el => el.tag === 'button' && el.children.some(child => child.tag === 'strong' && content(child).trim() === 'v2'))
    assert.ok(newer, content(host))
    await newer.props.onClick(); await flush()
    assert.equal(fixture.calls.filter(item => item.action === 'adopt').length, 0)
    assert.ok(content(host).includes('修改基础 v1'))
    find(host, el => el.tag === 'img' && el.props.src === 'blob:result').props.onLoad()
    await nextTick()
    await find(host, el => el.tag === 'button' && content(el).includes('采用此版本')).props.onClick()
    assert.equal(fixture.calls.find(item => item.action === 'adopt').body.version, 2)
    assert.equal(fixture.stored.get('legacy-image:1').state.phase, 'review')
  } finally { app.unmount(); await flush() }
  const reopened = renderer.createApp(Optimize, props), secondHost = node('root')
  reopened.mount(secondHost)
  try {
    await flush()
    assert.ok(find(secondHost, el => el.tag === 'pre'))
    assert.ok(content(secondHost).includes('修改基础 v1'))
    assert.ok(find(secondHost, el => el.tag === 'img' && el.props.src === 'blob:result'))
    assert.equal(fixture.calls.filter(item => item.action === 'analysis').length, 0)
    // 指令已经整理好（phase=review）时框选也不能是死路：点击后退回编辑态并进入框选。
    await find(secondHost, el => el.tag === 'button' && content(el) === '框选区域').props.onClick()
    await flush()
    assert.ok(find(secondHost, el => el.props.class === 'wb-draw-status'))
  } finally { reopened.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('four selected suggestions remain visible and unresolved scope blocks compilation before mixed authorization', async () => {
  const report = { ...analysis, output: { ...analysis.output, findings: [analysis.output.findings[0],
    { ...analysis.output.findings[0], finding_id: 'detail', observation: '细部观察', region: [0.65, 0.1, 0.9, 0.4] },
    { finding_id: 'ambient', observation: '整体色彩观察', suggested_change: '调整整图色彩', confidence: 0.6 },
    { finding_id: 'light', observation: '整体光线观察', suggested_change: '柔化整图光线', confidence: 0.6 },
  ] } }
  const fixture = offlineWorkbench(report), previous = globalThis.workbenchAPI
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image').props.onLoad()
    for (let index = 1; index <= 4; index++) {
      find(host, el => el.props['aria-label'] === '加入建议 ' + index).props.onChange(); await nextTick()
    }
    for (const id of ['edge', 'detail', 'ambient', 'light']) assert.ok(find(host, el => el.props['data-requirement'] === 'finding:' + id))
    assert.match(content(host), /已选 4 条 · 区域 2 条 · 整图 0 条 · 待定位 2 条/)
    assert.ok(find(host, el => el.props['aria-label'] === '区域 1'))
    assert.equal(find(host, el => el.props['aria-label'] === '区域 2'), null)
    const detail = find(host, el => el.props['data-requirement'] === 'finding:detail')
    find(detail, el => el.props.class === 'wb-card-title').props.onClick(); await nextTick()
    assert.ok(find(host, el => el.props['aria-label'] === '区域 2'))
    assert.equal(find(host, el => el.props['aria-label'] === '区域 1'), null)
    assert.equal(find(host, el => el.props['aria-label'] === '区域 3'), null)
    await find(host, el => el.tag === 'button' && content(el) === '整理改图指令').props.onClick(); await flush()
    assert.equal(fixture.calls.filter(item => item.action === 'edit').length, 0)
    assert.match(content(find(host, el => el.props['data-operation'] === 'edit')), /2 条需求尚未确定/)
    for (const id of ['ambient', 'light']) {
      const card = find(host, el => el.props['data-requirement'] === 'finding:' + id)
      const title = find(card, el => el.props.class === 'wb-card-title')
      if (!title.props['aria-expanded']) { title.props.onClick(); await nextTick() }
      find(host, el => el.tag === 'select' && el.props['aria-label']?.endsWith('的修改范围')).props.onChange({ target: { value: 'whole' } })
      await nextTick()
    }
    const input = await openSupplement(host)
    input.props['onUpdate:modelValue']('保留原有布局'); input.props.onInput()
    find(host, el => el.props['aria-label'] === '补充要求作用范围').props.onChange({ target: { value: 'whole' } })
    await nextTick()
    await find(host, el => el.tag === 'button' && content(el) === '整理改图指令').props.onClick(); await flush()
    const request = fixture.calls.find(item => item.action === 'edit').body
    assert.equal(request.edit_scope.mode, 'mixed')
    assert.equal(request.edit_scope.regions.length, 2)
    assert.deepEqual(request.edit_scope.whole_finding_ids, ['ambient', 'light'])
    assert.deepEqual(request.edit_scope.instruction_target, { mode: 'whole' })
    assert.deepEqual(request.selected_finding_ids, ['edge', 'detail', 'ambient', 'light'])
    assert.ok(find(host, el => el.tag === 'pre'))
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('deleting an AI frame removes its demand, persists dismissal, and never silently broadens a deleted supplement target', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI, reportBefore = JSON.stringify(analysis)
  globalThis.workbenchAPI = fixture.api
  const props = { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] }
  const host = node('root'), app = renderer.createApp(Optimize, props)
  app.mount(host)
  try {
    await flush()
    find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image').props.onLoad()
    find(host, el => el.props['aria-label'] === '加入建议 1').props.onChange(); await nextTick()
    const input = await openSupplement(host)
    input.props['onUpdate:modelValue']('调整这一区域'); input.props.onInput()
    find(host, el => el.props['aria-label'] === '补充要求作用范围').props.onChange({ target: { value: 'region:finding:edge' } })
    find(host, el => el.props['aria-label'] === '区域 1').props.onFocus(); await nextTick()
    find(host, el => el.props['aria-label'] === '删除区域 1').props.onClick({ stopPropagation() {} }); await nextTick()
    assert.equal(find(host, el => el.props['aria-label'] === '区域 1'), null)
    assert.equal(find(host, el => el.props['data-requirement'] === 'finding:edge'), null)
    find(host, el => el.tag === 'button' && content(el).includes('撤销删除')).props.onClick(); await nextTick()
    assert.ok(find(host, el => el.props['aria-label'] === '区域 1'))
    find(host, el => el.props['aria-label'] === '区域 1').props.onKeydown({ key: 'Delete', preventDefault() {} })
    await nextTick()
    await input.props.onBlur(); await flush()
    assert.deepEqual(fixture.stored.get('legacy-image:1').state.dismissed_finding_region_ids, ['edge'])
    assert.deepEqual(fixture.stored.get('legacy-image:1').state.selected_finding_ids, [])
  } finally { app.unmount(); await flush() }
  const nextHost = node('root'), reopened = renderer.createApp(Optimize, props)
  reopened.mount(nextHost)
  try {
    await flush()
    find(nextHost, el => el.tag === 'img' && typeof el.props.onLoad === 'function').props.onLoad()
    assert.equal(find(nextHost, el => el.props['aria-label'] === '区域 1'), null)
    await openSupplement(nextHost)
    assert.equal(find(nextHost, el => el.props['aria-label'] === '补充要求作用范围').props.value, 'region:finding:edge')
    await find(nextHost, el => el.tag === 'button' && content(el) === '整理改图指令').props.onClick(); await flush()
    assert.equal(fixture.calls.filter(item => item.action === 'edit').length, 0)
    assert.match(content(find(nextHost, el => el.props['data-operation'] === 'edit')), /目标选区不存在/)
    assert.equal(JSON.stringify(analysis), reportBefore)
  } finally { reopened.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('manual frame deletion and undo restore original coordinates while retaining the written request', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  const manual = { region_id: 'manual', label: '手动1', bounds: [0.1, 0.2, 0.6, 0.8], finding_id: null, instruction: '' }
  fixture.stored.set('legacy-image:1', { version: 1, state: { image: basis.source_image, round_id: 'manual-round', task: basis.task_context.task,
    analysis_artifact_id: null, selected_finding_ids: [], edit_scope: { mode: 'regions', regions: [manual] },
    instruction: '仅修改框内', instruction_artifact_id: null, phase: 'editing' } })
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    find(host, el => el.props['aria-label'] === '区域 手动1').props.onKeydown({ key: 'Backspace', preventDefault() {} }); await nextTick()
    assert.equal(find(host, el => el.props['aria-label'] === '区域 手动1'), null)
    find(host, el => el.tag === 'button' && content(el).includes('撤销删除')).props.onClick(); await nextTick()
    assert.ok(find(host, el => el.props['aria-label'] === '区域 手动1'))
    await (await openSupplement(host)).props.onBlur(); await flush()
    assert.deepEqual(fixture.stored.get('legacy-image:1').state.edit_scope.regions[0], manual)
    assert.equal(fixture.stored.get('legacy-image:1').state.instruction, '仅修改框内')
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('manual-only editing retains a supplement target when an AI frame becomes a manual frame', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    find(host, el => el.tag === 'img' && typeof el.props.onLoad === 'function').props.onLoad()
    find(host, el => el.props['aria-label'] === '加入建议 1').props.onChange(); await nextTick()
    await openSupplement(host)
    find(host, el => el.props['aria-label'] === '补充要求作用范围').props.onChange({ target: { value: 'region:finding:edge' } })
    const input = find(host, el => el.props['aria-label'] === '本轮补充要求')
    input.props['onUpdate:modelValue']('只修改这个选区'); input.props.onInput()
    find(host, el => el.tag === 'button' && content(el) === '仅按我的需求修改').props.onClick(); await nextTick()
    await find(host, el => el.tag === 'button' && content(el) === '整理改图指令').props.onClick(); await flush()
    const request = fixture.calls.find(item => item.action === 'edit').body
    assert.equal(request.analysis_artifact_id, null)
    assert.deepEqual(request.selected_finding_ids, [])
    assert.equal(request.edit_scope.instruction_target.region_id, request.edit_scope.regions[0].region_id)
    assert.deepEqual(request.edit_scope.regions[0].bounds, analysis.output.findings[0].region)
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('a lost edit response queries the original Run and restores its instruction without resubmitting', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  const compile = fixture.api.createComicEditInstruction
  let request, completed = false
  fixture.api.createComicEditInstruction = async (...args) => {
    request = args[1]
    const result = await compile(...args)
    result.run_id = 'original-run'
    throw new TypeError('Failed to fetch')
  }
  fixture.api.getComicImageRequests = async () => request ? [{ id: 'original-run', status: completed ? 'completed' : 'running',
    state: { media_input: request }, error: null }] : []
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image').props.onLoad()
    find(host, el => el.props['aria-label'] === '加入建议 1').props.onChange(); await nextTick()
    await find(host, el => el.tag === 'button' && content(el) === '整理改图指令').props.onClick(); await flush()
    await find(host, el => el.tag === 'button' && content(el) === '核对原请求状态').props.onClick(); await flush()
    assert.equal(fixture.calls.filter(item => item.action === 'edit').length, 1)
    completed = true
    await find(host, el => el.tag === 'button' && content(el) === '核对原请求状态').props.onClick(); await flush()
    assert.equal(fixture.calls.filter(item => item.action === 'edit').length, 1)
    assert.ok(find(host, el => el.tag === 'pre'))
    assert.equal(fixture.stored.get('legacy-image:1').state.phase, 'review')
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

for (const status of ['needs_clarification', 'review_unavailable', 'needs_scope_confirmation']) test(`edit ${status} preserves demands and restores a waiting result without resubmitting`, async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  let request, resolution
  fixture.api.createComicEditInstruction = async (_pid, body) => {
    fixture.calls.push({ action: 'edit', body }); request = body
    resolution = { edit_status: status, run_id: 'waiting-edit', round_id: body.round_id,
      source_image: body.image, input_sha256: 'frozen-input',
      message: status === 'needs_clarification' ? '请确认框内需要修改的对象。' : '系统核验暂未完成，无需反复改写原文。',
      review_diagnostics: { stage: 'final_review', diagnostic_artifact_id: 'diagnostic',
        run_id: 'waiting-edit', source_image: body.image, round_id: body.round_id,
        issues: [{ code: 'missing', reason: '具体的一项需要处理', requirement_ids: ['finding:edge'],
          finding_ids: ['edge'], region_ids: ['finding:edge'], constraint_ids: [] }] },
      candidate: status === 'review_unavailable' ? { verified: false,
        output: { positive_prompt: '尚未核验的候选正文', negative_prompt: '', summary: '候选' } } : null,
      correction: { used: status === 'review_unavailable', stage: 'final_review' } }
    if (status === 'needs_scope_confirmation') Object.assign(resolution, {
      plan_fingerprint: 'bound-plan', edit_plan: { items: [], handling: [], questions: [],
        scope_proposals: [{ item_id: 'edge', target: '边缘', bounds: [.05, .1, .9, .9], reason: '需要连接框外部分', within_scope_alternative: '原范围内只修整一部分' }] } })
    return resolution
  }
  fixture.api.getComicImageRequests = async () => resolution ? [{ id: 'waiting-edit', status: 'waiting',
    state: { media_input: request, image_edit_resolution: resolution } }] : []
  globalThis.workbenchAPI = fixture.api
  const props = { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] }
  const host = node('root'), app = renderer.createApp(Optimize, props)
  app.mount(host)
  try {
    await flush()
    find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image').props.onLoad()
    find(host, el => el.props['aria-label'] === '加入建议 1').props.onChange(); await flush()
    await find(host, el => el.tag === 'button' && content(el) === '整理改图指令').props.onClick(); await flush()
    assert.match(content(host), status === 'needs_clarification' ? /需要确认具体需求/ : status === 'needs_scope_confirmation' ? /需要确认影响范围/ : /系统核验暂未完成/)
    assert.match(content(host), /具体的一项需要处理/)
    assert.equal(fixture.stored.get('legacy-image:1').state.phase, 'editing')
    assert.equal(fixture.stored.get('legacy-image:1').state.instruction_artifact_id, null)
    assert.equal(find(host, el => el.tag === 'button' && content(el).includes('确认并复制')), null)
  } finally { app.unmount(); await flush() }
  const reopenedHost = node('root'), reopened = renderer.createApp(Optimize, props)
  reopened.mount(reopenedHost)
  try {
    await flush()
    assert.match(content(reopenedHost), /具体的一项需要处理/)
    assert.equal(fixture.calls.filter(item => item.action === 'edit').length, 1)
    assert.equal(fixture.calls.filter(item => item.action === 'confirm').length, 0)
    if (status === 'needs_clarification') {
      await find(reopenedHost, el => el.tag === 'button' && content(el) === '查看并处理待确认问题').props.onClick(); await flush()
      assert.equal(fixture.calls.filter(item => item.action === 'edit').length, 1)
    }
    await find(reopenedHost, el => el.tag === 'button' && content(el) === '返回需求').props.onClick(); await flush()
    const input = await openSupplement(reopenedHost)
    input.props['onUpdate:modelValue']('只改变已定位对象的颜色'); input.props.onInput(); await flush()
    assert.equal(find(reopenedHost, el => el.props['data-operation'] === 'edit-resolution'), null)
  } finally { reopened.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

for (const scenario of ['local', 'whole', 'multiple']) test(`scope approval generates a prompt once with a collapsed plan; ${scenario}`, async () => {
  const whole = scenario === 'whole'
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  const compile = fixture.api.createComicEditInstruction
  let pending, submitted, release, submissions = 0
  fixture.api.createComicEditInstruction = async (...args) => {
    submissions++
    const body = args[1]
    if (body.plan_confirmation) {
      submitted = body
      await new Promise(resolve => { release = resolve })
      const result = await compile(...args)
      result.scope.unified_plan = { items: [], handling: [], questions: [],
        scope_proposals: [{ item_id: 'edge', target: '边缘', bounds: [.05, .1, .9, .9] }] }
      return result
    }
    pending = body
    return { edit_status: 'needs_scope_confirmation', run_id: 'scope-run', plan_fingerprint: 'scope-fingerprint',
      source_image: body.image, round_id: body.round_id, message: '确认影响范围',
      review_diagnostics: { stage: 'plan', issues: [] },
      edit_plan: { contract: 'unified-edit-plan-2', items: Array.from({ length: 20 }, (_, index) => ({ item_id: String(index), target: '边缘', attribute: '清晰度', desired_state: '清晰', preserve: ['原色'] })),
        handling: [], questions: [], scope_proposals: [{ item_id: 'edge', target: whole ? '整图亮度' : '边缘', bounds: whole ? [0, 0, 1, 1] : [.05, .1, .9, .9], reason: '连接部位在框外', within_scope_alternative: '只改框内部分' },
          ...(scenario === 'multiple' ? [{ item_id: 'light', target: '光线', bounds: [0, 0, 1, 1], reason: '整图调整' }] : [])] } }
  }
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image').props.onLoad()
    find(host, el => el.props['aria-label'] === '加入建议 1').props.onChange(); await flush()
    await find(host, el => el.tag === 'button' && content(el) === '整理改图指令').props.onClick(); await flush()
    const preview = find(host, el => el.props['aria-label'] === '统一修改计划')
    assert.equal(preview.tag, 'details'); assert.notEqual(preview.props.open, true)
    const button = find(host, el => el.tag === 'button' && content(el) === '确认范围并生成提示词')
    assert.equal(button.props.disabled, true)
    await button.props.onClick(); await flush()
    assert.equal(submissions, 1)
    const check = find(host, el => el.tag === 'input' && el.props.value === 'edge' && el.props.type === 'checkbox')
    check.props['onUpdate:modelValue'](['edge']); await flush()
    if (scenario === 'multiple') {
      assert.equal(button.props.disabled, true)
      await button.props.onClick(); await flush()
      assert.equal(submissions, 1)
      const second = find(host, el => el.tag === 'input' && el.props.value === 'light' && el.props.type === 'checkbox')
      second.props['onUpdate:modelValue'](['edge', 'light']); await flush()
    }
    assert.equal(button.props.disabled, false)
    const action = button.props.onClick(); await flush()
    await button.props.onClick(); await flush()
    assert.equal(submissions, 2)
    assert.equal(submitted.edit_scope.region_semantics, 'locator')
    assert.deepEqual(submitted.edit_scope.regions, pending.edit_scope.regions)
    assert.deepEqual(submitted.plan_confirmation, { run_id: 'scope-run', fingerprint: 'scope-fingerprint', accepted_item_ids: scenario === 'multiple' ? ['edge', 'light'] : ['edge'] })
    release(); await action; await flush()
    assert.equal(find(host, el => el.tag === 'button' && content(el) === '确认范围并生成提示词'), null)
    assert.match(content(host), /仅柔化光线并改善选中边缘/)
    assert.equal(find(host, el => el.props.class === 'wb-proposed-region'), null)
    assert.equal(fixture.calls.filter(item => item.action === 'edit').length, 1)
    assert.equal(fixture.calls.filter(item => item.action === 'confirm').length, 0)
  } finally { release?.(); app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('old pending plans can generate from saved demands without replaying obsolete questions or frames', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  const compile = fixture.api.createComicEditInstruction
  let requests = 0, original
  fixture.api.createComicEditInstruction = async (...args) => {
    requests++
    const body = args[1]
    if (requests > 1) {
      assert.deepEqual(body.edit_scope, original.edit_scope)
      assert.equal(body.plan_confirmation, undefined)
      return compile(...args)
    }
    original = body
    return { edit_status: 'needs_clarification', run_id: 'obsolete-run', source_image: body.image,
      round_id: body.round_id, message: '旧范围不明确', review_diagnostics: { stage: 'plan', issues: [] },
      edit_plan: { contract: 'unified-edit-plan-1', items: [], handling: [], questions: [],
        scope_proposals: [{ item_id: 'keep', target: '只需保留的内容', bounds: [0, 0, .5, .5] }] } }
  }
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image').props.onLoad()
    find(host, el => el.props['aria-label'] === '加入建议 1').props.onChange(); await flush()
    await find(host, el => el.tag === 'button' && content(el) === '整理改图指令').props.onClick(); await flush()
    assert.equal(requests, 1)
    assert.equal(find(host, el => el.props.class === 'wb-proposed-region'), null)
    assert.doesNotMatch(content(host), /旧范围不明确/)
    await find(host, el => el.tag === 'button' && content(el) === '按当前需求生成提示词').props.onClick(); await flush()
    assert.equal(requests, 2)
    assert.match(content(host), /仅柔化光线并改善选中边缘/)
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

for (const failed of [false, true]) test(`fresh edited result checks regression once and preserves version choice; failure=${failed}`, async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  const result = { ...image, version: 2, artifact_id: 'result', parent: basis.source_image, instruction_artifact_id: 'edit' }
  fixture.api.getComicImages = async () => [result]
  fixture.api.getComicImageVersions = async () => ({ versions: [result, image], next_before: null, selection: null })
  fixture.api.getComicWorkbench = async () => ({ version: 0, state: { auto_analyze: true } })
  fixture.api.getComicImageReports = async () => ({ items: [], next_cursor: null })
  fixture.api.compareComicImages = async (_pid, body) => {
    fixture.calls.push({ action: 'comparison', body })
    if (failed) throw new TypeError('连接中断')
    return { output: { summary: '目标部分实现，但出现退步', changes: [
      { check_id: 'one', status: 'satisfied', evidence: '边缘已清晰' },
      { check_id: 'two', status: 'not_satisfied', evidence: '颜色未改变' }],
      preservation: [{ check_id: 'keep', status: 'not_satisfied', evidence: '背景结构受损' }],
      new_problems: ['新增重影'], recommendation: 'retain_before', limitations: [] } }
  }
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), props = { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [], newImport: { image_id: image.image_id, version: 2 } }
  const app = renderer.createApp(Optimize, props)
  app.mount(host)
  try {
    await flush()
    assert.equal(fixture.calls.filter(item => item.action === 'comparison').length, 0)
    find(host, el => el.tag === 'img' && el.props.src === 'blob:result').props.onLoad(); await flush()
    const sent = fixture.calls.find(item => item.action === 'comparison').body
    assert.deepEqual(sent.before, basis.source_image)
    assert.deepEqual(sent.after, props.newImport)
    assert.equal(sent.instruction_artifact_id, 'edit')
    if (failed) assert.match(content(host), /连接中断/)
    else {
      assert.match(content(host), /已解决：边缘已清晰/)
      assert.match(content(host), /未解决：颜色未改变/)
      assert.match(content(host), /新增重影/)
      assert.match(content(host), /建议保留旧版/)
    }
    assert.equal(fixture.calls.filter(item => item.action === 'analysis' || item.action === 'adopt').length, 0)
    assert.equal(fixture.calls.filter(item => item.action === 'comparison').length, 1)
  } finally { app.unmount(); await flush() }
  const reopened = renderer.createApp(Optimize, { ...props, newImport: undefined }), nextHost = node('root')
  reopened.mount(nextHost)
  try {
    await flush()
    find(nextHost, el => el.tag === 'img' && el.props.src === 'blob:result').props.onLoad(); await flush()
    assert.equal(fixture.calls.filter(item => item.action === 'comparison').length, 1)
  } finally { reopened.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('file read failure is visible with retry and matching-original recovery, without model submission', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  fixture.api.getArtifactContentUrl = async () => { throw new Error('原文件缺失 · 错误编号：ERR-OFFLINE') }
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: '检查当前画面', legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    assert.ok(content(host).includes('ERR-OFFLINE'))
    assert.ok(find(host, el => el.tag === 'button' && content(el) === '重试读取'))
    assert.ok(find(host, el => el.tag === 'button' && content(el) === '重传缺失原文件'))
    assert.equal(fixture.calls.filter(item => item.action === 'analysis').length, 0)
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('stale Prompt links never recreate removed image families or an endless loading canvas', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  fixture.api.getComicImages = async () => []
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation',
    task: '检查当前画面', legacyImages: [image] })
  app.mount(host)
  try {
    await flush()
    assert.ok(content(host).includes('导入第一张图片'))
    assert.ok(!content(host).includes('正在读取图片'))
    assert.equal(fixture.calls.length, 0)
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('purged viewing and edit-base versions retain demands, never fetch files or imply valid adoption, and permit another family', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  const missing = { ...image, available: false, lifecycle_state: 'purged', unavailable_reason: '原图片已彻底删除' }
  const missingBase = { ...missing, version: 2, artifact_id: 'missing-v2' }
  const good = { ...image, image_id: 'healthy', artifact_id: 'healthy-v1', filename: 'healthy.png', available: true }
  const selection = { selected_version: 1, selection_version: 4, available: false, unavailable_reason: missing.unavailable_reason }
  fixture.api.getComicImages = async () => [{ ...missingBase, selection }, good]
  fixture.api.getComicWorkbench = async () => ({ version: 7, state: { image: missing, edit_image: missingBase, auto_analyze: true } })
  fixture.api.getComicImageVersions = async id => ({ versions: id === good.image_id ? [good] : [missingBase, missing], next_before: null, selection: id === good.image_id ? null : selection })
  fixture.stored.set(draftKey(missingBase), { version: 3, state: { ...basis, image: missingBase, round_id: 'old-round', task: '本轮任务',
    analysis_artifact_id: 'deleted-analysis', selected_finding_ids: [], edit_scope: null, instruction: '用户保留的修改要求', instruction_artifact_id: null, phase: 'editing' } })
  fixture.api.getArtifactContentUrl = async id => { fixture.calls.push({ action: 'file', id }); return 'blob:' + id }
  globalThis.workbenchAPI = fixture.api
  const states = [], host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: '本轮任务', legacyImages: [], onState: value => states.push(value) })
  app.mount(host)
  try {
    await flush()
    assert.match(content(host), /查看 v1 · 图片不可用 · 修改基础 v2（不可用）/)
    assert.match(content(host), /原采用版本 · 不可用/)
    assert.match(content(host), /图片已彻底删除，请导入新素材/)
    const supplement = await openSupplement(host)
    assert.equal(supplement.value, '用户保留的修改要求')
    assert.equal(states.at(-1).adopted, false)
    assert.equal(fixture.calls.some(item => ['file', 'analysis', 'edit', 'workspace'].includes(item.action)), false)
    assert.equal(find(host, el => el.tag === 'button' && content(el) === '重传缺失原文件'), null)
    find(host, el => el.tag === 'select').props.onChange({ target: { value: good.image_id } })
    await flush()
    assert.ok(find(host, el => el.tag === 'img' && el.props.src === 'blob:healthy-v1'))
    assert.equal((await openSupplement(host)).value, '')
    assert.ok(fixture.calls.filter(item => item.action === 'workspace').every(item => item.state.edit_image.image_id === good.image_id))
    assert.equal(fixture.stored.get(draftKey(good))?.state.analysis_artifact_id ?? null, null)
    assert.equal(fixture.calls.some(item => item.action === 'adopt'), false)
    assert.equal(fixture.stored.get(draftKey(missingBase)).state.instruction, '用户保留的修改要求')
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('an unavailable history report cannot blank out saved demands or trigger regeneration', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  const saved = { image: basis.source_image, round_id: 'round', task: basis.task_context.task, analysis_artifact_id: 'gone-report',
    selected_finding_ids: [], edit_scope: null, instruction: '继续保留这条修改要求', instruction_artifact_id: null, phase: 'editing' }
  fixture.stored.set(draftKey(image), { version: 2, state: saved })
  fixture.api.getComicImageReports = async () => { throw new Error('历史报告已回收 · ERR-HISTORY') }
  fixture.api.getComicImageReport = async () => { throw new Error('历史报告已回收 · ERR-HISTORY') }
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    assert.match(content(host), /部分历史报告不可用，修改需求已保留/)
    const supplement = await openSupplement(host)
    assert.equal(supplement.value, saved.instruction)
    assert.equal(fixture.stored.get(draftKey(image)).state.analysis_artifact_id, 'gone-report')
    assert.equal(fixture.calls.some(item => ['analysis', 'edit'].includes(item.action)), false)
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('failed analysis still permits manual compilation, with immediate saving and local error feedback', async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI
  fixture.api.getComicImageReports = async () => ({ items: [], next_cursor: null })
  fixture.stored.set('legacy-image:1', { version: 1, state: { image: basis.source_image, round_id: 'manual-round',
    task: basis.task_context.task, analysis_artifact_id: null, selected_finding_ids: [],
    edit_scope: { mode: 'regions', regions: [{ region_id: 'manual-region', bounds: [0.1, 0.2, 0.6, 0.8], instruction: '', finding_id: null }] },
    instruction: '选中区域的边框改为蓝色', instruction_artifact_id: null, phase: 'editing' } })
  fixture.api.analyzeComicImage = async () => { throw new Error('账户欠费 · 错误编号：ERR-VISION') }
  const compile = fixture.api.createComicEditInstruction
  fixture.api.createComicEditInstruction = async (_pid, request) => {
    fixture.calls.push({ action: 'failed-edit', body: request })
    throw Object.assign(new Error('本轮要求未通过核验 · 错误编号：ERR-EDIT'), { httpStatus: 400 })
  }
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image').props.onLoad(); await flush()
    await find(host, el => el.tag === 'button' && content(el) === '分析图片').props.onClick(); await flush()
    assert.match(content(find(host, el => el.props['data-operation'] === 'analysis')), /ERR-VISION/)
    const save = fixture.api.saveComicReviewDraft
    let finishSave
    fixture.api.saveComicReviewDraft = async (...args) => { await new Promise(resolve => { finishSave = resolve }); return save(...args) }
    const action = find(host, el => el.tag === 'button' && content(el) === '整理改图指令').props.onClick()
    await flush()
    assert.ok(find(host, el => el.tag === 'button' && content(el) === '正在保存修改需求…' && el.props.disabled))
    finishSave(); await action; await flush()
    const localError = find(host, el => el.props['data-operation'] === 'edit')
    assert.match(content(localError), /ERR-EDIT/)
    localError.props.onClick(); await nextTick()
    assert.match(content(host), /选区已保留/)
    const submitted = fixture.calls.find(item => item.action === 'failed-edit').body
    assert.equal(submitted.analysis_artifact_id, null)
    assert.equal(submitted.edit_scope.regions[0].region_id, 'manual-region')
    fixture.api.saveComicReviewDraft = save; fixture.api.createComicEditInstruction = compile
    await find(host, el => el.tag === 'button' && content(el) === '整理改图指令').props.onClick(); await flush()
    assert.ok(find(host, el => el.tag === 'pre'))
    assert.ok(find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image'))
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

for (const auto of [true, false]) test(`fresh Shot upload analyzes only after decoding; preference=${auto}; history never resubmits`, async () => {
  const fixture = offlineWorkbench(), previous = globalThis.workbenchAPI, consumed = []
  fixture.api.getComicWorkbench = async () => ({ version: 0, state: { auto_analyze: auto } })
  fixture.api.getComicImageReports = async () => ({ items: [], next_cursor: null })
  globalThis.workbenchAPI = fixture.api
  const props = { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task,
    legacyImages: [], newImport: basis.source_image, onImportConsumed: value => consumed.push(value) }
  const host = node('root'), app = renderer.createApp(Optimize, props)
  app.mount(host)
  try {
    await flush()
    assert.equal(fixture.calls.filter(item => item.action === 'analysis').length, 0)
    const img = find(host, el => el.tag === 'img' && el.props.src === 'blob:legacy-image')
    img.props.onLoad(); await flush()
    img.props.onLoad(); await flush()
    assert.equal(fixture.calls.filter(item => item.action === 'analysis').length, auto ? 1 : 0)
    assert.deepEqual(consumed, [basis.source_image])
  } finally { app.unmount(); await flush() }
  const reopened = renderer.createApp(Optimize, { ...props, newImport: undefined }), nextHost = node('root')
  reopened.mount(nextHost)
  try {
    await flush()
    find(nextHost, el => el.tag === 'img' && el.props.src === 'blob:result').props.onLoad(); await flush()
    assert.equal(fixture.calls.filter(item => item.action === 'analysis').length, auto ? 1 : 0)
  } finally { reopened.unmount(); await flush(); globalThis.workbenchAPI = previous }
})

test('real API client sends fixed image/report identities and distinct explicit writes; restoring reports only reads', async () => {
  const previousDocument = globalThis.document, previousFetch = globalThis.fetch
  globalThis.document = { querySelector: () => ({ content: 'offline-token' }) }
  const apiCode = transpile(readFileSync(new URL('../src/services/core.ts', import.meta.url), 'utf8').replaceAll('import.meta.env', '({DEV:false})'))
  const api = await import(moduleUrl(apiCode))
  const calls = []
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, method: options.method ?? 'GET', body: options.body ? JSON.parse(options.body) : null })
    return new Response(JSON.stringify(url.includes('image-analyses') ? { items: [analysis] } : { items: [instruction] }), { status: 200 })
  }
  try {
    const common = { conversation_id: 'conversation', expected_project_version: 7 }
    await api.getComicImageAnalyses('project', 'conversation')
    await api.getComicEditInstructions('project', 'conversation')
    await api.analyzeComicImage('project', { ...common, request_id: 'analyze-id', image: basis.source_image, task: '检查当前画面' })
    await api.createComicEditInstruction('project', { ...common, request_id: 'edit-id', image: basis.source_image,
      task: '检查当前画面', analysis_artifact_id: 'analysis', selected_finding_ids: ['edge'], instruction: '仅调整光线' })
    await api.confirmComicEditInstruction('project', 'edit', { ...common, request_id: 'confirm-id' })
    assert.deepEqual(calls.slice(0, 2).map(item => item.method), ['GET', 'GET'])
    assert.ok(calls.slice(0, 2).every(item => item.url.includes('conversation_id=conversation')))
    assert.deepEqual(calls.slice(2).map(item => item.method), ['POST', 'POST', 'POST'])
    assert.deepEqual(calls[2].body.image, basis.source_image)
    assert.deepEqual(calls[3].body.selected_finding_ids, ['edge'])
    assert.equal(calls[3].body.analysis_artifact_id, 'analysis')
    assert.equal(calls[4].body.instruction_artifact_id, 'edit')
    assert.deepEqual(calls.slice(2).map(item => item.body.request_id), ['analyze-id', 'edit-id', 'confirm-id'])
    globalThis.fetch = async () => new Response(JSON.stringify({ safe_message: '视觉模型调用失败',
      provider_error_code: 'Arrearage', error_id: 'ERR-VISION', trace_id: 'trace-vision' }), { status: 400 })
    await assert.rejects(api.analyzeComicImage('project', { ...common, request_id: 'blocked', image: basis.source_image, task: '检查当前画面' }),
      error => error.message.includes('账户欠费') && error.message.includes('Arrearage') && error.errorId === 'ERR-VISION' && error.traceId === 'trace-vision')
    const diagnostics = { stage: 'final_review', diagnostic_artifact_id: 'audit', issues: [{ finding_ids: ['edge'], region_ids: ['region'], reason: '遗漏修改' }] }
    globalThis.fetch = async () => new Response(JSON.stringify({ safe_message: '系统整理结果未通过核验', error_id: 'ERR-EDIT', review_diagnostics: diagnostics }), { status: 400 })
    await assert.rejects(api.createComicEditInstruction('project', { ...common, request_id: 'review-failed', image: basis.source_image,
      task: '检查当前画面', analysis_artifact_id: 'analysis', selected_finding_ids: ['edge'] }), error => assert.deepEqual(error.reviewDiagnostics, diagnostics) === undefined)
  } finally { globalThis.document = previousDocument; globalThis.fetch = previousFetch }
})


test('a completed analysis with no findings shows the actual conclusion without inventing defects', async () => {
  const report = { ...analysis, output: { ...analysis.output, findings: [], summary: '当前图像符合本轮需求，没有发现明确偏差。' } }
  const fixture = offlineWorkbench(report), previous = globalThis.workbenchAPI
  globalThis.workbenchAPI = fixture.api
  const host = node('root'), app = renderer.createApp(Optimize, { projectId: 'project', conversationId: 'conversation', task: basis.task_context.task, legacyImages: [] })
  app.mount(host)
  try {
    await flush()
    await find(host, el => el.tag === 'button' && content(el).startsWith('分析建议')).props.onClick(); await nextTick()
    const result = find(host, el => el.props['aria-label'] === '图片分析结果')
    assert.ok(result)
    assert.equal(result.tag, 'section')
    assert.equal(result.props.role, 'status')
    assert.match(content(result), /分析完成 · 未发现明确偏差/)
    assert.match(content(result), /当前图像符合本轮需求/)
    assert.match(content(result), /仍可直接框选/)
    assert.equal(find(result, el => el.tag === 'details'), null)
    assert.equal(find(host, el => el.props['aria-label'] === '加入建议 1'), null)
    assert.equal(fixture.calls.filter(call => call.action === 'analysis').length, 0)
  } finally { app.unmount(); await flush(); globalThis.workbenchAPI = previous }
})
