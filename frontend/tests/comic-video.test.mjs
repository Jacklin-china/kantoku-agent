import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
import { computed, createSSRApp, h, ref } from 'vue'
import { compileScript, parse } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'

const transpile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText
const moduleUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const coreUrl = moduleUrl('export class CoreApiError extends Error { constructor(message,status=0) { super(message); this.httpStatus=status } }')
const source = readFileSync(new URL('../src/domains/comic/videoWorkbench.ts', import.meta.url), 'utf8')
const controllerUrl = moduleUrl(transpile(source).replace(/from (["'])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replace(/from (["'])\.\.\/\.\.\/services\/core\1/g, `from ${JSON.stringify(coreUrl)}`))
const { createVideoWorkbench, videoStatus } = await import(controllerUrl)
const { CoreApiError } = await import(coreUrl)
const clone = value => JSON.parse(JSON.stringify(value))
const storage = new Map()
globalThis.sessionStorage = { getItem: key => storage.get(key) ?? null, setItem: (key,value) => storage.set(key,value), removeItem: key => storage.delete(key) }
test.beforeEach(() => storage.clear())
const shot = { shot_id: 'one', sequence_number: 1, image_id: 'image', image_version: 1, action: '抬手接住灯笼', camera: '缓慢跟随', duration_seconds: 4, transition: '切向街口', filename: 'one.png' }
const second = { ...shot, shot_id: 'two', sequence_number: 2, action: '提灯走向街口' }
const initial = { story_id: 'story', story_version: 1, content_sha256: 'hash1', confirmed: true, ready: true, provider: 'xiaoyunque',
  model_label: '小云雀 2.0', story_context: '生成一条完整故事视频。\n抬手接住灯笼，提灯走向街口。', unavailable_reason: null, manifest: { shots: [shot, second] }, tasks: [] }
const story = { story: { anchor_project_id: 'project', version: 1, content_sha256: 'hash1', fixed_images: [] }, anchor_project_id: 'project', export_text: '抬手接住灯笼，提灯走向街口。' }
function task(phase = 'queued', fields = {}) { return { run_id: 'run', request_id: 'request', phase, status: phase === 'succeeded' ? 'completed' : 'waiting',
  can_query: phase === 'generating', prompt: '跟随灯笼运动', scope: 'story', artifact_id: phase === 'succeeded' ? 'video' : null, error: null,
  snapshot: { story_id: 'story', version: 1, content_sha256: 'hash1', title: '灯笼', shots: [shot, second] }, ...fields } }
function deferred() { let resolve, reject; const promise = new Promise((yes,no) => { resolve=yes; reject=no }); return { promise, resolve, reject } }
function fixture(canSubmit = () => true) {
  let view = clone(initial), storyView = clone(story)
  const calls = []
  const api = {
    getComicVideo: async owner => { calls.push(['get',owner]); return clone(view) },
    getComicProject: async id => ({ project: { project_id:id,current_version:8 } }),
    submitComicVideo: async (owner,body) => { calls.push(['submit',owner,clone(body)]); const result = task('queued',{ request_id:body.request_id }); view.tasks.unshift(result); return result },
    queryComicVideo: async (owner,body) => { calls.push(['query',owner,clone(body)]); return task('generating') },
    getArtifactContentUrl: async id => { calls.push(['media',id]); return 'blob:'+id },
  }
  const controller = createVideoWorkbench(() => storyView, api, canSubmit)
  return { controller, api, calls, setView: next => { view=clone(next) }, setStory: next => { storyView=clone(next) } }
}

test('confirmed shots bind one video request to the exact story version without changing the story', async () => {
  const f=fixture(); await f.controller.load('chat')
  try {
    assert.equal(f.controller.state.shotId,'one'); assert.equal(f.controller.canRun.value,false)
    f.controller.state.prompt='箭头先行，主角随后起身。'
    assert.equal(await f.controller.run(),true)
    const submitted=f.calls.find(call=>call[0]==='submit')[2]
    assert.equal(submitted.content_sha256,'hash1'); assert.equal(submitted.expected_story_version,1)
    assert.equal(submitted.shot_id,null); assert.equal(submitted.scope,'story')
    assert.equal(f.controller.busy.value,true); assert.equal(f.controller.state.view.story_version,1)
    assert.equal(f.controller.state.prompt,'')
  } finally { f.controller.dispose() }
})

test('double clicking run submits only once and a queued task uses server status', async () => {
  const f=fixture(), pending=deferred(); f.api.submitComicVideo=async (...args)=>{f.calls.push(['submit',...args]);return pending.promise}
  await f.controller.load('chat'); f.controller.state.prompt='移动镜头'
  const first=f.controller.run(); const secondRun=f.controller.run()
  await Promise.resolve(); await Promise.resolve()
  assert.equal(await secondRun,false); assert.equal(f.calls.filter(call=>call[0]==='submit').length,1)
  assert.equal(f.controller.submittingCurrent.value,true)
  f.controller.select('two'); assert.equal(f.controller.submittingCurrent.value,true)
  pending.resolve(task()); assert.equal(await first,true)
  assert.equal(f.controller.currentTask.value.phase,'queued')
  f.controller.select('one','shot'); assert.equal(f.controller.currentTask.value,undefined)
  f.controller.dispose()
})

test('unknown network result persists the request id and reload recovers without another submit', async () => {
  const f=fixture(); let requestId
  f.api.submitComicVideo=async (owner,body)=>{requestId=body.request_id;f.calls.push(['submit']);throw new CoreApiError('结果未知')}
  await f.controller.load('chat'); f.controller.state.prompt='运动'
  assert.equal(await f.controller.run(),false); assert.equal(f.controller.state.pendingRequestId,requestId)
  f.controller.dispose()
  const reopened=fixture(); reopened.setView({...clone(initial),tasks:[task('generating',{request_id:requestId})]})
  await reopened.controller.load('chat')
  try { assert.equal(reopened.controller.state.pendingRequestId,''); assert.equal(reopened.calls.some(call=>call[0]==='submit'),false) }
  finally { reopened.controller.dispose() }
})

test('drafts are isolated by story version and shot while completed videos restore from backend', async () => {
  const f=fixture(); await f.controller.load('chat')
  f.controller.select('one','shot')
  f.controller.state.prompt='第一镜草稿'; f.controller.select('two'); f.controller.state.prompt='第二镜草稿'
  f.controller.select('one'); assert.equal(f.controller.state.prompt,'第一镜草稿')
  f.setView({...clone(initial),story_version:2,content_sha256:'hash2',tasks:[task('succeeded')]})
  await f.controller.load()
  try {
    assert.equal(f.controller.state.prompt,''); assert.equal(f.controller.currentTask.value,undefined)
    f.controller.chooseTask(f.controller.state.view.tasks[0]); await f.controller.refreshMedia()
    assert.equal(f.controller.currentTask.value.snapshot.version,1); assert.equal(f.controller.state.videoUrl,'blob:video')
  } finally { f.controller.dispose() }
})

test('late submit response cannot replace the next conversation or its loading state', async () => {
  const f=fixture(), pending=deferred()
  f.api.submitComicVideo=async ()=>pending.promise
  await f.controller.load('old'); f.controller.state.prompt='旧需求'; const original=f.controller.run()
  await Promise.resolve(); await Promise.resolve()
  await f.controller.load('new'); pending.resolve(task()); await original
  try { assert.equal(f.controller.state.owner,'new'); assert.equal(f.controller.state.view.tasks.length,0); assert.equal(f.controller.state.submitting,false) }
  finally { f.controller.dispose() }
})

test('unconfirmed input, disabled provider, long prompt and unresolved billing block generation', async () => {
  for (const change of [{confirmed:false},{ready:false},{tasks:[task('unknown')]},{tasks:[task('interrupted')]}]) {
    const f=fixture(); f.setView({...clone(initial),...change}); await f.controller.load('chat'); f.controller.state.prompt='移动'
    try { assert.equal(await f.controller.run(),false); assert.equal(f.calls.some(call=>call[0]==='submit'),false) }
    finally { f.controller.dispose() }
  }
  const f=fixture(); await f.controller.load('chat'); f.controller.state.prompt='长'.repeat(2001)
  try { assert.equal(f.controller.canRun.value,false); assert.equal(await f.controller.run(),false) }
  finally { f.controller.dispose() }
})

test('completed video is downloaded once and stale media cannot enter a different selection', async () => {
  const f=fixture(), media=deferred(); f.api.getArtifactContentUrl=async ()=>media.promise
  f.setView({...clone(initial),tasks:[task('succeeded')]})
  const loaded=f.controller.load('chat'); await Promise.resolve(); await Promise.resolve()
  f.controller.select('two','shot'); media.resolve('blob:old-video'); await loaded
  try { assert.equal(f.controller.state.videoUrl,''); assert.equal(f.controller.currentTask.value,undefined) }
  finally { f.controller.dispose() }
})

test('browsing storyboard shots preserves the whole-work prompt, result and loading context', async () => {
  const f=fixture(); await f.controller.load('chat')
  try {
    assert.equal(f.controller.state.scope,'story')
    f.controller.state.prompt='整部作品配轻柔音乐，最后淡出。'
    f.controller.select('two')
    assert.equal(f.controller.state.scope,'story')
    assert.equal(f.controller.state.prompt,'整部作品配轻柔音乐，最后淡出。')
    assert.equal(f.controller.submittedPrompt.value,initial.story_context+'\n成片要求：\n'+f.controller.state.prompt)
    f.controller.state.prompt=story.export_text
    assert.equal(f.controller.submittedPrompt.value,initial.story_context)
    f.controller.state.prompt=story.export_text+'\n配乐跟随剧情。'
    assert.equal(f.controller.submittedPrompt.value,initial.story_context+'\n成片要求：\n配乐跟随剧情。')
    f.controller.state.prompt=story.export_text
    f.controller.state.view.tasks=[task('succeeded')]
    f.controller.chooseTask(f.controller.state.view.tasks[0]); f.controller.select('one')
    assert.equal(f.controller.currentTask.value.run_id,'run')
    f.controller.saveDraft(); f.controller.dispose()
    const reopened=fixture(); await reopened.controller.load('chat')
    try { assert.equal(reopened.controller.state.prompt,story.export_text); assert.equal(reopened.controller.state.scope,'story') }
    finally { reopened.controller.dispose() }
  } finally { f.controller.dispose() }
})

test('combined story and user prompt length is checked before submission without truncation', async () => {
  const f=fixture(); f.setView({...clone(initial),story_context:'完整故事'.repeat(480)})
  await f.controller.load('chat'); f.controller.state.prompt='配乐'.repeat(100)
  try {
    assert.ok(f.controller.promptLength.value>2000)
    assert.equal(await f.controller.run(),false)
    assert.equal(f.controller.state.prompt,'配乐'.repeat(100))
    assert.equal(f.calls.some(call=>call[0]==='submit'),false)
  } finally { f.controller.dispose() }
})

test('unsaved story edits block every run entry and saving enables the same draft', async () => {
  const saved = ref(false), f = fixture(() => saved.value)
  await f.controller.load('chat'); f.controller.state.prompt = '缓慢推进'
  try {
    assert.equal(f.controller.canRun.value, false)
    assert.equal(await f.controller.run(), false)
    assert.equal(f.calls.some(call => call[0] === 'submit'), false)
    saved.value = true
    assert.equal(f.controller.canRun.value, true)
    assert.equal(await f.controller.run(), true)
  } finally { f.controller.dispose() }
})

test('actual video template has a prompt input and real-state animation with no demo or explanatory clutter', async () => {
  const provided=fixture(); await provided.controller.load('chat')
  provided.controller.state.view.tasks=[task('generating')]
  const bridgeUrl=moduleUrl(`import {computed} from ${JSON.stringify(import.meta.resolve('vue'))}; export const videoStatus=${JSON.stringify(videoStatus)}; let provided; export function setController(value){provided=value} export function createVideoWorkbench(){return provided}`)
  const bridge=await import(bridgeUrl); bridge.setController({...provided.controller,load:async()=>{}})
  const storyUrl=moduleUrl('export const storyImageKey=image=>image.image_id+":"+image.version')
  const viewUrl=moduleUrl(transpile(compileScript(parse(readFileSync(new URL('../src/components/comic/VideoStage.vue',import.meta.url),'utf8')).descriptor,{id:'video-test',inlineTemplate:true}).content)
    .replace(/from (["'])vue\1/g,`from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace(/from (["'])lucide-vue-next\1/g,`from ${JSON.stringify(import.meta.resolve('lucide-vue-next'))}`)
    .replace(/from (["'])\.\.\/\.\.\/domains\/comic\/videoWorkbench\1/g,`from ${JSON.stringify(bridgeUrl)}`)
    .replace(/from (["'])\.\.\/\.\.\/domains\/comic\/storyWorkbench\1/g,`from ${JSON.stringify(storyUrl)}`))
  const {default:VideoStage}=await import(viewUrl)
  try {
    const workbench={state:{owner:'chat',view:story,previews:{},previewErrors:{}},dirty:computed(()=>false),showImages:async()=>{}}
    const html=await renderToString(createSSRApp({render:()=>h(VideoStage,{workbench})}))
    assert.match(html,/成片提示词/); assert.match(html,/aria-label="视频播放区"/); assert.match(html,/正在生成/)
    assert.match(html,/生成完整作品/); assert.match(html,/全部分镜/); assert.match(html,/单镜头试片/)
    assert.match(html,/抬手接住灯笼/); assert.doesNotMatch(html,/界面壳|演示数据|不会发起任何生成请求|构建运动轨迹|[0-9]+%/)
    const old = task('succeeded')
    old.snapshot.images=[{artifact_id:'old-image',image_id:'image',version:'1',sha256:'old'}]
    provided.controller.state.view={...clone(initial),content_sha256:'hash2',story_version:2,tasks:[old]}
    provided.controller.chooseTask(old); provided.controller.state.videoUrl='blob:original-video'
    workbench.state.previews={'old-image':'blob:original-reference'}
    const completed=await renderToString(createSSRApp({render:()=>h(VideoStage,{workbench})}))
    assert.match(completed,/<video[^>]*src="blob:original-video"[^>]*poster="blob:original-reference"[^>]*controls/)
    assert.match(completed,/故事 v1/)
    provided.controller.state.view.credentials_configurable=true
    provided.controller.state.view.session_credentials_configured=false
    const credentials=await renderToString(createSSRApp({render:()=>h(VideoStage,{workbench})}))
    assert.match(credentials,/Access Key ID/); assert.match(credentials,/Secret Access Key/)
    assert.match(credentials,/type="password"/); assert.match(credentials,/重启后需要重新填写/)
    provided.controller.state.view={...clone(initial),confirmed:false,manifest:null,tasks:[]}
    const unconfirmed=await renderToString(createSSRApp({render:()=>h(VideoStage,{workbench})}))
    assert.match(unconfirmed,/确认故事镜头后即可开始/)
    assert.doesNotMatch(unconfirmed,/故事与全部分镜已随作品带入/)
  } finally { provided.controller.dispose() }
})
