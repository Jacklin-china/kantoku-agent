import { computed, reactive } from 'vue'
import * as core from '../../services/core'
import type { ComicStoryView, ComicVideoTask, ComicVideoView } from '../../services/core'

type VideoApi = Pick<typeof core, 'getComicVideo' | 'submitComicVideo' | 'queryComicVideo' | 'getComicProject' | 'getArtifactContentUrl' | 'configureVideoCredentials'>
const activePhases = new Set(['queued', 'generating'])
export const videoStatus: Record<string, string> = { queued: '等待生成', generating: '正在生成', succeeded: '已生成',
  failed: '生成失败', unknown: '结果待核对', interrupted: '提交已中断', unavailable: '视频不可用', cancelled: '已取消' }

/** 只投影服务端任务；浏览器保存输入草稿和未决请求编号，不保存或推算生成结果。 */
export function createVideoWorkbench(story: () => ComicStoryView | null, api: VideoApi = core, canSubmit: () => boolean = () => true) {
  const state = reactive({ owner: '', view: null as ComicVideoView | null, loading: false, submitting: false,
    error: '', prompt: '', scope: 'story' as 'shot' | 'story', shotId: '', activeRunId: '',
    pendingRequestId: '', videoUrl: '', mediaError: '', loadingMedia: false, submittingKey: '' })
  let epoch = 0, sequence = 0, stopped = false, timer: ReturnType<typeof setTimeout> | null = null
  let draftKey = '', mediaKey = '', mediaSequence = 0, recoveryPolls = 0
  const pendingKey = () => `kantoku-video-pending:${state.owner}`
  const currentTask = computed(() => state.view?.tasks.find(task => task.run_id === state.activeRunId)
    ?? state.view?.tasks.find(task => task.snapshot.content_sha256 === state.view?.content_sha256 && task.scope === state.scope
      && (state.scope === 'story' || task.snapshot.shots.some(shot => shot.shot_id === state.shotId))))
  const busy = computed(() => !!currentTask.value && activePhases.has(currentTask.value.phase))
  const contextKey = () => JSON.stringify([state.owner, state.view?.content_sha256, state.scope, state.scope === 'shot' ? state.shotId : 'all'])
  const submittingCurrent = computed(() => state.submitting && state.submittingKey === contextKey())
  const contextBlocked = computed(() => state.view?.tasks.some(task => ['unknown', 'interrupted'].includes(task.phase)
    || (activePhases.has(task.phase) && task.snapshot.content_sha256 === state.view?.content_sha256
      && task.scope === state.scope && (state.scope === 'story' || task.snapshot.shots.some(shot => shot.shot_id === state.shotId)))))
  const submittedPrompt = computed(() => {
    if (state.scope === 'shot') return state.prompt
    let addition = state.prompt
    const copied = story()?.export_text?.trim()
    if (copied && addition.trim().startsWith(copied)) addition = addition.trim().slice(copied.length).trimStart()
    return (state.view?.story_context ?? '') + (addition.trim() ? '\n成片要求：\n' + addition : '')
  })
  const promptLength = computed(() => Array.from(submittedPrompt.value).length)
  const canRun = computed(() => canSubmit() && !!state.view?.confirmed && !!state.view.ready && !!state.prompt.trim()
    && promptLength.value <= 2000 && (state.scope === 'shot' || !!state.view.story_context)
    && !state.submitting && !state.pendingRequestId && !state.loading && !contextBlocked.value
    && (state.scope === 'story' || !!state.shotId))

  function stopTimer(): void { if (timer) clearTimeout(timer); timer = null }
  function releaseMedia(): void { mediaSequence++; mediaKey = ''; if (state.videoUrl) URL.revokeObjectURL(state.videoUrl); state.videoUrl = ''; state.mediaError = ''; state.loadingMedia = false }
  function saveDraft(): void { try { if (draftKey) sessionStorage.setItem(draftKey, state.prompt) } catch { /* 本地草稿不可写不改变任务状态。 */ } }
  function select(shotId: string, scope = state.scope): void {
    const key = `kantoku-video-draft:${state.owner}:${state.view?.content_sha256}:${scope}:${scope === 'story' ? 'all' : shotId}`
    saveDraft(); state.shotId = shotId; state.scope = scope
    if (key !== draftKey) {
      state.activeRunId = ''; draftKey = key
      try { state.prompt = sessionStorage.getItem(draftKey)
        ?? (scope === 'story' ? sessionStorage.getItem(`kantoku-video-draft:${state.owner}:${state.view?.content_sha256}:story:${shotId}`) : null) ?? '' } catch { state.prompt = '' }
    }
    void refreshMedia()
  }
  async function refreshMedia(): Promise<void> {
    const artifact = currentTask.value?.artifact_id ?? ''
    if (artifact === mediaKey && !state.mediaError) return
    releaseMedia(); mediaKey = artifact
    if (!artifact) return
    const selected = ++mediaSequence, ownerEpoch = epoch
    state.loadingMedia = true
    try {
      const url = await api.getArtifactContentUrl(artifact, true)
      if (ownerEpoch !== epoch || selected !== mediaSequence || stopped) { if (url) URL.revokeObjectURL(url); return }
      if (!url) throw new Error('视频文件暂不可读取')
      state.videoUrl = url
    } catch (error) { if (selected === mediaSequence && ownerEpoch === epoch) state.mediaError = error instanceof Error ? error.message : '视频读取失败' }
    finally { if (selected === mediaSequence && ownerEpoch === epoch) state.loadingMedia = false }
  }
  function chooseTask(task: ComicVideoTask): void { state.activeRunId = task.run_id; void refreshMedia() }
  function rememberPending(id: string): void {
    state.pendingRequestId = id
    try { if (id) sessionStorage.setItem(pendingKey(), id); else sessionStorage.removeItem(pendingKey()) } catch { /* 仍可读取后端原任务。 */ }
  }
  function schedule(): void {
    stopTimer()
    if (!stopped && (state.view?.tasks.some(task => activePhases.has(task.phase)) || (state.pendingRequestId && recoveryPolls < 5))) {
      timer = setTimeout(() => { void load() }, 4000)
    }
  }
  async function load(owner = state.owner): Promise<void> {
    if (!owner || stopped) return
    if (owner !== state.owner) {
      saveDraft(); stopTimer(); epoch++; releaseMedia(); state.owner = owner; state.view = null
      state.prompt = ''; state.shotId = ''; state.scope = 'story'; state.activeRunId = ''; state.error = ''; state.submitting = false; draftKey = ''
      try { state.pendingRequestId = sessionStorage.getItem(pendingKey()) ?? '' } catch { state.pendingRequestId = '' }
      recoveryPolls = 0
    }
    const ownerEpoch = epoch, requestSequence = ++sequence
    state.loading = true
    try {
      const view = await api.getComicVideo(owner)
      if (!view || stopped || ownerEpoch !== epoch || requestSequence !== sequence) return
      if (view.story_version < (state.view?.story_version ?? 0)) return
      const previousHash = state.view?.content_sha256
      state.view = view
      state.error = ''
      if (previousHash !== view.content_sha256 || !view.manifest?.shots.some(shot => shot.shot_id === state.shotId)) select(view.manifest?.shots[0]?.shot_id ?? '')
      if (state.pendingRequestId) {
        const original = view.tasks.find(task => task.request_id === state.pendingRequestId)
        if (original) { rememberPending(''); state.error = ''; recoveryPolls = 0 }
        else { recoveryPolls++; state.error = '原请求结果尚未确定，请查询原请求；不会自动重新生成。' }
      }
      const anchor = story()?.anchor_project_id
      for (const task of view.tasks.filter(task => task.can_query && task.status === 'waiting' && task.phase === 'generating')) {
        if (!anchor || ownerEpoch !== epoch) break
        await api.queryComicVideo(owner, { conversation_id: owner, anchor_project_id: anchor, run_id: task.run_id })
      }
      await refreshMedia()
    } catch (error) { if (ownerEpoch === epoch) state.error = error instanceof Error ? error.message : '视频任务读取失败' }
    finally { if (ownerEpoch === epoch && requestSequence === sequence) { state.loading = false; schedule() } }
  }
  async function run(): Promise<boolean> {
    if (!canRun.value) return false
    const source = story(), view = state.view
    if (!source?.story || !view?.content_sha256 || source.story.content_sha256 !== view.content_sha256) {
      state.error = '故事版本已变化，请重新读取当前故事。'; return false
    }
    const ownerEpoch = epoch, owner = state.owner, prompt = state.prompt, requestId = crypto.randomUUID()
    const shotId = state.shotId, scope = state.scope
    state.submitting = true; state.submittingKey = contextKey(); state.error = ''; let submitted = false
    try {
      const project = await api.getComicProject(source.story.anchor_project_id)
      if (ownerEpoch !== epoch || stopped) return false
      if (!project) throw new Error('故事所属作品暂不可读取')
      rememberPending(requestId); submitted = true
      const task = await api.submitComicVideo(owner, { conversation_id: owner, request_id: requestId,
        anchor_project_id: source.story.anchor_project_id, expected_project_version: project.project.current_version,
        expected_story_version: view.story_version, content_sha256: view.content_sha256,
        prompt, scope, shot_id: scope === 'shot' ? shotId : null })
      if (ownerEpoch !== epoch || stopped) return false
      rememberPending('')
      if (state.view) state.view.tasks = [task, ...state.view.tasks.filter(item => item.run_id !== task.run_id)]
      if (state.scope === scope && (scope === 'story' || state.shotId === shotId)) { state.activeRunId = task.run_id; if (state.prompt === prompt) { state.prompt = ''; saveDraft() } }
      await refreshMedia(); schedule(); return true
    } catch (error) {
      if (ownerEpoch !== epoch) return false
      state.error = error instanceof Error ? error.message : '视频运行未完成'
      const status = error instanceof core.CoreApiError ? error.httpStatus : 0
      if (submitted && status > 0 && status < 500) rememberPending('')
      else if (submitted) schedule()
      return false
    } finally { if (ownerEpoch === epoch) state.submitting = false }
  }
  async function query(task = currentTask.value): Promise<void> {
    if (!task?.can_query) { await load(); return }
    const anchor = story()?.anchor_project_id, ownerEpoch = epoch
    if (!anchor) return
    try { await api.queryComicVideo(state.owner, { conversation_id: state.owner, anchor_project_id: anchor, run_id: task.run_id }); if (ownerEpoch === epoch) await load() }
    catch (error) { if (ownerEpoch === epoch) state.error = error instanceof Error ? error.message : '查询未完成' }
  }
  function dispose(): void { saveDraft(); stopped = true; epoch++; stopTimer(); releaseMedia() }
  return { state, currentTask, busy, submittingCurrent, submittedPrompt, promptLength, canRun, load, select, chooseTask, saveDraft, refreshMedia, run, query, dispose,
    configureCredentials: api.configureVideoCredentials }
}
