<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ArrowUp, Download, Film, LoaderCircle, RefreshCw } from 'lucide-vue-next'
import { storyImageKey, type StoryWorkbench } from '../../domains/comic/storyWorkbench'
import { createVideoWorkbench, videoStatus } from '../../domains/comic/videoWorkbench'

const props = defineProps<{ workbench: StoryWorkbench }>()
const emit = defineEmits<{ go: [page: string] }>()
const video = createVideoWorkbench(() => props.workbench.state.view, undefined, () => !props.workbench.dirty.value)
const state = video.state
const shots = computed(() => state.view?.manifest?.shots ?? [])
const currentShot = computed(() => shots.value.find(shot => shot.shot_id === state.shotId))
const task = video.currentTask
const image = computed(() => {
  const first = task.value?.snapshot.shots[0]
  const frozen = task.value?.snapshot.images?.find(item => item.image_id === first?.image_id && Number(item.version) === first?.image_version)
  if (frozen) return frozen
  if (task.value && task.value.snapshot.content_sha256 !== state.view?.content_sha256) return undefined
  const shot = currentShot.value
  if (!shot) return undefined
  const key = storyImageKey({ image_id: shot.image_id, version: shot.image_version })
  return props.workbench.state.view?.story?.fixed_images.find(item => storyImageKey(item) === key)
})
const frame = computed(() => image.value ? props.workbench.state.previews[image.value.artifact_id] ?? '' : '')
const frameError = computed(() => image.value ? props.workbench.state.previewErrors[image.value.artifact_id] : '')
const targetDuration = computed(() => (task.value?.snapshot.shots ?? (state.scope === 'story' ? shots.value : currentShot.value ? [currentShot.value] : []))
  .reduce((sum, shot) => sum + (shot.duration_seconds ?? 0), 0))
const caption = computed(() => task.value ? `故事 v${task.value.snapshot.version} · ${task.value.scope === 'story' ? '完整作品' : `镜头 ${task.value.snapshot.shots[0]?.sequence_number} 试片`}`
  : state.scope === 'story' ? '完整作品预览' : `镜头 ${currentShot.value?.sequence_number ?? '—'} 试片`)
const running = computed(() => video.submittingCurrent.value || video.busy.value || state.loadingMedia)
const ready = video.canRun
const accessKey = ref(''), secretKey = ref(''), savingCredentials = ref(false), credentialError = ref('')
watch(() => [props.workbench.state.owner, props.workbench.state.view?.story?.content_sha256], () => {
  if (props.workbench.state.owner) void video.load(props.workbench.state.owner)
}, { immediate: true })
watch(() => image.value?.artifact_id, () => { if (image.value) void props.workbench.showImages([image.value]) }, { immediate: true })
onBeforeUnmount(video.dispose)
function useStoryPrompt(): void {
  const shot = currentShot.value
  state.prompt = state.scope === 'story' ? props.workbench.state.view?.export_text ?? '' : shot
    ? `动作：${shot.action}\n运镜：${shot.camera}\n目标时长：${shot.duration_seconds} 秒\n衔接：${shot.transition}` : ''
  video.saveDraft()
}
async function saveCredentials(): Promise<void> {
  if (savingCredentials.value) return
  savingCredentials.value = true; credentialError.value = ''
  try {
    await video.configureCredentials(accessKey.value.trim(), secretKey.value.trim())
    accessKey.value = ''; secretKey.value = ''
    await video.load()
  } catch (error) {
    credentialError.value = error instanceof Error ? error.message : '密钥设置未完成'
  } finally { savingCredentials.value = false }
}
</script>

<template>
  <section class="vs" aria-label="视频生成">
    <header class="vs-head"><h3>视频生成</h3><span v-if="state.view?.confirmed">故事 v{{ state.view.story_version }} · {{ shots.length }} 个镜头</span><button class="ui-button quiet sm" :disabled="state.loading" aria-label="刷新视频任务" @click="video.load()"><RefreshCw :size="15" /></button></header>
    <div v-if="state.error" class="vs-notice" role="alert"><p>{{ state.error }}</p><button class="ui-button sm" @click="video.load()">查询原请求</button></div>
    <div v-if="state.view && !state.view.confirmed" class="vs-notice"><p>确认故事镜头后即可开始制作视频。</p><button class="ui-button sm" @click="emit('go', 'story')">返回故事编排</button></div>
    <form v-if="state.view?.credentials_configurable && !state.view.session_credentials_configured" class="vs-credentials" @submit.prevent="saveCredentials">
      <strong>连接火山引擎</strong><p>填写密钥页面上方的 Access Key。密钥仅保存在当前工作台进程中，重启后需要重新填写。</p>
      <label>Access Key ID<input v-model="accessKey" autocomplete="off" spellcheck="false" required /></label>
      <label>Secret Access Key<input v-model="secretKey" type="password" autocomplete="off" spellcheck="false" required /></label>
      <button class="ui-button sm" :disabled="savingCredentials" type="submit">{{ savingCredentials ? '正在保存' : '保存密钥' }}</button>
      <p v-if="credentialError" role="alert">{{ credentialError }}</p>
    </form>
    <div v-if="state.view?.confirmed" class="vs-purpose"><strong>{{ state.scope === 'story' ? '完整作品' : '单镜头试片' }}</strong><span>{{ state.scope === 'story' ? '故事与全部分镜已随作品带入，结合成片提示词生成一条完整视频。' : '仅测试当前镜头的表现。' }}</span><button v-if="state.scope === 'shot'" class="ui-button sm" @click="video.select(state.shotId, 'story')">返回完整作品</button></div>
    <div class="vs-layout">
      <aside class="vs-shot-panel" aria-label="故事镜头">
        <div class="vs-shot-heading"><strong>全部分镜</strong><small>{{ shots.length }} 个镜头</small></div>
        <ol class="vs-shot-list">
          <li v-for="shot in shots" :key="shot.shot_id"><button type="button" :aria-label="`查看镜头 ${shot.sequence_number}`" :class="{ selected: state.shotId === shot.shot_id }" :aria-current="state.shotId === shot.shot_id ? 'true' : undefined" @click="video.select(shot.shot_id)"><span class="vs-shot-number">{{ String(shot.sequence_number).padStart(2, '0') }}</span><span class="vs-shot-copy"><strong>镜头 {{ shot.sequence_number }}</strong><span>{{ shot.action }}</span><small>{{ shot.duration_seconds }} 秒</small></span></button></li>
        </ol>
        <p v-if="!shots.length" class="vs-muted">尚无已确认镜头</p>
        <button class="ui-button quiet sm vs-return" @click="emit('go', 'export')">查看导出材料</button>
      </aside>
      <div class="vs-workspace">
        <div class="vs-player-head"><strong>{{ caption }}</strong><span>{{ state.view?.model_label ?? '小云雀' }}</span><a v-if="state.videoUrl" class="ui-button quiet sm" :href="state.videoUrl" :download="`video-v${task?.snapshot.version}.mp4`"><Download :size="14" /> 下载</a></div>
        <div class="vs-player" :class="{ 'is-running': running }" :aria-busy="running" aria-label="视频播放区">
          <video v-if="state.videoUrl" :key="task?.artifact_id ?? ''" :src="state.videoUrl" :poster="frame || undefined" controls playsinline preload="metadata" @error="state.mediaError = '视频无法播放，请重新读取原视频文件。'" />
          <img v-else-if="frame" :src="frame" :alt="`镜头 ${currentShot?.sequence_number} 的参考图`" />
          <div v-else class="vs-player-empty"><Film :size="34" :stroke-width="1" /><span>{{ state.loading ? '正在读取故事分镜' : '完整作品将在这里播放' }}</span></div>
          <div v-if="running" class="vs-loading" role="status"><span class="vs-scan" aria-hidden="true" /><LoaderCircle :size="22" class="vs-spinner" /><strong>{{ video.submittingCurrent.value ? '正在提交' : state.loadingMedia ? '正在读取视频' : videoStatus[task?.phase ?? 'queued'] }}</strong><span>视频生成后将在这里播放</span></div>
          <div v-if="state.mediaError" class="vs-player-error" role="alert"><span>{{ state.mediaError }}</span><button class="ui-button sm" @click="video.refreshMedia()">重新读取视频</button></div>
        </div>
        <p v-if="frameError && !state.videoUrl" class="vs-config-note" role="alert">{{ frameError }}</p>
        <div class="vs-player-foot"><span v-if="task">{{ videoStatus[task.phase] ?? task.phase }}</span><span v-else>等待运行</span><span>目标 {{ targetDuration }} 秒</span><button v-if="task?.can_query && !running" class="ui-button quiet sm" @click="video.query()">继续查询</button></div>
        <details v-if="currentShot" class="vs-shot-details"><summary>镜头 {{ currentShot.sequence_number }} 内容</summary><dl><div><dt>动作</dt><dd>{{ currentShot.action }}</dd></div><div><dt>运镜</dt><dd>{{ currentShot.camera }}</dd></div><div><dt>衔接</dt><dd>{{ currentShot.transition }}</dd></div></dl></details>
        <form class="vs-chat" aria-label="视频生成输入" @submit.prevent="video.run()">
          <div class="vs-chat-head"><label for="video-prompt">{{ state.scope === 'story' ? '成片提示词' : '试片提示词' }}</label><button type="button" class="ui-button quiet sm" :disabled="!shots.length" @click="useStoryPrompt">{{ state.scope === 'story' ? '填入导出提示词' : '填入镜头提示词' }}</button></div>
          <textarea id="video-prompt" v-model="state.prompt" rows="4" placeholder="粘贴导出的完整提示词，或补充整部作品的节奏、对白、字幕与声音要求……" @input="video.saveDraft()" @keydown.ctrl.enter.prevent="video.run()" @keydown.meta.enter.prevent="video.run()" />
          <div class="vs-chat-footer"><span :class="{ 'vs-over-limit': video.promptLength.value > 2000 }">{{ video.promptLength.value }} / 2000{{ state.scope === 'story' ? '（含故事分镜）' : '' }}</span><span v-if="running">生成期间可查看分镜</span><button class="ui-button primary" type="submit" :disabled="!ready"><ArrowUp :size="15" />{{ video.submittingCurrent.value ? '正在提交' : state.scope === 'story' ? '生成完整作品' : '生成镜头试片' }}</button></div>
          <p v-if="state.view?.unavailable_reason" class="vs-config-note">{{ state.view.unavailable_reason }}</p><p v-if="video.promptLength.value > 2000" class="vs-config-note" role="alert">故事与成片提示词合计超过小云雀的 2000 字限制，请精简故事或补充要求后运行。</p>
        </form>
        <details v-if="state.scope === 'story'" class="vs-instructions"><summary>查看完整制作指令</summary><p>{{ video.submittedPrompt.value }}</p></details>
        <details class="vs-advanced"><summary>单镜头试片（可选）</summary><p>需要单独测试画面表现时，可为当前镜头生成试片。</p><button class="ui-button sm" :disabled="!currentShot || state.scope === 'shot'" @click="video.select(state.shotId, 'shot')">仅测试当前镜头</button></details>
        <section v-if="state.view?.tasks.length" class="vs-history" aria-label="视频生成记录"><h4>生成记录</h4><ol><li v-for="item in state.view.tasks" :key="item.run_id"><button type="button" class="vs-history-title" :aria-pressed="task?.run_id === item.run_id" @click="video.chooseTask(item)"><span>故事 v{{ item.snapshot.version }} · {{ item.scope === 'story' ? '完整作品' : `镜头 ${item.snapshot.shots[0]?.sequence_number} 试片` }}</span><small>{{ videoStatus[item.phase] ?? item.phase }}</small></button><details><summary>查看制作指令</summary><p>{{ item.submitted_prompt ?? item.prompt }}</p></details><p v-if="item.error" class="vs-record-error">{{ item.error }}</p></li></ol></section>
      </div>
    </div>
  </section>
</template>

<style scoped>
.vs-credentials { display:grid; gap:var(--space-2); margin-bottom:var(--space-4); padding:var(--space-3); border:1px solid var(--border-muted); border-radius:var(--radius-panel); font-size:var(--font-control); }.vs-credentials p { margin:0; color:var(--text-secondary); }.vs-credentials label { display:grid; gap:4px; }.vs-credentials input { box-sizing:border-box; width:100%; padding:8px; border:1px solid var(--border-muted); border-radius:var(--radius-control); background:var(--surface); color:var(--text-primary); }.vs-credentials button { justify-self:start; }
.vs { min-width:0; max-width:1180px; margin:0 auto; }
.vs-head { display:flex; align-items:center; gap:var(--space-3); margin-bottom:var(--space-5); }.vs-head h3 { margin:0; font-size:var(--font-section); font-weight:600; }.vs-head > span { color:var(--text-muted); font-size:var(--font-kicker); }.vs-head > button { margin-left:auto; }
.vs-purpose { display:flex; align-items:baseline; flex-wrap:wrap; gap:var(--space-2) var(--space-3); margin-bottom:var(--space-5); font-size:var(--font-control); }.vs-purpose strong { font-weight:500; }.vs-purpose span, .vs-shot-heading small { color:var(--text-muted); font-size:var(--font-kicker); }.vs-instructions, .vs-advanced { margin-top:var(--space-3); font-size:var(--font-kicker); color:var(--text-secondary); }.vs-instructions summary, .vs-advanced summary { cursor:pointer; }.vs-instructions p { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.65; }
.vs-layout { display:grid; grid-template-columns:184px minmax(0,1fr); gap:var(--space-5); align-items:start; }
.vs-shot-panel { min-width:0; border-right:1px solid var(--border-muted); padding-right:var(--space-3); }.vs-shot-heading { display:flex; align-items:center; justify-content:space-between; margin-bottom:var(--space-3); font-size:var(--font-control); }
.vs-shot-list { list-style:none; padding:0; margin:0; max-height:62vh; overflow-y:auto; scrollbar-width:thin; }.vs-shot-list li + li { margin-top:var(--space-2); }
.vs-shot-list button { display:flex; align-items:center; gap:var(--space-2); width:100%; padding:var(--space-3) var(--space-2); border:1px solid transparent; border-radius:var(--radius-control); background:transparent; color:var(--text-secondary); text-align:left; font:inherit; cursor:pointer; }.vs-shot-list button:hover { background:var(--surface-subtle); }.vs-shot-list button.selected { background:var(--canvas-inset); border-color:var(--border-muted); color:var(--text-primary); }
.vs-shot-number { font-size:var(--font-kicker); color:var(--text-muted); font-variant-numeric:tabular-nums; }.vs-shot-copy { display:grid; gap:4px; min-width:0; flex:1; }.vs-shot-copy strong { font-size:var(--font-control); font-weight:500; }.vs-shot-copy > span { overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; font-size:var(--font-kicker); line-height:1.5; }.vs-shot-copy small, .vs-muted { font-size:var(--font-kicker); color:var(--text-muted); }.vs-return { margin-top:var(--space-4); }
.vs-workspace { min-width:0; }.vs-player-head { display:flex; align-items:center; gap:var(--space-3); min-height:32px; margin-bottom:var(--space-2); font-size:var(--font-control); }.vs-player-head strong { font-weight:500; }.vs-player-head > span { margin-left:auto; color:var(--text-muted); font-size:var(--font-kicker); }
.vs-player { position:relative; display:flex; justify-content:center; align-items:center; width:100%; aspect-ratio:16/9; max-height:52vh; min-height:220px; overflow:hidden; border-radius:var(--radius-panel); background:#101318; }.vs-player > img, .vs-player > video { width:100%; height:100%; object-fit:contain; }.vs-player.is-running > img { opacity:.35; filter:blur(2px); }.vs-player-empty { display:grid; justify-items:center; gap:var(--space-3); color:#9299a3; font-size:var(--font-control); }
.vs-loading { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:var(--space-3); color:white; background:rgba(16,19,24,.45); }.vs-loading strong { font-size:var(--font-control); font-weight:500; }.vs-loading > span:last-child { font-size:var(--font-kicker); color:#c7cbd3; }.vs-scan { position:absolute; inset:0; pointer-events:none; background:linear-gradient(110deg, transparent 30%,rgba(180,194,230,.12) 49%,transparent 68%); background-size:230% 100%; animation:vs-scan 3s linear infinite; }.vs-spinner { animation:vs-turn 1.4s linear infinite; }
.vs-player-foot { display:flex; align-items:center; gap:var(--space-3); min-height:34px; font-size:var(--font-kicker); color:var(--text-muted); }.vs-player-foot > span:nth-child(2) { margin-left:auto; }.vs-player-error { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:var(--space-3); padding:var(--space-4); background:rgba(16,19,24,.8); color:white; font-size:var(--font-control); }
.vs-shot-details { margin-bottom:var(--space-4); font-size:var(--font-kicker); color:var(--text-secondary); }.vs-shot-details summary, .vs-history summary { cursor:pointer; }.vs-shot-details dl { display:grid; gap:var(--space-2); margin-bottom:0; }.vs-shot-details dl > div { display:grid; grid-template-columns:35px minmax(0,1fr); gap:var(--space-3); }.vs-shot-details dt { color:var(--text-muted); }.vs-shot-details dd { margin:0; white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.65; }
.vs-chat { border:1px solid var(--border-muted); border-radius:var(--radius-panel); background:var(--surface); padding:var(--space-3) var(--space-4); }.vs-chat-head { display:flex; justify-content:space-between; align-items:center; gap:var(--space-2); font-size:var(--font-control); }.vs-chat-head label { font-weight:500; }
.vs-chat textarea { display:block; box-sizing:border-box; width:100%; min-height:96px; max-height:240px; resize:vertical; margin:var(--space-2) 0; padding:4px 0; border:0; background:transparent; color:var(--text-primary); font:inherit; font-size:var(--font-control); line-height:1.7; white-space:pre-wrap; overflow-wrap:anywhere; }.vs-chat textarea:focus { outline:0; box-shadow:none; }.vs-chat:focus-within { border-color:var(--accent); box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 9%,transparent); }
.vs-chat-footer { display:flex; align-items:center; gap:var(--space-3); color:var(--text-muted); font-size:var(--font-kicker); }.vs-chat-footer button { margin-left:auto; }.vs-over-limit { color:var(--danger); }.vs-config-note { margin:var(--space-2) 0 0; color:var(--text-muted); font-size:var(--font-kicker); line-height:1.6; }
.vs-notice { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); margin-bottom:var(--space-4); padding:var(--space-3); background:var(--surface-subtle); border-left:2px solid var(--border-strong); font-size:var(--font-control); }.vs-notice p { margin:0; overflow-wrap:anywhere; }
.vs-history { margin-top:var(--space-5); }.vs-history h4 { margin:0 0 var(--space-3); font-weight:500; font-size:var(--font-control); }.vs-history ol { list-style:none; padding:0; margin:0; }.vs-history li { border-top:1px solid var(--border-muted); padding:var(--space-3) 0; }.vs-history-title { display:flex; justify-content:space-between; gap:var(--space-3); width:100%; padding:0; border:0; background:transparent; color:var(--text-secondary); font:inherit; font-size:var(--font-control); text-align:left; cursor:pointer; }.vs-history-title[aria-pressed=true] { color:var(--accent); }.vs-history-title small, .vs-history details { font-size:var(--font-kicker); color:var(--text-muted); }.vs-history details { margin-top:var(--space-2); }.vs-history p { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.65; }.vs-record-error { color:var(--danger); font-size:var(--font-kicker); }
.vs button:focus-visible, .vs a:focus-visible, .vs summary:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
@keyframes vs-scan { from { background-position:160% 0; } to { background-position:-60% 0; } } @keyframes vs-turn { to { transform:rotate(360deg); } }
@media(max-width:850px) { .vs-layout { grid-template-columns:minmax(0,1fr); gap:var(--space-3); }.vs-shot-panel { padding:0; border-right:0; }.vs-shot-heading { margin-bottom:var(--space-2); }.vs-shot-list { display:flex; gap:var(--space-2); overflow-x:auto; max-height:none; padding-bottom:var(--space-2); }.vs-shot-list li { flex:0 0 140px; }.vs-shot-list li + li { margin-top:0; }.vs-shot-copy > span, .vs-shot-list svg { display:none; }.vs-return { display:none; }.vs-player { min-height:180px; }.vs-head { margin-bottom:var(--space-3); } }
@media(prefers-reduced-motion:reduce) { .vs-scan, .vs-spinner { animation:none; } }

.vs { max-width:1440px; color:var(--paper); }
.vs-head { padding:16px 20px; border:3px solid var(--ink); background:#203967; box-shadow:5px 5px 0 #0009; }
.vs-head h3 { color:var(--paper); font-size:22px; font-weight:800; }
.vs-head > span, .vs-purpose span { color:#d2def3; }
.vs-head > button { color:var(--paper); }
.vs-purpose { padding:12px 16px; border:2px solid #536b96; background:#162952; }
.vs-layout { grid-template-columns:220px minmax(0,1fr); gap:18px; }
.vs-shot-panel { padding:0 0 16px; border:3px solid var(--ink); background:var(--paper); color:var(--ink); box-shadow:5px 5px 0 #0009; }
.vs-shot-heading { padding:11px 14px; border-bottom:3px solid var(--ink); background:var(--ink-2); color:var(--gold-light); }
.vs-shot-heading small { color:#d4d4d4; }
.vs-shot-list, .vs-shot-panel > p, .vs-return { margin-left:12px; margin-right:12px; }
.vs-shot-list button { border:2px solid transparent; border-radius:0; }
.vs-shot-list button.selected { border-color:var(--ink); background:var(--gold-light); color:var(--ink); box-shadow:3px 3px 0 var(--ink); }
.vs-player-head { margin:0; padding:10px 14px; border:3px solid var(--ink); background:var(--ink-2); color:var(--gold-light); }
.vs-player-head > span { color:#d4d4d4; }
.vs-player { border:3px solid var(--ink); border-top:0; border-radius:0; background:#080f20; box-shadow:5px 5px 0 #0009; }
.vs-player-foot { margin:0 0 18px; padding:6px 12px; background:#203967; color:#e4edff; }
.vs-chat, .vs-credentials, .vs-notice, .vs-shot-details, .vs-instructions, .vs-advanced, .vs-history { padding:16px; border:3px solid var(--ink); border-radius:0; background:var(--paper); color:var(--ink); box-shadow:5px 5px 0 #0009; }
.vs-chat-head { padding-bottom:10px; border-bottom:2px dashed var(--paper-3); }
.vs-chat textarea { padding:10px; border:2px solid var(--ink); background:#fffdf5; }
.vs-chat:focus-within { outline:3px solid var(--gold); outline-offset:2px; }
.vs-shot-details, .vs-instructions, .vs-advanced, .vs-history { margin-top:14px; }
.vs-shot-details summary, .vs-instructions summary, .vs-advanced summary { color:var(--ink); font-weight:700; }
@media(max-width:850px) { .vs-layout { grid-template-columns:minmax(0,1fr); } .vs-shot-panel { padding:0 0 12px; } .vs-shot-list { margin:0 12px; } .vs-chat-footer { flex-wrap:wrap; } }
</style>
