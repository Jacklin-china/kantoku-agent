<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Clapperboard, Layers, FileText, Film, MessageSquareText, ArrowLeft, ArrowRight, Check, Download, Video, History, Plus } from 'lucide-vue-next'
import MessageComposer from './chat/MessageComposer.vue'
import UserMessageBubble from './chat/UserMessageBubble.vue'
import AssistantMessageBlock from './chat/AssistantMessageBlock.vue'
import UiDropdown from './ui/UiDropdown.vue'
import WorkspaceShell from './workspace/WorkspaceShell.vue'
import HistoryDrawer from './layout/HistoryDrawer.vue'
import ConversationHistory from './chat/ConversationHistory.vue'
import { conversationTaskTitle, ownsConversationRun } from './chat/conversationPresentation'
import DirectorNodeView from './DirectorNodeView.vue'
import ChatImageAttachment from './chat/ChatImageAttachment.vue'
import ComicShotGeneration from './ComicShotGeneration.vue'
import ComicFlowStage from './comic/ComicFlowStage.vue'
import VideoStage from './comic/VideoStage.vue'
import { createStoryWorkbench } from '../domains/comic/storyWorkbench'
import ImageOptimizeStage from './comic/ImageOptimizeStage.vue'
import WorkSettings from './comic/WorkSettings.vue'
import LegacyWorkImport from './comic/LegacyWorkImport.vue'
import { importComicLegacy } from '../services/core'
import { getComicWorkConversation, sendComicWorkTurn, getComicWorkTurn, deleteComicWork } from '../services/core'
import { copyPromptDelivery, externalShotPrompt, finalImagePrompt, imagePromptReferences, readPromptReference, visualContinuityMessages, promptVersionDiff, reviewImageSources, type PromptReference } from '../domains/comic/shotGeneration'
import { getArtifactContentBlob, exportComicCodexTask } from '../services/core'
import { createComicStoryboard } from '../services/core'
import { getComicConversationWorks, type ComicConversationWork } from '../services/core'
import { createComicWork, createComicImageTask, getComicWork, listComicWorks, renameComicWork, sendComicTaskTurn, getComicTaskTurn, getComicWorkbench, type ComicWork, type ComicImageTask } from '../services/core'
import { comicProductionProgress } from '../domains/comic/productionProgress'
import { buildDirectorChatTurns, conversationWorkspaceRun, directorConversationTask, directorProductionSteps } from '../domains/comic/directorPresentation'
import { directorFeedbackTarget } from '../domains/comic/directorPresentation'
import { confirmComicPrompt, getProjectPrompts, resumeRun, saveComicPrompt, uploadComicExternalImage } from '../services/core'
import { navigate, route } from '../router'
import { canConfirmDirector, canDispatchDirectorInput, directorConversationExecutions, directorConversationSummary, directorDraftFields, discardDirectorNodeDraft, editableDirectorDraft, directorFieldLabels, directorIsStale, directorNodeLabels, directorStageSections, fastDirectorNodeLabels, directorStateLabels, directorSummary, editableDirectorNode, selectDirectorExecution, stageDraftKey } from '../domains/comic/directorPresentation'
import { CoreApiError, cancelRun, compileComicPrompt, confirmDirectorVersion, saveDirectorDraft, createComicProject, createConversation, createDirectorExecution, deleteComicAsset, deleteComicPromptVersion, deleteConversation, deleteDirectorVersion, getArtifactContentUrl, getComicAssets, getComicProject, getComicPromptVersions, getComicShots, getComicStoryboards, getConversation, getConversations, getCurrentDirector, getDirectorExecutions, getDirectorVersions, getEvents, getRun, renameConversation, restoreComicPrompt, restoreDirectorVersion, type ComicAssetView, type ComicProjectContext, type ComicShotView, type ComicStoryboardView, type CreationMode, type DirectorExecution, type RuntimeEvent } from '../services/core'
import type { Conversation, ConversationMessage } from '../types'
import type { CoreRun } from '../types'
import type { CriticFeedback } from '../services/core'

// 工作区保留页面生命周期与请求所有权；聊天和步骤的纯展示规则在
// domains/comic/directorPresentation.ts。切换对话或作品后，异步回调必须检查 epoch，
// 防止旧响应覆盖当前选择；不要把页面上的临时状态当成服务端版本。
const props = defineProps<{ initialRunId?: string; historySignal?: number }>()
defineEmits<{ newProject: [] }>()
const legacyOnly = ref(false)
const project = ref<ComicProjectContext | null>(null)
const imageWorkbenchState = ref<{ projectId: string; conversationId: string; hasImages: boolean; adopted: boolean } | null>(null)
const mode = ref<CreationMode>('professional')
/* 选项文案集中一处；取值仍是 Core 的 fast / professional，业务逻辑未变。 */
const modeOptions: { value: CreationMode; label: string; hint: string }[] = [
  { value: 'fast', label: '普通模式', hint: '快速产出导演方案' },
  { value: 'professional', label: '专业导演模式', hint: '逐节点可编辑与审核' },
]
const executions = ref<DirectorExecution[]>([])
const runs = ref<Record<string, CoreRun>>({})
const assets = ref<ComicAssetView[]>([])
const boards = ref<ComicStoryboardView[]>([])
const shots = ref<ComicShotView[]>([])
const versions = ref<Record<string, unknown>[]>([])
const restoreChoice = ref<number | null>(null)
/* 提示词历史恢复：与导演层同一范式——先二次确认，再落一条新版本。
   历史版本一律保留，当前 Prompt 的确认状态会被清除（因为内容变了，旧确认不再有效）。 */
const promptRestoreChoice = ref<number | null>(null)
const prompts = ref<Record<string, unknown>[]>([])
const selectedBoard = ref('')
const selectedShot = ref('')
const selectedRun = ref('')
const works = ref<ComicConversationWork[]>([]), selectedWorkId = ref('')
const currentWork = computed(() => works.value.find(item => item.work_id === selectedWorkId.value))
const workspaceWorks = ref<ComicWork[]>([]), activeWork = ref<ComicWork | null>(null), activeTask = ref<ComicImageTask | null>(null)
const storyOwner = computed(() => activeWork.value ? `work:${activeWork.value.work_id}` : activeConversationId.value)
const workOptions = computed(() => works.value.map(item => ({ value: item.work_id, label: item.title,
  hint: `${item.has_director ? '导演作品' : '导入图片作品'} · ${item.image_count} 张图片` })))
let workSelectionEpoch = 0
const selectedStage = ref(route.value.directorStage ?? 'director_assemble')
/* 内部 section 沿用旧的 'director'（画面方案/导演节点）与 'storyboard'（生图指令）语义，
   避免大规模重写 1600 行组件的取数逻辑；七步导航 id 只在边界处做一次映射。
   01画面方案/02生图指令 复用现有 director·storyboard 分支；
   03图片优化/04改图指令/05版本比较/06故事编排/07导出材料 均由各自独立分支承载。
   注意：optimize / revision 曾经被临时映射到 'assets'（占位），导致点击这两步会落到
   「作品素材」页；现在它们已有真实分支，必须保持 1:1，不再并入 assets。 */
const sectionAliasesToInternal: Record<string, string> = { plan: 'director', prompt: 'storyboard', optimize: 'images', revision: 'images', compare: 'images' }
const internalToSectionAlias: Record<string, string> = { director: 'plan', storyboard: 'prompt' }
function toInternalSection(page: string): string { return sectionAliasesToInternal[page] ?? page }
function toNavId(internal: string): string { return internalToSectionAlias[internal] ?? internal }
const section = ref(toInternalSection(route.value.workspacePage === 'conversation' ? 'plan' : (route.value.workspacePage ?? 'plan')))
const navOpen = ref(!window.matchMedia('(max-width: 600px)').matches)
/* 桌面端图标条折叠态：与 navOpen（抽屉显隐）独立，跨页共用同一 key 保持状态一致。
   localStorage 在 SSR / 单元测试环境下不存在，必须容错，否则 setup 阶段直接抛错。 */
const navCollapsedKey = 'kantoku-ws-nav-collapsed'
function readNavCollapsed(): boolean {
  try { return localStorage.getItem(navCollapsedKey) === '1' } catch { return false }
}
const navCollapsed = ref(readNavCollapsed())
/* 作品与历史对话抽屉：工作区此前完全没有历史入口——点了「新建作品」，
   上一个作品的画面、方案、Prompt 全都还在，却在界面上找不到任何入口回去。
   抽屉常驻一条入口按钮，列表复用首页那套真实会话/作品数据与重命名、删除能力。 */
const historyDrawer = ref(false)
/* 左栏「历史」入口从这里落到抽屉上：左栏是全局的，抽屉的数据属于本页，
   所以用父级递增信号驱动，而不是把会话数据提到 App 层重复一份。 */
watch(() => props.historySignal, (value, previous) => {
  if (value && value !== previous) historyDrawer.value = true
})
function toggleNavCollapsed(value: boolean): void {
  navCollapsed.value = value
  try { localStorage.setItem(navCollapsedKey, value ? '1' : '0') } catch { /* 忽略写入失败 */ }
}
const chatExpanded = ref(!route.value.workspacePage || route.value.workspacePage === 'conversation')
const nodeCollapsed = ref(false)
const composer = ref<InstanceType<typeof MessageComposer> | null>(null)
const directorNodeView = ref<InstanceType<typeof DirectorNodeView> | null>(null)
const timeline = ref<HTMLElement | null>(null)
const nearBottom = ref(true)
const busy = ref(false)
const submittingDirector = ref(false)
const cancelling = ref(false)
const queuedInputs = ref<{ id: number; text: string; mode: CreationMode }[]>([])
let nextInputId = 0
const loading = ref(true)
const error = ref('')
const refreshConnectionError = ref(''), imageFlowExpanded = ref(false)
const pendingText = ref('')
const pendingRequestId = ref('')
const pendingTaskRequest = ref('')
let recoveringTaskRequest: { key: string; promise: Promise<void> } | null = null
function ownsWorkspaceRun(run: CoreRun, owner: string): boolean {
  return activeWork.value ? activeWork.value.tasks.some(task => !task.history_source_only && task.project_id === run.state.project_id
    && task.conversation_id === run.state.conversation_id) : ownsConversationRun(run, owner)
}
function rememberTaskRequest(taskId: string, requestId: string, expected = ''): void {
  try {
    const key = `kantoku-comic-task-request:${taskId}`
    const saved = sessionStorage.getItem(key)
    if (!requestId && expected && saved && saved !== expected) return
    if (requestId) sessionStorage.setItem(key, requestId)
    else sessionStorage.removeItem(key)
  } catch { /* Server request history remains authoritative. */ }
  if ((activeTask.value?.task_id === taskId || activeWork.value?.work_id === taskId)
    && (!expected || pendingTaskRequest.value === expected)) pendingTaskRequest.value = requestId
}
function restoreTaskRequest(workId: string, taskId = ''): void {
  try { pendingTaskRequest.value = sessionStorage.getItem(`kantoku-comic-task-request:${workId}`)
    ?? (taskId ? sessionStorage.getItem(`kantoku-comic-task-request:${taskId}`) : null) ?? '' }
  catch { /* Keep an in-memory request if browser storage becomes unavailable. */ }
}
async function recoverTaskRequest(): Promise<void> {
  const work = activeWork.value, task = activeTask.value, request = pendingTaskRequest.value
  if (!work || !request || busy.value || disposed) return
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch, owner = activeConversationId.value
  const current = (): boolean => !disposed && epoch === conversationEpoch && workEpoch === workSelectionEpoch
    && activeWork.value?.work_id === work.work_id && pendingTaskRequest.value === request
  const key = `${epoch}:${workEpoch}:${work.work_id}:${request}`
  if (recoveringTaskRequest?.key === key) return recoveringTaskRequest.promise
  const pending = Promise.resolve().then(async () => {
  try {
    let status = await getComicWorkTurn(work.work_id, request)
    if (!current()) return
    if (!status?.phase && task) status = await getComicTaskTurn(work.work_id, task.task_id, request)
    if (!current()) return
    if (!status?.phase || !['completed', 'failed', 'cancelled', 'waiting'].includes(status.phase)) {
      await loadConversationMessages(owner)
      return
    }
    // A new plan may have created its first task while this view was detached.
    const workspace = status.result?.task_id ? await getComicWork(work.work_id) : null
    if (!current()) return
    if (status.result?.task_id && !workspace?.tasks.some(item => item.task_id === status.result!.task_id))
      throw new Error('原请求已有结果，作品暂未读取完整，请重新查询。')
    if (workspace && status.result?.task_id) {
      await openImageTask(work.work_id, String(status.result.task_id), true, String(status.result.run_id ?? ''), workspace)
      if (disposed || conversationEpoch !== epoch + 1 || workSelectionEpoch !== workEpoch + 1
        || activeWork.value?.work_id !== work.work_id || activeTask.value?.task_id !== status.result.task_id
        || !project.value || error.value) return
    } else {
      await refresh()
      if (!current()) return
      await loadConversationMessages(owner)
      if (!current()) return
    }
    rememberTaskRequest(work.work_id, '', request)
    if (task) rememberTaskRequest(task.task_id, '', request)
    pendingText.value = ''
    error.value = status.phase === 'completed' ? '' : status.error
      ? failureText(new CoreApiError(status.error.safe_message, 0, status.error.trace_id ?? null, status.error.error_id ?? null))
      : '本轮未完成，原有方案和输入已保留；没有重新提交模型。'
  } catch (failure) {
    if (!disposed && epoch === conversationEpoch && workEpoch === workSelectionEpoch)
      refreshConnectionError.value = failureText(failure)
  }
  finally { if (recoveringTaskRequest?.promise === pending) recoveringTaskRequest = null }
  })
  recoveringTaskRequest = { key, promise: pending }
  return pending
}
const previousRunIds = ref<string[]>([])
const drafts = ref<Record<string, Record<string, string>>>({})
const revisionVersion = ref<number | null>(null)
const revisionTarget = ref<{ projectId: string; projectVersion: number; version: number; conversationId: string; runId: string } | null>(null)
const restoredSpec = ref<Record<string, unknown> | null>(null)
const compiling = ref(false)
const imageMode = 'external' as const
const manualDirectorApproval = ref(true)
const preparingShot = ref('')
const shotPrompts = ref<Record<string, Record<string, unknown>[]>>({})
const projectPrompts = ref<Record<string, unknown>[]>([])
const promptPreview = ref<Record<string, unknown> | null>(null)
const copiedPrompt = ref('')
const promptCopyFeedback = ref(''), copyingPrompt = ref(''), promptCopyError = ref('')
const compareVersions = ref<number[]>([])
const uploadingShot = ref('')
const newlyImportedImage = ref<{ projectId: string; conversationId: string; image: { image_id: string; version: number } } | null>(null)
const shotErrors = ref<Record<string, string>>({})
const promptReadErrors = ref<Record<string, string>>({})
const promptEdit = ref<{ shotId: string; version: number; director_summary: string; positive_prompt: string; negative_prompt: string } | null>(null)
const productionRun = ref<CoreRun | null>(null)
const productionEvents = ref<RuntimeEvent[]>([])
let productionAdvanced = false
const stageScroll = ref<HTMLElement | null>(null)
const scrollPositions = new Map<string, number>()
const referenceUrls = ref<Record<string, string>>({})
const referenceErrors = ref<Record<string, string>>({})
const referenceLoading = new Set<string>()
watch(promptPreview, prompt => {
  if (prompt) for (const reference of imagePromptReferences(prompt)) void openPromptReference(reference)
})
let timer: ReturnType<typeof setTimeout> | null = null
/* 对话页轮询改为自适应：只有确有任务运行时高频刷新，空闲时降频，页面隐藏时暂停。
   旧实现固定每 2 秒执行一次全量 refresh()（作品、任务、Run、素材等多个请求），
   既拖慢首屏完成，也与用户操作争抢请求与主线程。 */
const POLL_ACTIVE_MS = 2000
const POLL_IDLE_MS = 20000
function pollDelay(): number {
  if (typeof document !== 'undefined' && document.hidden) return POLL_IDLE_MS
  const active = busy.value || submittingDirector.value || !!pendingTaskRequest.value
    || hasRunning.value || ['running', 'pending'].includes(productionRun.value?.status ?? '')
  return active ? POLL_ACTIVE_MS : POLL_IDLE_MS
}
async function pollOnce(): Promise<void> {
  const epoch = conversationEpoch
  try {
    if (activeWork.value && !busy.value) {
      if (!pendingTaskRequest.value) restoreTaskRequest(activeWork.value.work_id, activeTask.value?.task_id)
      if (pendingTaskRequest.value) { await recoverTaskRequest(); return }
    }
    if (!disposed && epoch === conversationEpoch) await refresh()
  } catch (failure) {
    if (!disposed && epoch === conversationEpoch) refreshConnectionError.value = failureText(failure)
  }
}
function scheduleRefresh(): void {
  if (timer) clearTimeout(timer)
  timer = setTimeout(async () => {
    timer = null
    if (disposed) return
    if (!(typeof document !== 'undefined' && document.hidden)) await pollOnce()
    scheduleRefresh()
  }, pollDelay())
}
function pollReentry(): void {
  if (disposed) return
  if (timer) clearTimeout(timer)
  timer = null
  void pollOnce().then(() => scheduleRefresh())
}
let disposed = false
let refreshing: { key: string; promise: Promise<void> } | null = null
let messageRequest: { key: string; promise: Promise<void> } | null = null
let storyRequest: { owner: string; promise: Promise<void> } | null = null
let applyingRoute = false
let pageRequest = 0
let conversationEpoch = 0
/* 制作导航只保留当前制作流程与对话；素材和历史旧链接仍兼容读取。
   对话置顶：它是入口也是随时可回到的落点，不占制作步骤序号，所以侧栏排第一、
   流程条仍按 01→06 的制作顺序排列（点对话时流程条会被会话界面整体替换）。 */
const navigation = [
  { id: 'conversation', label: '对话', icon: MessageSquareText },
  { id: 'plan', label: '画面方案', icon: Clapperboard },
  { id: 'prompt', label: '生图指令', icon: FileText },
  { id: 'images', label: '图片工作台', icon: Layers },
  { id: 'story', label: '故事编排', icon: Film },
  { id: 'export', label: '导出材料', icon: Download },
  { id: 'video', label: '视频生成', icon: Video },
]
/* 对话线程与工作流执行记录是两类数据：左侧只列用户 ↔ AI 的聊天线程。 */
const conversations = ref<Conversation[]>([])
const conversationBusy = ref(false)
const activeConversationId = ref('')
const historyEntries = computed(() => [
  ...workspaceWorks.value.map(work => ({ id: work.work_id, title: work.title, updated_at: work.updated_at ?? '' })),
  ...conversations.value,
].sort((a, b) => String(b.updated_at ?? '').localeCompare(String(a.updated_at ?? ''))))
async function selectHistory(id: string): Promise<void> {
  const work = workspaceWorks.value.find(item => item.work_id === id)
  if (work) await openWorkspace(work)
  else await openConversation(id)
}
const storyWorkbench = createStoryWorkbench(() => activeWork.value
  ? { project_id: activeWork.value.work_id, current_version: activeWork.value.version } : project.value?.project ?? null)
/* Conversation（用户 ↔ AI 聊天）与 Run History（执行记录）是两套数据，这里只装聊天。 */
const conversationMessages = ref<ConversationMessage[]>([])
interface ChatTurn { id: string; role: 'user' | 'assistant'; content: string; artifactId?: string; runId?: string }
async function loadConversationMessages(id: string): Promise<void> {
  if (!id) { conversationMessages.value = []; return }
  const epoch = conversationEpoch, workId = activeWork.value?.work_id
  const key = `${epoch}:${workId ?? id}`
  // 同一选择共享在途读取，避免 API 客户端同 key 的后一次请求取消前一次。
  if (messageRequest?.key === key) return messageRequest.promise
  const pending = Promise.resolve().then(async () => {
    try {
      const loaded = await (workId ? getComicWorkConversation(workId) : getConversation(id))
      if (!loaded || disposed || epoch !== conversationEpoch || activeConversationId.value !== id
          || activeWork.value?.work_id !== workId) return
      conversationMessages.value = (loaded.messages ?? []).filter(item => item.role !== 'system' && item.content.trim())
      // 文本先渲染，图片下载不阻塞首屏。
      const pendingArtifacts = [...new Set(conversationMessages.value
        .map(item => item.artifact_id)
        .filter((artifactId): artifactId is string => !!artifactId && !referenceUrls.value[artifactId]))]
      if (pendingArtifacts.length) void Promise.all(pendingArtifacts.map(item => openReference(item)))
    } catch (failure) {
      if (!disposed && epoch === conversationEpoch && activeConversationId.value === id) refreshConnectionError.value = failureText(failure)
    } finally { if (messageRequest?.promise === pending) messageRequest = null }
  })
  messageRequest = { key, promise: pending }
  return pending
}
const chatTurns = computed<ChatTurn[]>(() => buildDirectorChatTurns(
  conversationMessages.value, runs.value, transcript.value,
  pendingText.value, pendingRequestId.value, previousRunIds.value,
))

async function loadConversations(worksReady: Promise<void> = Promise.resolve()): Promise<void> {
  const [all] = await Promise.all([getConversations('comic'), worksReady])
  const workspaceIds = new Set(workspaceWorks.value.flatMap(work => [work.story_conversation_id, ...work.tasks.map(task => task.conversation_id)]))
  conversations.value = all.filter(item => item.interaction_mode === 'guided' && !workspaceIds.has(item.id))
}
async function loadWorks(): Promise<void> { workspaceWorks.value = await listComicWorks() }
async function createNewWorkspace(): Promise<void> {
  if (conversationBusy.value) return
  conversationBusy.value = true
  try { const work = await createComicWork(); await loadWorks(); await openWorkspace(work) }
  catch (failure) { error.value = failureText(failure) }
  finally { conversationBusy.value = false }
}
async function openWorkspace(work: ComicWork, taskId = '', restore = false): Promise<void> {
  if (storyWorkbench.dirty.value && !await storyWorkbench.save()) { error.value = storyWorkbench.state.error; return }
  if (work.tasks.length) { await openImageTask(work.work_id, taskId || work.tasks[work.tasks.length - 1]!.task_id, restore, '', work); return }
  const page = restore ? route.value.workspacePage ?? 'conversation' : 'conversation'
  conversationEpoch++; resetConversationView(); activeWork.value = work; activeTask.value = null; activeConversationId.value = work.conversation_id
  restoreTaskRequest(work.work_id)
  chatExpanded.value = page === 'conversation'; section.value = toInternalSection(page)
  loading.value = false
  navigate({ name: 'workspace', domain: 'comic', workId: work.work_id, workspacePage: page }, true)
  await loadConversationMessages(work.conversation_id)
  if (['story', 'export'].includes(section.value)) await loadPage()
}
async function openImageTask(workId: string, taskId: string, restore = false, runId = '', preloaded: ComicWork | null = null): Promise<void> {
  if (storyWorkbench.dirty.value && !await storyWorkbench.save()) { error.value = storyWorkbench.state.error; return }
  const epoch = ++conversationEpoch, sameWork = activeWork.value?.work_id === workId
  const savedDrafts = drafts.value, savedQueue = queuedInputs.value
  const wantedRun = runId || (restore ? route.value.directorRunId ?? '' : '')
  resetConversationView(sameWork); loading.value = true
  if (sameWork) { drafts.value = savedDrafts; queuedInputs.value = savedQueue }
  activeWork.value = null; activeTask.value = null; activeConversationId.value = ''
  // 列表里已有作品时直接复用，不再重复请求；已知任务的作品上下文与作品并发拉取。
  const preTask = preloaded?.tasks.find(item => item.task_id === taskId)
  const preContext = preTask && !preTask.history_source_only
    ? getComicProject(preTask.project_id).catch(() => null) : null
  try {
    const work = preloaded ?? await getComicWork(workId)
    if (!work || epoch !== conversationEpoch || disposed) return
    let task = work.tasks.find(item => item.task_id === taskId)
    if (!task) throw new Error('当前画面不属于这个作品，请重新选择')
    if (task.history_source_only) {
      const recovered = work.plan_recoveries?.find(item => item.source_run_id === wantedRun)
        ?? work.plan_recoveries?.slice(-1)[0]
      if (recovered) { task = work.tasks.find(item => item.task_id === recovered.task_id); runId = recovered.run_id }
      if (!task) throw new Error('恢复的方案不可用，请重新选择')
      taskId = task.task_id
    }
    activeWork.value = work; activeTask.value = task; activeConversationId.value = task.conversation_id
    restoreTaskRequest(workId, taskId)
    chatExpanded.value = !restore || route.value.workspacePage === 'conversation'
    if (restore) { section.value = toInternalSection(route.value.workspacePage ?? 'plan'); selectedStage.value = route.value.directorStage ?? 'director_assemble' }
    // 消息不依赖作品上下文、Run、素材或故事；这些慢请求不再挡住对话首屏。
    const messages = loadConversationMessages(task.conversation_id).then(() => {
      if (epoch === conversationEpoch && !disposed) loading.value = false
    })
    const context = (preTask?.project_id === task.project_id && preContext ? await preContext : null) ?? await getComicProject(task.project_id)
    if (epoch !== conversationEpoch || disposed) return
    project.value = context
    await refresh({ context, workspace: work, messagesLoaded: true })
    if (epoch !== conversationEpoch || disposed) return
    const preferredRun = runId || wantedRun
    selectedRun.value = preferredRun && workExecutions.value.some(item => item.run_id === preferredRun) ? preferredRun
      : workExecutions.value.find(item => item.director_spec?.version === project.value?.project.director_version)?.run_id ?? ''
    await syncCurrentDirector(); await messages
    if (epoch !== conversationEpoch || disposed) return
    const workspacePage = chatExpanded.value ? 'conversation' : toNavId(section.value)
    navigate({ ...route.value, name: 'workspace', domain: 'comic', workId, taskId: ['story', 'export'].includes(workspacePage) ? undefined : taskId, conversationId: undefined,
      directorRunId: selectedRun.value || undefined, workspacePage }, true)
    await loadPage()
  } catch (failure) { if (epoch === conversationEpoch) error.value = failureText(failure) }
  finally { if (epoch === conversationEpoch) loading.value = false }
}
async function addImageTask(requirement = ''): Promise<ComicImageTask | null> {
  const workId = activeWork.value?.work_id
  if (!workId) return null
  const work = await getComicWork(workId)
  if (!work || activeWork.value?.work_id !== workId) return null
  const task = await createComicImageTask(work, requirement)
  await loadWorks()
  if (activeWork.value?.work_id !== workId) return null
  await openImageTask(workId, task.task_id, false, '', workspaceWorks.value.find(item => item.work_id === workId) ?? null)
  return task
}
async function renameWorkspace(): Promise<void> {
  const work = activeWork.value; if (!work) return
  const title = window.prompt('作品名称', work.title)?.trim(); if (!title) return
  try { const current = await getComicWork(work.work_id); if (!current) return
    const saved = await renameComicWork(current, title); await loadWorks()
    if (activeWork.value?.work_id === work.work_id) activeWork.value = saved
  } catch (failure) { error.value = failureText(failure) }
}
async function startConversation(): Promise<void> {
  if (conversationBusy.value) return
  conversationBusy.value = true
  try {
    const created = await createConversation('guided', 'comic')
    conversations.value.unshift(created)
    await openConversation(created.id)
    await loadConversations()
  } catch (failure) {
    error.value = failureText(failure)
  } finally {
    conversationBusy.value = false
  }
}

/* openConversation 有两个入口语义，必须区分：
   ① 用户主动点选/新建对话 → 应回到对话页（重置现场）；
   ② 页面加载时按 URL 恢复现场 → **不得**覆盖 URL 里的 workspacePage / directorStage，
      否则刷新会跳回对话页（UX-006）。restore: true 表示后者。
   旧实现无条件写 workspacePage:'conversation' + directorStage:undefined，
   即便 onMounted 之后补写 ref，URL 污染已发生且会被 route watch 反噬。 */
async function openConversation(id: string, initialDirectorRun = '', options: { restore?: boolean } = {}): Promise<void> {
  const bound = workspaceWorks.value.find(work => work.tasks.some(task => task.conversation_id === id))
  if (bound && !route.value.workId?.includes(':brief:')) {
    await openImageTask(bound.work_id, bound.tasks.find(task => task.conversation_id === id)!.task_id,
      options.restore, initialDirectorRun, bound)
    return
  }
  if (storyWorkbench.dirty.value && !await storyWorkbench.save()) { error.value = storyWorkbench.state.error; return }
  loading.value = true
  activeWork.value = null; activeTask.value = null
  const restore = options.restore === true
  const epoch = ++conversationEpoch
  resetConversationView()
  activeConversationId.value = id
  localStorage.setItem('kantoku-comic-active-conversation', id)
  if (restore) {
    // 恢复现场：URL 保持不动，仅把本地状态对齐到 URL（由下方 route watch 统一处理）
    navigate({ ...route.value, workId: route.value.workId?.includes(':brief:') ? route.value.workId : undefined, taskId: undefined, conversationId: id, directorRunId: initialDirectorRun || route.value.directorRunId }, true)
  } else {
    navigate({ ...route.value, workId: undefined, taskId: undefined, conversationId: id, directorRunId: initialDirectorRun || undefined, workspacePage: 'conversation', directorStage: undefined }, true)
  }
  chatExpanded.value = !restore
  loading.value = true
  try {
    const [detail, nextWorks] = await Promise.all([getConversation(id).then(detail => {
      if (detail && epoch === conversationEpoch && !disposed) {
        conversationMessages.value = (detail.messages ?? []).filter(item => item.role !== 'system')
        loading.value = false
      }
      return detail
    }), getComicConversationWorks(id)])
    if (epoch !== conversationEpoch || disposed) return
    if (!detail) throw new Error('无法读取这条对话，请重试')
    conversationMessages.value = (detail.messages ?? []).filter(item => item.role !== 'system')
    works.value = nextWorks
    const wantedWork = nextWorks.find(item => item.director_run_id === initialDirectorRun)
      ?? (restore ? nextWorks.find(item => item.work_id === route.value.workId) : undefined)
      ?? nextWorks.find(item => item.has_director) ?? nextWorks[0]
    selectedWorkId.value = wantedWork?.work_id ?? ''
    const related = await Promise.all((detail.related_run_ids ?? []).map(getRun))
    if (epoch !== conversationEpoch || disposed) return
    const owned = related.filter((run): run is CoreRun => !!run && ownsConversationRun(run, id))
    owned.forEach(run => { runs.value[run.id] = run })
    const latest = conversationWorkspaceRun(owned, id, initialDirectorRun || wantedWork?.director_run_id || undefined)
    productionRun.value = latest?.workflow === 'comic.production.v1' ? latest : owned.find(run => (run.state.quick_creation as Record<string, unknown> | undefined)?.director_run_id === latest?.id) ?? null
    const productionProject = (productionRun.value?.state.quick_creation as Record<string, unknown> | undefined)?.project_id
    if (wantedWork || latest || productionProject) {
      const context = await getComicProject(String(wantedWork?.project_id ?? productionProject ?? latest?.state.project_id))
      if (epoch !== conversationEpoch || disposed) return
      project.value = context
      await refresh({ context, messagesLoaded: true })
      if (epoch !== conversationEpoch || disposed) return
      // 恢复现场时优先沿用已存在的选版，避免把用户正在看的版本顶掉（UX-006）
      const wanted = executions.value.find(item => item.run_id === initialDirectorRun)?.run_id
        ?? (wantedWork ? wantedWork.director_run_id ?? '' : undefined)
        ?? (restore ? executions.value.find(item => item.run_id === selectedRun.value)?.run_id : undefined)
        ?? executions.value[0]?.run_id ?? ''
      selectedRun.value = wanted
      mode.value = active.value?.director_execution_summary.mode ?? 'fast'
      // 同步"作品当前已保存的导演稿"：这是页面刷新后能继续编辑的依据（UX-010）
      await syncCurrentDirector()
    } else {
      currentDirector.value = null
    }
    await nextTick()
    if (epoch === conversationEpoch) composer.value?.fill('')
  } catch (failure) { if (epoch === conversationEpoch) error.value = failureText(failure) }
  finally { if (epoch === conversationEpoch) loading.value = false }
}

async function finishConversationRename(id: string, title: string): Promise<void> {
  const work = workspaceWorks.value.find(item => item.work_id === id)
  if (work) {
    try { const current = await getComicWork(id); if (!current) return
      const saved = await renameComicWork(current, title); await loadWorks()
      if (activeWork.value?.work_id === id) activeWork.value = saved
    } catch (failure) { error.value = failureText(failure) }
    return
  }
  await renameConversation(id, title).catch((failure) => { error.value = failureText(failure) })
  await loadConversations()
}

async function removeConversation(id: string): Promise<void> {
  if (!window.confirm('删除这条对话？关联作品、任务、产物和费用记录会保留。')) return
  try {
    const work = workspaceWorks.value.find(item => item.work_id === id)
    if (work) {
      const current = await getComicWork(id); if (!current) return
      await deleteComicWork(current); await loadWorks()
      if (activeWork.value?.work_id === id) {
        conversationEpoch++; resetConversationView(); activeWork.value = null; activeTask.value = null; activeConversationId.value = ''
        navigate({ name: 'workspace', domain: 'comic', workspacePage: 'conversation' }, true)
      }
      return
    }
    await deleteConversation(id)
    await loadConversations()
    if (activeConversationId.value === id) {
      if (conversations.value[0]) await openConversation(conversations.value[0].id)
      else await startConversation()
    }
  } catch (failure) { error.value = failureText(failure) }
}
const assetFieldLabels: Record<string, string> = {
  appearance: '外观', clothing: '服装', traits: '特征', location: '地点', time: '时间', weather: '天气',
  lighting: '光影', atmosphere: '氛围', environment_features: '环境特点', visual_style: '视觉风格',
  color_palette: '色彩', materials: '材质', camera_language: '摄影语言', art_direction: '艺术方向',
}
/* 当前稿单独从服务端读取；消息入口展示其绑定的不可变版本。
   只有明确选择查看当前版本时才用 currentDirector 更新选版。 */
const currentDirector = ref<Record<string, unknown> | null>(null)
const workExecutions = computed(() => executions.value.filter(item => {
  const record = runs.value[item.run_id]
  return record?.state.project_id === project.value?.project.project_id
    && (!currentWork.value || (currentWork.value.has_director &&
      item.director_spec?.creative_brief_version === currentWork.value.brief_version))
}))
const active = computed(() => selectDirectorExecution(workExecutions.value, selectedRun.value, submittingDirector.value, previousRunIds.value))
/* 当前稿是否比"正在看的执行记录"更新 —— 决定是"查看历史"还是"继续当前"。
   注意：这里必须与 active 的执行记录比版本，不能与 spec 比 ——
   spec 反过来依赖本变量，直接比较会形成循环类型推断（TS7022）。 */
const currentDirectorIsNewer = computed<boolean>(() => {
  const mine = Number((active.value?.director_spec as { version?: number } | undefined)?.version)
  const saved = Number((currentDirector.value as { version?: number } | null)?.version)
  return Number.isFinite(mine) && Number.isFinite(saved) && saved > mine
})
const summary = computed(() => restoredSpec.value || currentDirectorIsNewer.value ? undefined : active.value?.director_execution_summary)
const spec = computed<Record<string, unknown> | null>(() => restoredSpec.value
  ?? active.value?.director_spec ?? currentDirector.value ?? null)
const node = computed(() => summary.value?.stages.find(item => item.stage === selectedStage.value))
const hasRunning = computed(() => ['running', 'pending'].includes(productionRun.value?.status ?? '') || executions.value.some(item => ['running', 'pending'].includes(item.status) && !item.recovery_required))
const productionProgress = computed(() => productionRun.value ? comicProductionProgress(productionRun.value, productionEvents.value, !!productionRun.value.state.image_artifact_id) : null)
const executionLabel = computed(() => hasRunning.value
  ? productionProgress.value?.label ?? summary.value?.status_label ?? '正在执行'
  : busy.value ? '正在处理当前操作' : '')
const draftKey = computed(() => stageDraftKey(project.value?.project.project_id ?? '', spec.value?.schema_version === 2 ? `version:${spec.value.version}` : active.value?.run_id ?? '', spec.value?.schema_version === 2 ? 'director_assemble' : selectedStage.value))
const fields = computed(() => drafts.value[draftKey.value] ?? {})
const editing = computed(() => selectedStage.value !== 'director_assemble' && (spec.value?.schema_version === 2
  ? Object.keys(fields.value).some(key => key.startsWith(`${directorStageSections[selectedStage.value]}.`)) : draftKey.value in drafts.value))
const dirty = computed(() => Object.keys(fields.value).length > 0)
const stale = computed(() => directorIsStale(spec.value, project.value?.creative_brief.version, project.value?.project.director_version, assets.value))
// 底部主操作需要指明“为哪一个镜头生成”，避免用户在长列表里点错目标。
const selectedShotHint = computed(() => {
  const shot = shots.value.find(item => item.shot_id === selectedShot.value)
  return shot ? `为镜头 ${shot.sequence_number} · ${shot.subject} 生成完整 Image Prompt` : ''
})
/* 生成按钮不能因为"还不能生成"就整个消失——用户会以为功能不存在。
   按钮常驻，只在不可用时禁用，并说清卡在哪一步。 */
const productionLockReason = computed(() => {
  if (!spec.value) return '还没有导演方案，先生成并确认方案'
  if (stale.value) return '方案来源已变化，需要先更新导演方案'
  if (dirty.value) return '方案有未保存的修改，保存后重新确认'
  if (spec.value.schema_version !== 2) return '这是旧版方案，需要重新生成 v2 导演方案'
  if (!confirmed.value) return '导演方案还没确认，确认后才能生成完整指令'
  if (!productionEligible.value) return '摄影方案或审核结论尚未通过，暂时不能生成'
  return ''
})
// 历史方案必须能看出“什么时候保存的”，用户才知道它与当前作品差了多少。
const stalePlanSavedAt = computed(() => {
  const wanted = Number((spec.value as { version?: number } | null)?.version)
  const match = versions.value.find(item => Number(item.version) === wanted)
  return typeof match?.created_at === 'string' ? match.created_at : ''
})
const confirmable = computed(() => canConfirmDirector(restoredSpec.value ? 'completed' : active.value?.status, spec.value, stale.value, dirty.value) && !busy.value && !hasRunning.value)
// Durable approval is independent of the transient button's busy/running state.
const confirmed = computed(() => !stale.value && !dirty.value && spec.value?.user_confirmed === true
  && (spec.value.approval as { status?: string } | undefined)?.status === 'approved')
const productionEligible = computed(() => {
  if (!spec.value || spec.value.schema_version !== 2 || stale.value || dirty.value) return false
  if (spec.value.review_required === false) {
    return (spec.value.validation as { can_produce?: boolean } | undefined)?.can_produce === true
  }
  const review = spec.value.critic_result as Record<string, unknown> | undefined
  const camera = spec.value.cinematography as Record<string, unknown> | undefined
  const findings = review?.findings as Record<string, unknown>[] | undefined
  if (camera?.status === 'complete' && spec.value.critic_status === 'unavailable' && spec.value.critic_result === null) return confirmed.value
  return camera?.status === 'complete' && ['pass', 'needs_revision'].includes(String(review?.verdict))
    && typeof review?.reviewed_spec_hash === 'string'
    && !findings?.some(item => item.severity === 'error' || item.code === 'REVIEW_EXECUTION_FAILED')
})
function externalPromptForShot(shot: ComicShotView): Record<string, unknown> | null {
  const board = boards.value.find(item => item.storyboard_id === shot.storyboard_id)
  if (!project.value || !board || board.director_spec_version !== spec.value?.version) return null
  return externalShotPrompt(shotPrompts.value[shot.shot_id] ?? [], shot.version, Number(spec.value?.version), {
    projectId: project.value.project.project_id, shotId: shot.shot_id, storyboardId: board.storyboard_id,
    storyboardVersion: board.version, briefVersion: Number(project.value.creative_brief.version),
  })
}
const comparison = computed(() => {
  const before = prompts.value.find(prompt => Number(prompt.version) === compareVersions.value[0])
  const after = prompts.value.find(prompt => Number(prompt.version) === compareVersions.value[1])
  return before && after ? promptVersionDiff(finalImagePrompt(before), finalImagePrompt(after)) : []
})
function uploadedArtifact(prompt: Record<string, unknown> | null): string {
  return String((prompt?.external_image as Record<string, unknown> | undefined)?.artifact_id ?? '')
}
/* 已导入外部图片的 Prompt 列表：交付页要按"是否真的拿到图"来组织，而不是按资产分组。
   按镜头序号排序，让顺序与分镜页一致。 */
const uploadedPrompts = computed(() => projectPrompts.value
  .filter(item => uploadedArtifact(item))
  .sort((a, b) => Number(a.shot_sequence_number ?? 0) - Number(b.shot_sequence_number ?? 0)))
/* 当前镜头 = 分镜页正在看的那个；切到资产页时它应是第一优先。 */
const featuredPrompt = computed(() => uploadedPrompts.value.find(item => String(item.shot_id ?? '') === selectedShot.value)
  ?? uploadedPrompts.value[0] ?? null)
const pendingPrompts = computed(() => projectPrompts.value.filter(item => !uploadedArtifact(item)))
const reviewImages = computed(() => reviewImageSources(
  projectPrompts.value.filter(item => item.project_id === project.value?.project.project_id),
  [], activeConversationId.value))
const editable = computed(() => spec.value?.schema_version === 2 ? !stale.value && editableDirectorNode(selectedStage.value) : !!node.value && editableDirectorNode(node.value.stage) && summary.value?.mode === 'professional' && !!summary.value?.available_actions.includes('edit_stage'))
const visibleNodes = computed(() => mode.value === 'fast' ? fastDirectorNodeLabels
  : spec.value?.review_required === false ? { ...directorNodeLabels, director_critic: '方案检查' }
  : directorNodeLabels)
const directorPipelineIndex = computed(() => Math.max(0, Object.keys(visibleNodes.value).indexOf(selectedStage.value)))
const directorFindings = computed(() => (spec.value?.critic_feedback as CriticFeedback[] | undefined) ?? summary.value?.critic_feedback ?? [])
/* 真实审核结果里 level 有 blocking / warning 两档：前者是必须改的语义冲突，
   后者只是优化建议。挤在同一张卡里会让人分不清哪些真的挡住确认。 */
const directorBlocking = computed(() => directorFindings.value.filter(item => item.level === 'blocking'))
const directorAdvisory = computed(() => directorFindings.value.filter(item => item.level !== 'blocking'))

/* 方案的来路决定审核该说什么：
   - model    ：模型一次生成，用户没改过 → 按设计默认通过，不该报"暂无意见"
   - manual   ：用户改过并保存 → 已重审，无冲突就是真通过
   - restored ：从历史版本恢复 → 只读，不重新审核
   判据取后端现有字段，不在前端另造状态。 */
const specOrigin = computed<'model' | 'manual' | 'restored'>(() => {
  const declared = (spec.value as { source?: string } | null)?.source
  if (declared === 'manual' || declared === 'restored') return declared
  const provenance = (spec.value?.field_provenance ?? {}) as Record<string, { source_type?: string }>
  return Object.values(provenance).some(item => item?.source_type === 'manual_edit') ? 'manual' : 'model'
})

const specOriginLabel = computed(() => ({
  model: '模型初稿', manual: '你的修改', restored: '历史恢复',
}[specOrigin.value]))

/* 空审核时标题不再重复挂状态小字：那点宽度留给正文，一行才说得完整。 */
const auditStateLabel = computed(() => {
  if (directorBlocking.value.length) return `${directorBlocking.value.length} 条须先改`
  if (directorAdvisory.value.length) return '仅建议'
  return ''
})

/* 空审核时的说明：要说清"为什么没有问题"，而不是留一句像没接上的"暂无"。 */
const auditQuietText = computed(() => {
  if (spec.value?.critic_status === 'unavailable') return '审核暂不可用，方案已保留'
  if (dirty.value) return '改动未保存 · 保存后重新审核'
  if (spec.value && !directorFindings.value.length) {
    if (specOrigin.value === 'manual') return '已按你的改动重审 · 未发现冲突'
    if (specOrigin.value === 'restored') return '历史恢复稿 · 未重新审核'
    return '初稿默认通过 · 改动后会重新审核'
  }
  if (!spec.value) return '尚未生成方案，暂无审核内容'
  return summary.value?.critic_result?.public_summary || '暂无审核意见'
})
const directorConstraints = computed(() => project.value?.creative_brief?.hard_constraints ?? [])
const projectTitle = computed(() => activeWork.value?.title ?? conversations.value.find(item => item.id === activeConversationId.value)?.title ?? '新作品')
const title = computed(() => section.value === 'director' ? visibleNodes.value[selectedStage.value] : navigation.find(item => item.id === toNavId(section.value))?.label)
const activeNavigation = computed(() => chatExpanded.value ? 'conversation' : toNavId(section.value))

/* 制作流程的「流程视角」：每步的完成状态由现有真实数据推导，不虚构进度。
   - done    ：该阶段已有产出（导演方案 / Prompt / 已上传图片 / 已选图 / 故事）
   - current ：用户此刻正在这一步
   - todo    ：尚未开始
   状态判据只读现有 computed，不新增任何后端字段。
   视频生成暂不做 done 判定：此处没有读取可用于确认完成的产物状态。 */
const productionSteps = computed(() => {
  const planIncomplete = spec.value?.generation_incomplete === true
  const hasPlan = !planIncomplete && (typeof spec.value?.version === 'number'
    || workExecutions.value.some(item => typeof item.director_spec?.version === 'number'
      && item.director_spec?.generation_incomplete !== true))
  const currentShot = shots.value.find(shot => shot.shot_id === selectedShot.value)
  const hasPrompt = !!currentShot && !!externalPromptForShot(currentShot)
  const workbench = imageWorkbenchState.value?.projectId === project.value?.project.project_id
    && imageWorkbenchState.value?.conversationId === activeConversationId.value ? imageWorkbenchState.value : null
  const hasImage = uploadedPrompts.value.length > 0 || (workbench ? workbench.hasImages : !!activeTask.value?.image_count)
  const hasStory = !!storyWorkbench.state.view?.story?.shots.length
  const storyConfirmed = storyWorkbench.confirmed.value
  const hasStoryExport = !!storyWorkbench.state.view?.exports.some(item => item.available !== false && item.story_version === storyWorkbench.state.view?.story?.version)
  const hasSelected = workbench ? workbench.adopted : !!activeTask.value?.adopted_image_count
  return directorProductionSteps({ planIncomplete, hasPlan, hasPrompt, hasImage, hasSelected,
    hasStory, storyConfirmed, hasStoryExport, activeNavigation: activeNavigation.value })
})
const activeRun = computed(() => !restoredSpec.value && active.value ? runs.value[active.value.run_id] : null)
const runningExecution = computed(() => executions.value.find(item => ['running', 'pending'].includes(item.status) && !item.recovery_required))
const canDispatch = computed(() => canDispatchDirectorInput({ busy: busy.value, running: hasRunning.value, loading: loading.value, error: !!error.value, cancelling: cancelling.value }))
const transcript = computed(() => directorConversationExecutions(executions.value, runs.value).map(item => {
  const run = runs.value[item.run_id]
  const text = run ? directorConversationTask(run) : ''
  // 聊天里只保留用户自己的创作表达；重跑/节点名属于执行记录，不进对话。
  return { execution: item, text,
    answer: run?.workflow === 'comic.shot.continue' && item.status !== 'completed'
      ? '这一轮未生成独立导演方案，旧版续写流程在审核时停止。原方案未被覆盖，请重新发送这条创作需求。'
      : run?.state.revision_instruction && item.director_spec
      ? `已根据修改要求保存导演方案 v${item.director_spec.version}，请进入方案查看并审核。`
      : directorConversationSummary(item.director_spec), label: item.director_execution_summary.status_label }
}))
function nodeState(stage: string): string {
  if (stale.value) return '已过期'
  if (restoredSpec.value) return '保存的方案'
  const actual = summary.value?.stages.find(item => item.stage === stage)
  if (spec.value?.generation_incomplete === true && spec.value.review_required === false
      && actual?.status !== 'completed') return stage === 'director_critic' ? '内部检查未完成' : '待内部检查'
  return actual ? directorStateLabels[actual.status] ?? actual.status : spec.value ? '方案内容' : '未开始'
}
function nodeStatus(stage: string): string {
  return !restoredSpec.value && !stale.value ? summary.value?.stages.find(item => item.stage === stage)?.status ?? '' : ''
}
/* 关卡流水线的三态：展开 / 已完成 / 只读。状态用轨道方块与贴纸表达，不再堆文字。 */
function isNodeOpen(stage: string): boolean {
  return selectedStage.value === stage && !nodeCollapsed.value
}
function nodeClass(stage: string): string {
  if (isNodeOpen(stage)) return 'is-open'
  if (nodeStatus(stage) === 'completed') return 'is-done'
  if (spec.value && !nodeEditable(stage)) return 'is-lock'
  return ''
}
function nodeSticker(stage: string): string {
  if (nodeStatus(stage) === 'completed') return 'is-grass'
  if (selectedStage.value === stage) return 'is-gold'
  return ''
}
function nodeEditable(stage: string): boolean {
  if (!editableDirectorNode(stage)) return false
  return spec.value?.schema_version === 2 ? !stale.value
    : !!summary.value?.stages.some(item => item.stage === stage) && summary.value.mode === 'professional' && summary.value.available_actions.includes('edit_stage')
}
function nodePreview(stage: string): string {
  if (!spec.value) return ''
  if (stage === 'director_critic') return summary.value?.critic_result?.public_summary ?? ''
  if (stage === 'director_assemble') return typeof spec.value.version === 'number' ? `方案 v${spec.value.version} · ${confirmed.value ? '已确认' : '草稿'}` : ''
  const prefix = `${directorStageSections[stage]}.`
  return Object.entries(directorDraftFields(spec.value)).find(([key, value]) => key.startsWith(prefix) && value.trim())?.[1] ?? ''
}
async function openDraft(runId?: string): Promise<void> {
  if (runId) await selectPlan(runId)
  visitStage('director_assemble')
}
async function selectPlan(runId: string): Promise<void> {
  const record = runs.value[runId]
  if (!record || !ownsWorkspaceRun(record, activeConversationId.value)) return
  if (activeWork.value) {
    const task = activeWork.value.tasks.find(item => item.project_id === record.state.project_id)
    if (!task) return
    await openImageTask(activeWork.value.work_id, task.task_id, false, runId)
    return
  }
  const work = works.value.find(item => item.director_run_id === runId)
    ?? works.value.find(item => item.project_id === runs.value[runId]?.state.project_id
      && item.brief_version === executions.value.find(item => item.run_id === runId)?.director_spec?.creative_brief_version)
  if (work) { await selectWork(work.work_id, runId); return }
  const epoch = conversationEpoch
  const targetId = String(runs.value[runId]?.state.project_id ?? '')
  if (!targetId || !ownsConversationRun(runs.value[runId]!, activeConversationId.value)) return
  if (targetId !== project.value?.project.project_id) {
    const context = await getComicProject(targetId)
    if (!context || epoch !== conversationEpoch || disposed) return
    project.value = context
    boards.value = []; shots.value = []; prompts.value = []; versions.value = []
    selectedBoard.value = ''; selectedShot.value = ''; promptPreview.value = null
    assets.value = await getComicAssets(targetId)
  }
  if (epoch !== conversationEpoch || disposed) return
  selectedRun.value = runId; restoredSpec.value = null; invalidate()
  await syncCurrentDirector()
  await loadPage()
}
async function selectWork(workId: string, runId = ''): Promise<void> {
  const target = works.value.find(item => item.work_id === workId)
  if (!target) return
  const sequence = ++workSelectionEpoch, epoch = conversationEpoch
  pageRequest++
  try {
    const context = await getComicProject(target.project_id)
    if (!context || sequence !== workSelectionEpoch || epoch !== conversationEpoch || disposed) return
    project.value = context; selectedWorkId.value = target.work_id
    selectedRun.value = runId || target.director_run_id || ''
    restoredSpec.value = null; currentDirector.value = null; invalidate()
    boards.value = []; shots.value = []; prompts.value = []; versions.value = []; assets.value = []
    projectPrompts.value = []; shotPrompts.value = {}; imageWorkbenchState.value = null
    compiling.value = false; preparingShot.value = ''; uploadingShot.value = ''; shotErrors.value = {}; promptReadErrors.value = {}
    selectedBoard.value = ''; selectedShot.value = ''; promptPreview.value = null; promptEdit.value = null
    productionRun.value = null; productionEvents.value = []; productionAdvanced = false
    await syncCurrentDirector()
    if (sequence !== workSelectionEpoch || epoch !== conversationEpoch || disposed) return
    assets.value = await getComicAssets(target.project_id)
    if (sequence !== workSelectionEpoch || epoch !== conversationEpoch || disposed) return
    await loadPage()
  } catch (failure) {
    if (sequence === workSelectionEpoch && epoch === conversationEpoch) error.value = failureText(failure)
  }
}
function failureText(failure: unknown): string {
  if (failure instanceof CoreApiError) return `${failure.message}${failure.traceId ? ` · Trace：${failure.traceId}` : ''}`
  return failure instanceof Error ? failure.message : '操作未完成，请查看真实任务状态'
}
async function confirm(): Promise<void> {
  if (!confirmable.value || !project.value || !spec.value) return
  busy.value = true
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch
  let accepted = false
  try {
    const saved = await confirmDirectorVersion(project.value.project.project_id, Number(spec.value.version), project.value.project.current_version)
    if (epoch !== conversationEpoch || workEpoch !== workSelectionEpoch) return
    restoredSpec.value = saved; await refresh()
    accepted = true
  }
  catch (failure) { if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) error.value = failureText(failure) }
  finally { if (epoch === conversationEpoch) busy.value = false }
  if (accepted && epoch === conversationEpoch && workEpoch === workSelectionEpoch) await enterStoryboard()
}
function invalidate(): void { revisionVersion.value = null; revisionTarget.value = null }
function beginEdit(): void {
  if (!editable.value || busy.value || hasRunning.value) return
  invalidate()
  if (spec.value?.schema_version === 2) {
    const prefix = `${directorStageSections[selectedStage.value]}.`
    const selected = Object.fromEntries(Object.entries(directorDraftFields(spec.value)).filter(([key]) => key.startsWith(prefix)))
    drafts.value[draftKey.value] = { ...selected, ...fields.value }
    return
  }
  if (!node.value) return
  if (!editing.value) drafts.value[draftKey.value] = Object.fromEntries(Object.entries(Object.values(node.value.output)[0] ?? {})
    .filter(([key, value]) => key !== 'hard_constraints' && key in directorFieldLabels && (value == null || typeof value === 'string' || Array.isArray(value)))
    .map(([key, value]) => [key, Array.isArray(value) ? value.join('\n') : String(value ?? '')]))
  invalidate()
}
function editFinal(stage = 'creative_understanding'): void { visitStage(stage); beginEdit() }
async function locateSuggestion(path: string): Promise<void> {
  if (busy.value || hasRunning.value || stale.value) return
  const stage = directorFeedbackTarget(spec.value, path)
  if (!stage) return
  visitStage(stage); beginEdit()
  const owner = draftKey.value
  await nextTick()
  if (editing.value && owner === draftKey.value) await directorNodeView.value?.focusField(path)
}
function reviseByInstruction(): void {
  if (!project.value || !spec.value?.version || !activeConversationId.value || dirty.value || stale.value || busy.value || hasRunning.value) return
  revisionVersion.value = Number(spec.value.version)
  revisionTarget.value = {
    projectId: project.value.project.project_id, projectVersion: project.value.project.current_version,
    version: revisionVersion.value, conversationId: activeConversationId.value, runId: selectedRun.value,
  }
  chatExpanded.value = true
}
async function saveFinal(instruction?: string): Promise<void> {
  if (!project.value || !spec.value || busy.value || hasRunning.value || stale.value) { if (instruction) composer.value?.fill(instruction); return }
  const key = draftKey.value
  const epoch = conversationEpoch
  const owner = activeConversationId.value
  const projectId = project.value.project.project_id
  const target = instruction ? revisionTarget.value : null
  if (instruction && (!target || target.projectId !== projectId || target.conversationId !== owner || target.runId !== selectedRun.value || target.version !== spec.value.version || dirty.value)) {
    error.value = '修改目标已变化，请重新选择要修改的方案'
    composer.value?.fill(instruction); invalidate(); return
  }
  const runId = selectedRun.value
  const payload = {
    expected_project_version: target?.projectVersion ?? project.value.project.current_version,
    expected_director_version: target?.version ?? spec.value.version,
    conversation_id: owner,
    ...(instruction ? { revision_instruction: instruction } : { draft: editableDirectorDraft(spec.value, fields.value) }),
  }
  busy.value = true; error.value = ''
  try {
    const saved = await saveDirectorDraft(projectId, payload)
    if (epoch !== conversationEpoch || disposed) return
    delete drafts.value[key]
    if (project.value?.project.project_id !== projectId || selectedRun.value !== runId) return
    restoredSpec.value = saved; invalidate()
    await refresh()
    if (epoch !== conversationEpoch || disposed || project.value?.project.project_id !== projectId || selectedRun.value !== runId) return
    const savedRun = executions.value.find(item => item.director_spec?.project_id === projectId && item.director_spec.version === saved.version)
    if (savedRun) selectedRun.value = savedRun.run_id
  } catch (failure) {
    if (epoch === conversationEpoch && !disposed && project.value?.project.project_id === projectId && selectedRun.value === runId) {
      error.value = failureText(failure)
      if (instruction) composer.value?.fill(instruction)
    }
  }
  finally { if (epoch === conversationEpoch) busy.value = false }
}
async function reviewFinal(): Promise<void> {
  if (!spec.value || dirty.value) return
  if (spec.value.review_required === false && spec.value.version == null && active.value) {
    await resume()
    return
  }
  // 草稿还没有版本号；审核必须绑定已保存的导演版本，
  // 否则请求缺少绑定版本会被后端拒绝。先落一版再审核。
  if (spec.value.version == null) {
    await saveFinal()
    if (spec.value?.version == null) return
  }
  await execute('', { review_current: true, expected_director_version: spec.value.version })
}
function updateField(key: string, value: string): void { if (editing.value) drafts.value[draftKey.value]![key] = value }
function discardNode(): void {
  if (spec.value?.schema_version !== 2) { delete drafts.value[draftKey.value]; return }
  const remaining = discardDirectorNodeDraft(fields.value, selectedStage.value)
  if (Object.keys(remaining).length) drafts.value[draftKey.value] = remaining
  else delete drafts.value[draftKey.value]
}
async function openReference(id: string): Promise<void> {
  if (referenceUrls.value[id] || referenceLoading.has(id)) return
  referenceLoading.add(id)
  try {
    const url = await getArtifactContentUrl(id)
    if (!url) throw new Error('参考图暂时无法载入')
    if (disposed) URL.revokeObjectURL(url)
    else referenceUrls.value[id] = url
  } catch (failure) { referenceErrors.value[id] = failureText(failure) }
  finally { referenceLoading.delete(id) }
}
function promptReferenceKey(reference: PromptReference): string { return `${reference.artifact_id}:${reference.sha256}` }
async function openPromptReference(reference: PromptReference): Promise<void> {
  const key = promptReferenceKey(reference)
  if (referenceUrls.value[key] || referenceLoading.has(key)) return
  referenceLoading.add(key); delete referenceErrors.value[key]
  try {
    const content = await readPromptReference(reference, id => getArtifactContentBlob(id, true))
    if (!disposed) referenceUrls.value[key] = URL.createObjectURL(content)
  } catch (failure) { if (!disposed) referenceErrors.value[key] = failureText(failure) }
  finally { referenceLoading.delete(key) }
}
async function refresh(initial: { context?: ComicProjectContext | null; workspace?: ComicWork; messagesLoaded?: boolean } = {}): Promise<void> {
  if (!project.value) {
    const work = activeWork.value, epoch = conversationEpoch
    if (!work || disposed || busy.value) return
    const workspace = await getComicWork(work.work_id)
    if (!workspace || disposed || epoch !== conversationEpoch) return
    if (workspace.tasks.length) await openWorkspace(workspace, route.value.taskId, true)
    else { activeWork.value = workspace; await loadConversationMessages(workspace.conversation_id) }
    return
  }
  const id = project.value.project.project_id
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch
  const owner = activeConversationId.value
  const key = `${epoch}:${workEpoch}:${id}`
  if (refreshing?.key === key) return refreshing.promise
  const pending = Promise.resolve().then(async () => {
  try {
    if (disposed || epoch !== conversationEpoch || workEpoch !== workSelectionEpoch || id !== project.value?.project.project_id) return
    refreshConnectionError.value = ''
    const messages = initial.messagesLoaded ? Promise.resolve() : loadConversationMessages(owner)
    const projectIds = [...new Set([id, ...(activeWork.value?.tasks.filter(task => !task.history_source_only).map(task => task.project_id) ?? []), ...Object.values(runs.value)
      .filter(run => ownsWorkspaceRun(run, owner) && run.state.project_id)
      .map(run => String(run.state.project_id))])]
    const workId = activeWork.value?.work_id
    const [context, taskGroups, nextAssets, nextWorks, workspace] = await Promise.all([
      initial.context?.project.project_id === id ? Promise.resolve(initial.context) : getComicProject(id),
      Promise.all(projectIds.map(getDirectorExecutions)), getComicAssets(id),
      workId ? Promise.resolve([]) : getComicConversationWorks(owner),
      initial.workspace?.work_id === workId ? Promise.resolve(initial.workspace) : workId ? getComicWork(workId) : Promise.resolve(null),
    ])
    if (disposed || epoch !== conversationEpoch || workEpoch !== workSelectionEpoch || id !== project.value?.project.project_id) return
    // 被并发取消的请求返回 null；此时必须保留既有产出，
    // 否则新一轮生成后列表会被清空，用户看不到之前的结果。
    if (taskGroups.some(tasks => !tasks)) return
    const tasks = taskGroups.flatMap(tasks => tasks ?? [])
    // A refresh started before a write must not roll back its committed revision.
    if (context && (context.project.current_version ?? 0) >= (project.value?.project.current_version ?? 0)) project.value = context
    if (workspace && activeWork.value?.work_id === workspace.work_id) {
      activeWork.value = workspace
      activeTask.value = workspace.tasks.find(task => task.project_id === id) ?? null
      workspaceWorks.value = workspaceWorks.value.map(work => work.work_id === workspace.work_id ? workspace : work)
    }
    works.value = activeWork.value ? [] : nextWorks
    const selectedWork = nextWorks.find(item => item.director_run_id === selectedRun.value)
    if (selectedWork) selectedWorkId.value = selectedWork.work_id
    else if (!nextWorks.some(item => item.work_id === selectedWorkId.value))
      selectedWorkId.value = nextWorks.find(item => item.project_id === id)?.work_id ?? ''
    // 终态 Run 的输出不可变；任务摘要仍每次读取，状态发生变化时再更新详情。
    const records = await Promise.all(tasks.filter(item => {
      const known = runs.value[item.run_id]
      return !known || known.status !== item.status || !['completed', 'failed', 'cancelled'].includes(item.status)
    }).map(item => getRun(item.run_id)))
    if (disposed || epoch !== conversationEpoch || workEpoch !== workSelectionEpoch || id !== project.value?.project.project_id) return
    records.forEach(run => { if (run) runs.value[run.id] = run })
    const merged = new Map(executions.value.map(item => [item.run_id, item]))
    tasks.forEach(item => {
      const known = merged.get(item.run_id)
      if (known && ['completed', 'failed', 'cancelled'].includes(known.status)
          && ['running', 'pending'].includes(item.status)) return
      merged.set(item.run_id, item)
    })
    executions.value = [...merged.values()].filter(item => runs.value[item.run_id] && ownsWorkspaceRun(runs.value[item.run_id]!, owner))
    assets.value = nextAssets
    await messages
    if (productionRun.value) {
      const previousStatus = productionRun.value.status
      const finished = ['completed', 'failed', 'cancelled'].includes(previousStatus)
      const fresh = records.find(record => record?.id === productionRun.value?.id)
      const [run, updates] = await Promise.all([
        fresh ? Promise.resolve(fresh) : finished ? Promise.resolve(productionRun.value) : getRun(productionRun.value.id),
        finished ? Promise.resolve(productionEvents.value) : getEvents(productionRun.value.id),
      ])
      if (disposed || epoch !== conversationEpoch || workEpoch !== workSelectionEpoch) return
      productionRun.value = run; productionEvents.value = updates
      if (run) runs.value[run.id] = run
      if (run && !productionAdvanced && section.value === 'director' && (['storyboard', 'prompt', 'prepare', 'generate', 'qc', 'archive'].includes(run.current_node) || (run.status === 'completed' && !!(run.state.quick_creation as Record<string, unknown>)?.storyboard_id))) {
        productionAdvanced = true
        section.value = 'storyboard'
        chatExpanded.value = false
        await loadPage()
      }
      if (run?.status === 'completed' && previousStatus !== 'completed') { await loadConversationMessages(owner); await loadPage() }
      if (run?.status === 'failed') {
        // Report the run's real failure node and reason; never mask a director-stage
        // failure as an image-production failure.
        const reason = String(run.error ?? run.state.error_message ?? '任务执行失败')
        const errorId = String(run.state.error_id ?? '')
        error.value = errorId ? `${reason} · ${errorId}` : reason
      }
      if (run?.status === 'waiting') error.value = run.error ?? '图片任务等待查询或对账；可在当前工作区恢复，不会重新提交。'
    }
  } finally { if (refreshing?.promise === pending) refreshing = null }
  })
  refreshing = { key, promise: pending }
  return pending
}
async function execute(text: string, options: Record<string, unknown> = {}, selectedMode: CreationMode = mode.value): Promise<void> {
  if (activeWork.value && !Object.keys(options).length && text.trim()) { await executeTaskTurn(text, selectedMode); return }
  if (busy.value || hasRunning.value || (!project.value && !text.trim())) { composer.value?.fill(text); return }
  const editingKey = draftKey.value
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch
  const owner = activeConversationId.value
  const sourceProject = project.value
  if (!owner) { error.value = '请先打开或创建对话'; return }
  const requestId = !Object.keys(options).length ? crypto.randomUUID() : ''
  pendingRequestId.value = requestId
  submittingDirector.value = true; busy.value = true; pendingText.value = text; previousRunIds.value = executions.value.map(item => item.run_id); error.value = ''; invalidate()
  restoredSpec.value = null
  try {
    // Navigation detaches the view, not the already submitted request's ownership.
    const target = sourceProject
      ? await getComicProject(sourceProject.project.project_id) ?? sourceProject
      : await createComicProject(text)
    if (epoch === conversationEpoch && workEpoch === workSelectionEpoch && !disposed) project.value = target
    const conversation = owner
    const result = await createDirectorExecution(target.project.project_id, {
      expected_project_version: target.project.current_version, creation_mode: selectedMode,
      creative_operation: 'auto',
      independent_plan: !!text.trim() && !Object.keys(options).length,
      ...(requestId ? { request_id: requestId } : {}),
      conversation_id: conversation,
      ...('previous_run_id' in options || 'resume_run_id' in options ? {} : { task: text || null }), ...options,
    })
    if (disposed || epoch !== conversationEpoch || workEpoch !== workSelectionEpoch || owner !== activeConversationId.value) return
    if ('conversation_response' in result) {
      pendingText.value = ''
      await loadConversationMessages(owner)
      await loadConversations()
      return
    }
    executions.value = [result, ...executions.value.filter(item => item.run_id !== result.run_id)]
    const record = await getRun(result.run_id)
    if (disposed || epoch !== conversationEpoch || workEpoch !== workSelectionEpoch || owner !== activeConversationId.value) return
    if (record) runs.value[record.id] = record
    const resultProjectId = String(record?.state.project_id ?? result.director_spec?.project_id ?? '')
    if (resultProjectId && resultProjectId !== project.value?.project.project_id) {
      const context = await getComicProject(resultProjectId)
      if (disposed || epoch !== conversationEpoch || workEpoch !== workSelectionEpoch) return
      if (context) project.value = context
    }
    selectedRun.value = result.run_id; restoredSpec.value = null
    if ('stage_edits' in options) delete drafts.value[editingKey]
    await refresh()
    if (epoch !== conversationEpoch || workEpoch !== workSelectionEpoch || disposed) return
    if (result.shot_continuation) {
      selectedBoard.value = result.shot_continuation.storyboard.storyboard_id
      selectedShot.value = result.shot_continuation.shot.shot_id
      section.value = 'storyboard'
      await loadPage()
      await loadConversationMessages(owner)
    }
    if (epoch !== conversationEpoch || workEpoch !== workSelectionEpoch || disposed) return
    pendingText.value = ''
    await loadConversations()
  } catch (failure) {
    if (epoch !== conversationEpoch || workEpoch !== workSelectionEpoch || disposed) return
    error.value = failureText(failure)
    await refresh().catch(() => undefined)
    if (epoch !== conversationEpoch || workEpoch !== workSelectionEpoch || disposed) return
    const failedRun = selectDirectorExecution(executions.value, '', true, previousRunIds.value)
    if (failedRun) { selectedRun.value = failedRun.run_id; pendingText.value = '' }
  } finally { if (epoch === conversationEpoch) { if (workEpoch !== workSelectionEpoch) pendingText.value = ''; submittingDirector.value = false; busy.value = false } }
}
async function executeTaskTurn(text: string, selectedMode: CreationMode): Promise<void> {
  if (busy.value || conversationBusy.value || hasRunning.value) return
  if (pendingTaskRequest.value) { error.value = '请先查询上一轮请求的结果，避免重复提交。'; composer.value?.fill(text); return }
  const work = activeWork.value, task = activeTask.value
  if (!work) return
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch
  busy.value = true; submittingDirector.value = true; pendingText.value = text; error.value = ''
  const requestId = crypto.randomUUID()
  pendingRequestId.value = requestId
  previousRunIds.value = executions.value.map(item => item.run_id)
  try {
    const [current, context, view] = await Promise.all([getComicWork(work.work_id),
      task ? getComicProject(task.project_id) : Promise.resolve(null),
      task ? getComicWorkbench(task.project_id, task.conversation_id) : Promise.resolve(null)])
    if (!current) throw new Error('作品读取失败，请重新读取后发送')
    rememberTaskRequest(work.work_id, requestId)
    const result = await sendComicWorkTurn(work.work_id, {
      request_id: requestId, conversation_id: current.conversation_id,
      expected_project_version: current.version, independent_plan: true,
      task: text, creation_mode: selectedMode,
      target_task_id: task?.task_id ?? null,
      expected_target_project_version: context?.project.current_version ?? null,
      expected_director_version: context?.project.director_version ?? null,
      turn_image: view?.state?.image ?? null,
    })
    rememberTaskRequest(work.work_id, '', requestId)
    if (disposed || epoch !== conversationEpoch || workEpoch !== workSelectionEpoch) return
    await loadWorks()
    if (disposed || epoch !== conversationEpoch || workEpoch !== workSelectionEpoch) return
    if ('run_id' in result && result.task_id) {
      await openImageTask(work.work_id, result.task_id, false, result.run_id,
        workspaceWorks.value.find(item => item.work_id === work.work_id) ?? null)
    } else {
      await loadConversationMessages(activeConversationId.value)
      if (result.image_action) openSection('images')
    }
    if (activeWork.value?.work_id === work.work_id) pendingText.value = ''
  } catch (failure) {
    if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) error.value = failureText(failure)
  } finally { if (epoch === conversationEpoch) { busy.value = false; submittingDirector.value = false } }
}
function sendInput(text: string): void {
  if (!text.trim()) return
  chatExpanded.value = true
  void titleConversation(text)
  if (revisionVersion.value !== null) {
    if (busy.value || hasRunning.value || cancelling.value) { composer.value?.fill(text); return }
    void saveFinal(text); return
  }
  if (busy.value || hasRunning.value || cancelling.value || queuedInputs.value.length) {
    queuedInputs.value.push({ id: ++nextInputId, text, mode: mode.value })
    void dispatchQueued()
    return
  }
  void execute(text)
}
/* Title belongs to the sending conversation, never to Project or the selected Run. */
async function titleConversation(text: string): Promise<void> {
  const id = activeConversationId.value
  const current = conversations.value.find(item => item.id === id)
  if (!id || !current || !/^新对话$/.test(current.title)) return
  const title = conversationTaskTitle(text)
  if (!title) return
  current.title = title
  await renameConversation(id, title).catch((failure) => { if (activeConversationId.value === id) error.value = failureText(failure) })
  await loadConversations()
}
async function dispatchQueued(explicit = false): Promise<void> {
  if (explicit) error.value = ''
  if (!canDispatch.value || disposed) return
  const next = queuedInputs.value.shift()
  if (next) await execute(next.text, {}, next.mode)
}
async function cancelExecution(): Promise<void> {
  const task = productionRun.value && ['running', 'pending'].includes(productionRun.value.status)
    ? { run_id: productionRun.value.id } : runningExecution.value
  if (!task || cancelling.value) return
  const epoch = conversationEpoch
  cancelling.value = true
  try { await cancelRun(task.run_id); if (epoch === conversationEpoch) await refresh() }
  catch (failure) { if (epoch === conversationEpoch) error.value = failureText(failure) }
  finally { if (epoch === conversationEpoch) cancelling.value = false }
}
async function rerun(save = false): Promise<void> {
  if (!active.value || !node.value || !summary.value) return
  mode.value = summary.value.mode
  const options: Record<string, unknown> = { previous_run_id: active.value.run_id, rerun_from: node.value.stage }
  if (save) {
    const patch = { ...Object.values(node.value.output)[0] }
    Object.entries(fields.value).forEach(([key, text]) => { patch[key] = Array.isArray(patch[key]) ? text.split('\n').map(item => item.trim()).filter(Boolean) : patch[key] == null && !text.trim() ? null : text })
    options.stage_edits = { [node.value.stage]: patch }
  }
  await execute('', options)
}
async function resume(): Promise<void> {
  if (!active.value || !summary.value) return
  mode.value = summary.value.mode; await execute('', { resume_run_id: active.value.run_id })
}
function regenerate(): void {
  const task = String(activeRun.value?.state.task ?? project.value?.creative_brief.original_request ?? '')
  if (task) void execute(task)
}
async function loadPage(): Promise<void> {
  const requestId = ++pageRequest
  const epoch = conversationEpoch
  const id = project.value?.project.project_id
  const owner = activeConversationId.value
  if (['story', 'export', 'video'].includes(section.value)) {
    const owner = storyOwner.value
    if (!owner) return
    if (storyRequest?.owner === owner) return storyRequest.promise
    const pending = storyWorkbench.load(owner)
    storyRequest = { owner, promise: pending }
    try { await pending } finally { if (storyRequest?.promise === pending) storyRequest = null }
    return
  }
  if (!id) return
  error.value = ''
  try {
    if (section.value === 'storyboard') {
      if (currentWork.value && !currentWork.value.has_director) { boards.value = []; shots.value = []; return }
      const next = await getComicStoryboards(id)
      if (requestId !== pageRequest || !next) return
      boards.value = next
      if (!next.some(item => item.storyboard_id === selectedBoard.value)) selectedBoard.value = next[0]?.storyboard_id ?? ''
      await loadShots()
    }
    if (['prompt', 'assets', 'images'].includes(section.value)) {
      const next = await getProjectPrompts(id)
      if (requestId !== pageRequest || epoch !== conversationEpoch || id !== project.value?.project.project_id) return
      if (!next) return
      projectPrompts.value = next.filter(item => !currentWork.value || item.creative_brief_version === currentWork.value.brief_version)
      if (section.value !== 'images') await Promise.all(next.filter(prompt => uploadedArtifact(prompt)).map(prompt => openReference(uploadedArtifact(prompt))))
      if (requestId !== pageRequest) return
      if (section.value === 'prompt') {
        if (!next.some(prompt => prompt.shot_id === selectedShot.value)) selectedShot.value = String(next[0]?.shot_id ?? '')
        await loadPrompts()
      }
    }
    if (section.value === 'history') {
      const next = await getDirectorVersions(id)
      if (requestId === pageRequest) versions.value = next
    }
  } catch (failure) { if (requestId === pageRequest) error.value = failureText(failure) }
}
async function loadShots(): Promise<void> {
  const id = selectedBoard.value
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch, projectId = project.value?.project.project_id
  const current = () => epoch === conversationEpoch && workEpoch === workSelectionEpoch
    && projectId === project.value?.project.project_id && id === selectedBoard.value && !disposed
  if (!id) { shots.value = []; return }
  try {
    const next = await getComicShots(id)
    if (!current() || !next) return
    shots.value = next
    if (!next.some(item => item.shot_id === selectedShot.value)) selectedShot.value = next[0]?.shot_id ?? ''
    const results = await Promise.allSettled(next.map(shot => getComicPromptVersions(shot.shot_id)))
    if (!current()) return
    const histories: [string, Record<string, unknown>[]][] = []
    results.forEach((result, index) => {
      const shotId = next[index]!.shot_id
      if (result.status === 'fulfilled' && result.value !== null) {
        shotPrompts.value[shotId] = result.value; histories.push([shotId, result.value])
        promptReadErrors.value[shotId] = ''
      } else if (result.status === 'rejected') promptReadErrors.value[shotId] = failureText(result.reason)
    })
    await Promise.all(histories.flatMap(([, history]) => history.filter(prompt => uploadedArtifact(prompt)).map(prompt => openReference(uploadedArtifact(prompt)))))
  } catch (failure) { if (current()) error.value = failureText(failure) }
}
async function loadPrompts(): Promise<void> {
  const id = selectedShot.value
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch, projectId = project.value?.project.project_id
  const current = () => epoch === conversationEpoch && workEpoch === workSelectionEpoch
    && projectId === project.value?.project.project_id && id === selectedShot.value && !disposed
  if (!id) { prompts.value = []; return }
  prompts.value = shotPrompts.value[id] ?? prompts.value.filter(prompt => prompt.shot_id === id)
  try {
    const next = await getComicPromptVersions(id)
    if (current() && next !== null) {
      prompts.value = next; shotPrompts.value[id] = next
      promptReadErrors.value[id] = ''
      compareVersions.value = [Number(next[1]?.version ?? next[0]?.version), Number(next[0]?.version)]
    }
  } catch (failure) { if (current()) promptReadErrors.value[id] = failureText(failure) }
}
async function compilePrompt(): Promise<void> {
  const shot = shots.value.find(item => item.shot_id === selectedShot.value)
  const board = boards.value.find(item => item.storyboard_id === selectedBoard.value)
  if (!project.value || !shot || (manualDirectorApproval.value && !confirmed.value) || board?.director_spec_version !== spec.value?.version || compiling.value || busy.value || hasRunning.value) return
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch
  if (!activeConversationId.value) return
  compiling.value = true; preparingShot.value = shot.shot_id; error.value = ''
  shotErrors.value[shot.shot_id] = ''
  try {
    const prepared = await compileComicPrompt(shot.shot_id, project.value.project.current_version, shot.version, activeConversationId.value, true)
    if (epoch !== conversationEpoch || workEpoch !== workSelectionEpoch) return
    shotPrompts.value[shot.shot_id] = [prepared, ...(shotPrompts.value[shot.shot_id] ?? []).filter(prompt => prompt.artifact_id !== prepared.artifact_id)]
    if (selectedShot.value === shot.shot_id) promptPreview.value = prepared
    await Promise.all([
      refresh().catch(failure => {
        if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) refreshConnectionError.value = failureText(failure)
      }),
      selectedShot.value === shot.shot_id ? loadPrompts() : Promise.resolve(),
    ])
  } catch (failure) { if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) shotErrors.value[shot.shot_id] = failureText(failure) }
  finally { if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) { compiling.value = false; preparingShot.value = '' } }
}
async function viewImagePrompt(shot: ComicShotView): Promise<void> {
  selectedShot.value = shot.shot_id
  promptPreview.value = externalPromptForShot(shot)
  await loadPrompts()
}
async function copyImagePrompt(prompt: Record<string, unknown>, mode: 'combined' | 'text' | 'image' = 'combined', reference?: PromptReference): Promise<void> {
  if (copyingPrompt.value) return
  if (imagePromptReferences(prompt).length) promptPreview.value = prompt
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch, id = String(prompt.artifact_id)
  const current = () => epoch === conversationEpoch && workEpoch === workSelectionEpoch && !disposed
  if (error.value === promptCopyError.value) error.value = ''
  promptCopyError.value = ''
  copyingPrompt.value = id; copiedPrompt.value = ''; promptCopyFeedback.value = ''
  try {
    const result = await copyPromptDelivery(prompt, artifactId => getArtifactContentBlob(artifactId, true), mode, reference)
    if (!current()) return
    copiedPrompt.value = id
    promptCopyFeedback.value = result === 'combined' ? '图文已复制。粘贴后请检查图片附件和正文；若只出现图片，点击“仅复制文字”补齐。'
      : result === 'image' ? '参考图已复制，可直接粘贴到外部对话。提示词可用“仅复制文字”补齐。'
        : imagePromptReferences(prompt).length ? '文字已复制；请同时附上此处列出的参考图。' : '完整生图 Prompt 已复制，含禁止内容。'
  } catch (failure) { if (current()) error.value = promptCopyError.value = `无法复制 Prompt：${failureText(failure)}` }
  finally { if (copyingPrompt.value === id) copyingPrompt.value = '' }
}
async function copyCodexTask(prompt: Record<string, unknown>): Promise<void> {
  if (copyingPrompt.value || !project.value) return
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch, id = String(prompt.artifact_id)
  const current = () => epoch === conversationEpoch && workEpoch === workSelectionEpoch && !disposed
  const owner = project.value.project.project_id
  if (owner !== prompt.project_id) return
  if (error.value === promptCopyError.value) error.value = ''
  promptCopyError.value = ''; copiedPrompt.value = ''; promptCopyFeedback.value = ''
  copyingPrompt.value = id
  try {
    const task = await exportComicCodexTask(String(prompt.shot_id), {
      request_id: `codex-task-${id}-${project.value.project.current_version}`,
      conversation_id: activeConversationId.value,
      expected_project_version: project.value.project.current_version,
      expected_prompt_version: Number(prompt.version), expected_shot_version: Number(prompt.shot_version),
    })
    if (!current() || project.value?.project.project_id !== owner) return
    // Keep the saved instruction available if the browser rejects a clipboard write.
    promptCopyFeedback.value = task.handoff_text
    copiedPrompt.value = id
    await navigator.clipboard.writeText(task.handoff_text)
    if (current()) promptCopyFeedback.value = `工作任务已复制，已保存 ${task.reference_count} 张参考图。${task.materials_directory ? `首次请在 Codex 本机工作模式中关联资料夹：${task.materials_directory}。` : ""}将短指令粘贴到能读取该资料夹的工作对话；生成后回到图片工作台上传结果。实际出图尚待验收。`
  } catch (failure) { if (current()) error.value = promptCopyError.value = `无法复制 Codex 工作任务：${failureText(failure)}` }
  finally { if (copyingPrompt.value === id) copyingPrompt.value = '' }
}
async function downloadPromptReference(prompt: Record<string, unknown>, reference: PromptReference): Promise<void> {
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch
  const current = () => epoch === conversationEpoch && workEpoch === workSelectionEpoch && !disposed
  try {
    if (!imagePromptReferences(prompt).some(item => item.artifact_id === reference.artifact_id && item.sha256 === reference.sha256)) throw new Error('参考图不属于这份指令')
    const content = await readPromptReference(reference, id => getArtifactContentBlob(id, true))
    if (!current()) return
    const url = URL.createObjectURL(content), anchor = document.createElement('a')
    anchor.href = url; anchor.download = reference.filename; anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (failure) { if (current()) error.value = `无法下载参考图：${failureText(failure)}` }
}
function exportImagePrompt(prompt: Record<string, unknown>): void {
  const url = URL.createObjectURL(new Blob([finalImagePrompt(prompt)], { type: 'text/plain;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = `${String(prompt.shot_id)}-prompt-v${Number(prompt.version)}.txt`
  anchor.click(); URL.revokeObjectURL(url)
}
/* 04–07 阶段共用：复制失败时把正文交给调用方展示，不谎报成功。 */
async function copyText(text: string): Promise<void> {
  if (!text.trim()) return
  await navigator.clipboard.writeText(text).catch((failure) => {
    error.value = `无法复制文字：${failureText(failure)}`
  })
}
async function confirmImagePrompt(prompt: Record<string, unknown>): Promise<void> {
  if (!project.value || busy.value || hasRunning.value) return
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch
  busy.value = true
  try {
    const saved = await confirmComicPrompt(String(prompt.shot_id), project.value.project.current_version, Number(prompt.version), activeConversationId.value)
    if (epoch !== conversationEpoch || workEpoch !== workSelectionEpoch) return
    promptPreview.value = saved; await loadPrompts()
  } catch (failure) { if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) error.value = failureText(failure) }
  finally { if (epoch === conversationEpoch) busy.value = false }
}
async function importExternalImage(shot: ComicShotView, file: File): Promise<void> {
  const prompt = externalPromptForShot(shot)
  if (!prompt || !project.value || uploadingShot.value || hasRunning.value) return
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch
  const projectId = project.value.project.project_id
  uploadingShot.value = shot.shot_id; shotErrors.value[shot.shot_id] = ''
  try {
    if (file.size > 5 * 1024 * 1024) throw new Error('请选择不超过 5 MB 的 PNG、JPEG 或 WebP 图片')
    const uploaded = await uploadComicExternalImage(shot.shot_id, file, {
      expected_project_version: project.value.project.current_version, expected_prompt_version: Number(prompt.version),
      expected_shot_version: shot.version, conversation_id: activeConversationId.value,
    })
    if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) {
      await refresh(); await loadPage()
      const next = await getProjectPrompts(projectId)
      if (next && epoch === conversationEpoch && workEpoch === workSelectionEpoch && project.value?.project.project_id === projectId) {
        projectPrompts.value = next
        if (uploaded.created === true && typeof uploaded.artifact_id === 'string') {
          newlyImportedImage.value = { projectId, conversationId: activeConversationId.value,
            image: { image_id: uploaded.artifact_id, version: 1 } }
          openSection('images')
        }
      }
    }
  } catch (failure) { if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) shotErrors.value[shot.shot_id] = failureText(failure) }
  finally { if (epoch === conversationEpoch) uploadingShot.value = '' }
}
async function recoverImage(runId = productionRun.value?.id): Promise<void> {
  if (!runId || busy.value || hasRunning.value) return
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch
  busy.value = true; error.value = ''
  try { const run = await resumeRun(runId); if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) { productionRun.value = run; await refresh() } }
  catch (failure) { if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) error.value = failureText(failure) }
  finally { if (epoch === conversationEpoch) busy.value = false }
}
function editImagePrompt(prompt?: Record<string, unknown>): void {
  if (prompt) { selectedShot.value = String(prompt.shot_id); promptPreview.value = prompt }
  const current = prompt ?? prompts.value[0]
  if (!selectedShot.value || !current) return
  promptEdit.value = { shotId: selectedShot.value, version: Number(current.version), director_summary: String(current.director_summary), positive_prompt: String(current.positive_prompt), negative_prompt: String(current.negative_prompt ?? '') }
}
async function saveImagePrompt(): Promise<void> {
  if (!promptEdit.value || !project.value || busy.value || hasRunning.value) return
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch
  busy.value = true; error.value = ''
  const { shotId, version, ...draft } = promptEdit.value
  try {
    const saved = await saveComicPrompt(shotId, { expected_project_version: project.value.project.current_version, expected_version: version, draft })
    if (epoch !== conversationEpoch || workEpoch !== workSelectionEpoch) return
    promptEdit.value = null; promptPreview.value = saved; await refresh(); await loadPrompts()
    if (section.value === 'prompt') {
      const next = await getProjectPrompts(project.value.project.project_id)
      if (next && epoch === conversationEpoch && workEpoch === workSelectionEpoch) projectPrompts.value = next
    }
  } catch (failure) { if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) error.value = failureText(failure) }
  finally { if (epoch === conversationEpoch) busy.value = false }
}
function changeImageModel(): void {
  error.value = '模型从 config/settings.yaml 的 image.model / image.protocol 读取。修改兼容模型并重启后端后再生成；原任务结果未知时需先查询或对账，不能通过换模型重提。'
}
async function enterStoryboard(): Promise<void> {
  if (!project.value || !spec.value || !confirmed.value || !productionEligible.value || busy.value || hasRunning.value) return
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch
  const projectId = project.value.project.project_id, directorVersion = spec.value.version
  busy.value = true; error.value = ''
  openSection('storyboard')
  try {
    await loadPage()
    if (epoch !== conversationEpoch || workEpoch !== workSelectionEpoch || projectId !== project.value?.project.project_id || directorVersion !== spec.value?.version) return
    if (!boards.value.some(board => board.director_spec_version === spec.value?.version)) {
      const planned = await createComicStoryboard(projectId, project.value.project.current_version,
        project.value.creative_brief.original_request,
        Object.keys((spec.value.asset_versions ?? {}) as Record<string, number>).map(key => key.replace(/^asset:/, '')))
      if (epoch !== conversationEpoch || workEpoch !== workSelectionEpoch || projectId !== project.value?.project.project_id || directorVersion !== spec.value?.version) return
      // Use the server's returned revision, never guess current_version + 1.
      if (planned.storyboard.project_version !== undefined) project.value = { ...project.value,
        project: { ...project.value.project, current_version: Math.max(
          project.value.project.current_version, planned.storyboard.project_version) } }
      boards.value = [planned.storyboard, ...boards.value.filter(board => board.storyboard_id !== planned.storyboard.storyboard_id)]
      shots.value = planned.shots
      selectedBoard.value = planned.storyboard.storyboard_id
      selectedShot.value = planned.shots[0]?.shot_id ?? ''
      await refresh(); await loadPage()
    }
  } catch (failure) { if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) error.value = failureText(failure) }
  finally { if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) busy.value = false }
}
async function restore(version: number): Promise<void> {
  if (!project.value || busy.value || hasRunning.value) return
  busy.value = true; invalidate()
  const epoch = conversationEpoch, workEpoch = workSelectionEpoch
  try {
    const restored = await restoreDirectorVersion(project.value.project.project_id, version, project.value.project.current_version)
    if (epoch !== conversationEpoch || workEpoch !== workSelectionEpoch) return
    restoredSpec.value = restored
    await refresh()
    if (epoch !== conversationEpoch || workEpoch !== workSelectionEpoch) return
    // 恢复后立刻同步权威当前稿：这样即使之后刷新，也能读到这份新版本（UX-010）
    await syncCurrentDirector()
    if (epoch !== conversationEpoch || workEpoch !== workSelectionEpoch) return
    await loadPage()
    if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) { section.value = 'director'; selectedStage.value = 'director_assemble' }
  } catch (failure) { if (epoch === conversationEpoch && workEpoch === workSelectionEpoch) error.value = failureText(failure) }
  finally { if (epoch === conversationEpoch) { busy.value = false; restoreChoice.value = null } }
}
/* UX-010：从"历史版本"切回"作品当前稿"。
   只需清掉临时回显并重新同步权威稿 —— 不产生任何新版本、不改动任何数据。 */
async function viewCurrentDirector(): Promise<void> {
  await syncCurrentDirector()
  restoredSpec.value = currentDirector.value
}
/* 读取作品当前已保存的导演稿（后端权威值）。
   失败时静默退回 null —— 旧 Run 的 spec 仍可展示，不阻塞页面。 */
async function syncCurrentDirector(): Promise<void> {
  const projectId = project.value?.project.project_id
  const workId = selectedWorkId.value, epoch = conversationEpoch
  if (!projectId || !project.value?.project.director_version || (currentWork.value && !currentWork.value.has_director)) { currentDirector.value = null; return }
  try {
    const saved = await getCurrentDirector(projectId)
    if (epoch === conversationEpoch && projectId === project.value?.project.project_id && workId === selectedWorkId.value)
      currentDirector.value = saved
  } catch { if (epoch === conversationEpoch && workId === selectedWorkId.value) currentDirector.value = null }
}
/* 把历史 Prompt 版本恢复为当前新版本。
   后端在同一事务内校验：expected_version（乐观锁）、shot 版本、以及 creative_brief /
   director_spec / 角色资产 / 场景资产 / 风格 五个来源版本。任一漂移都会明确拒绝，
   因此这里不做前端猜测，只把真实原因原样透出（UX-007）。 */
async function restorePrompt(prompt: Record<string, unknown>): Promise<void> {
  if (!project.value || busy.value || hasRunning.value) return
  const shotId = String(prompt.shot_id ?? '')
  const version = Number(prompt.version)
  const current = prompts.value[0]
  if (!shotId || !Number.isFinite(version) || !current) return
  busy.value = true; invalidate()
  const epoch = conversationEpoch
  try {
    const restored = await restoreComicPrompt(shotId, version, Number(current.version), project.value.project.current_version)
    if (epoch !== conversationEpoch) return
    promptPreview.value = restored
    await refresh()
    if (epoch !== conversationEpoch) return
    await loadPrompts()
  } catch (failure) { if (epoch === conversationEpoch) error.value = failureText(failure) }
  finally { if (epoch === conversationEpoch) { busy.value = false; promptRestoreChoice.value = null } }
}
/* 与后端 restore 的校验条件保持一致（web_studio.py:3810-3816），
   把"为什么不能恢复"前置到按钮上，避免用户点了才被拒。
   返回空串 = 可恢复。 */
function stalePromptSource(prompt: Record<string, unknown>): string {
  const current = prompts.value[0]
  if (!current) return '当前没有可比对的 Prompt 版本。'
  const shot = shots.value.find(item => item.shot_id === String(prompt.shot_id ?? ''))
  const reasons: string[] = []
  if (shot && Number(prompt.shot_version) !== shot.version) reasons.push(`镜头已推进到 v${shot.version}`)
  if (Number(prompt.creative_brief_version) !== Number(current.creative_brief_version)) reasons.push('创意简报已更新')
  if (Number(prompt.director_spec_version) !== Number(current.director_spec_version)) reasons.push('导演方案已更新')
  const sameAssets = (a: unknown, b: unknown): boolean => JSON.stringify(a ?? {}) === JSON.stringify(b ?? {})
  if (!sameAssets(prompt.character_asset_versions, current.character_asset_versions)) reasons.push('角色资产已更新')
  if (!sameAssets(prompt.scene_asset_versions, current.scene_asset_versions)) reasons.push('场景资产已更新')
  if (Number(prompt.style_version) !== Number(current.style_version)) reasons.push('风格资产已更新')
  return reasons.length ? `${reasons.join('、')}，历史内容已过期，无法直接恢复。请重新编译生成新版本。` : ''
}
function newProject(): void {
  if (busy.value || hasRunning.value || ((Object.keys(drafts.value).length || queuedInputs.value.length) && !window.confirm('放弃未保存的修改和待发送补充并新建作品？'))) return
  void createNewWorkspace()
}
function resetConversationView(preserveStory = false): void {
  workSelectionEpoch++; works.value = []; selectedWorkId.value = ''; imageWorkbenchState.value = null
  if (!preserveStory) storyWorkbench.reset()
  submittingDirector.value = false
  productionRun.value = null; productionEvents.value = []; productionAdvanced = false
  mode.value = 'professional'; manualDirectorApproval.value = true; promptEdit.value = null
  preparingShot.value = ''; shotPrompts.value = {}
  projectPrompts.value = []; promptPreview.value = null; copiedPrompt.value = ''
  compareVersions.value = []; uploadingShot.value = ''; shotErrors.value = {}; promptReadErrors.value = {}
  refreshing = null
  refreshConnectionError.value = ''
  busy.value = false; cancelling.value = false; compiling.value = false; legacyOnly.value = false
  queuedInputs.value = []; previousRunIds.value = []; restoreChoice.value = null
  pendingRequestId.value = ''
  pendingTaskRequest.value = ''
  conversationMessages.value = []; composer.value?.fill(''); scrollPositions.clear()
  pageRequest++; project.value = null; executions.value = []; runs.value = {}; assets.value = []; boards.value = []; shots.value = []; versions.value = []; prompts.value = []
  selectedRun.value = ''; restoredSpec.value = null; currentDirector.value = null; drafts.value = {}; invalidate(); error.value = ''; pendingText.value = ''
  section.value = 'director'; selectedStage.value = 'director_assemble'; chatExpanded.value = true; restoreChoice.value = null
}
function visitStage(stage: string): void {
  // 再点当前已展开的节点 → 折叠面板内容，不切对话
  if (selectedStage.value === stage && section.value === 'director' && !chatExpanded.value && stage !== 'director_assemble') {
    nodeCollapsed.value = !nodeCollapsed.value
    return
  }
  // 后续任务没有产出时，导演页仍可打开本会话已保存的方案；失败记录留在历史中。
  if (!spec.value && !busy.value && !hasRunning.value) {
    const saved = workExecutions.value.find(item => typeof item.director_spec?.version === 'number'
      && item.director_spec.version === project.value?.project.director_version)
      ?? workExecutions.value.find(item => typeof item.director_spec?.version === 'number')
    if (saved) selectedRun.value = saved.run_id
  }
  nodeCollapsed.value = false
  section.value = 'director'; selectedStage.value = stage; chatExpanded.value = false
}
function openSection(id: string): void {
  if (id === 'conversation') {
    chatExpanded.value = true
    nodeCollapsed.value = false
    return
  }
  // 01 画面方案 = 导演节点视图
  if (id === 'plan') {
    // 已在该页时再点：保持整页内容可见，只重新解析当前方案；
    // 单个节点的收起/展开由「导演流程」节点按钮负责，避免整页变空白。
    if (section.value === 'director' && !chatExpanded.value) {
      nodeCollapsed.value = false
      visitStage(selectedStage.value)
      nodeCollapsed.value = false
      return
    }
    nodeCollapsed.value = false
    visitStage(selectedStage.value)
    return
  }
  // 其他页：已在该页时再点不做切换，避免整页内容被收起而留下空白
  if (!chatExpanded.value && section.value === toInternalSection(id)) {
    nodeCollapsed.value = false
    return
  }
  nodeCollapsed.value = false
  section.value = toInternalSection(id)
  chatExpanded.value = false
}
async function repairStoryImage(projectId: string): Promise<void> {
  if (activeWork.value && projectId) {
    const task = activeWork.value.tasks.find(item => item.task_id === projectId)
    if (!task) { error.value = '图片来源不属于当前作品，请在故事中替换图片'; return }
    if (activeTask.value?.task_id !== task.task_id) await openImageTask(activeWork.value.work_id, task.task_id)
    openSection('images'); return
  }
  if (projectId && projectId !== project.value?.project.project_id) {
    const target = executions.value.find(item => runs.value[item.run_id]?.state.project_id === projectId)
    if (target) await selectPlan(target.run_id)
    else {
      const context = await getComicProject(projectId)
      if (context) { project.value = context; await refresh() }
    }
  }
  openSection('images')
}

function scrollState(): void { const el = timeline.value; if (el) nearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80 }

/* ---------------- 删除与批量删除 ----------------
   项目资产 / Prompt 版本 / 导演方案版本三处共用同一套交互，范式沿用在线的全局资产库页：
   勾选集合 → 二次确认弹窗 → 逐项执行 → 逐项报告失败（不整体回滚）。
   两条产品约束：
     ① 当前正在使用的版本不允许删除——入口直接禁用，不做"隐式回退到上一版"；
     ② 删除只作用于历史版本，作品级乐观并发用返回的 project_version 链式续接。 */
interface PendingDeletionItem { id: string; version: number; label: string }
interface PendingDeletion {
  kind: 'assets' | 'prompt-versions' | 'director-versions'
  title: string
  hint: string
  items: PendingDeletionItem[]
}
const pendingDeletion = ref<PendingDeletion | null>(null)
const deletionDialog = ref<HTMLDialogElement | null>(null)
const deleting = ref(false)
const deletionFeedback = ref('')
const deletionFailures = ref<{ label: string; reason: string }[]>([])
const selectedAssetIds = ref<string[]>([])
const selectedPromptVersions = ref<number[]>([])
const selectedDirectorVersions = ref<number[]>([])

/* 启用中的资产：与模板里"已删除不进列表"的判定保持一致。 */
const liveAssets = computed(() => assets.value.filter(item => item.state !== 'deleted'))
const selectedAssets = computed(() => liveAssets.value.filter(item => selectedAssetIds.value.includes(item.asset_id)))
/* 当前版本判定：Prompt 列表由接口按 version DESC 返回，首项即当前；
   导演方案以作品指针为准——两者都不依赖前端推测。 */
const currentPromptVersion = computed(() => Number(prompts.value[0]?.version ?? 0))
const currentDirectorVersion = computed(() => Number(project.value?.project.director_version ?? 0))
const deletablePromptVersions = computed(() => prompts.value.filter(item => Number(item.version) !== currentPromptVersion.value))

function toggleIn(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter(item => item !== value) : [...list, value]
}
function toggleAssetSelection(assetId: string): void { selectedAssetIds.value = toggleIn(selectedAssetIds.value, assetId) }
function togglePromptVersion(version: number): void {
  selectedPromptVersions.value = selectedPromptVersions.value.includes(version)
    ? selectedPromptVersions.value.filter(item => item !== version)
    : [...selectedPromptVersions.value, version]
}
function toggleDirectorVersion(version: number): void {
  selectedDirectorVersions.value = selectedDirectorVersions.value.includes(version)
    ? selectedDirectorVersions.value.filter(item => item !== version)
    : [...selectedDirectorVersions.value, version]
}

function openDeletion(request: PendingDeletion): void {
  if (!request.items.length || deleting.value) return
  pendingDeletion.value = request
  deletionFeedback.value = ''
  deletionFailures.value = []
  void nextTick(() => { try { deletionDialog.value?.showModal() } catch { /* 非浏览器环境无 dialog */ } })
}
function closeDeletion(): void { try { deletionDialog.value?.close() } catch { /* 忽略 */ } }
function askAssetDeletion(items: ComicAssetView[]): void {
  openDeletion({
    kind: 'assets',
    title: `删除 ${items.length} 个项目资产？`,
    hint: '删除后资产变为「已删除」状态，不再参与后续分镜与 Prompt 编译；作品与历史版本仍然保留。',
    items: items.map(item => ({ id: item.asset_id, version: item.version, label: `${item.name}（v${item.version}）` })),
  })
}
function askPromptDeletion(items: Record<string, unknown>[]): void {
  openDeletion({
    kind: 'prompt-versions',
    title: `删除 ${items.length} 个 Prompt 版本？`,
    hint: `当前版本 v${currentPromptVersion.value} 会保留；已导入的外部图片不受影响。`,
    items: items.map(item => ({
      id: String(item.shot_id ?? selectedShot.value),
      version: Number(item.version),
      label: `镜头 ${item.shot_sequence_number ?? '-'} · Prompt v${item.version}`,
    })),
  })
}
function askDirectorDeletion(versions: number[]): void {
  openDeletion({
    kind: 'director-versions',
    title: `删除 ${versions.length} 个导演方案版本？`,
    hint: `当前使用的 v${currentDirectorVersion.value} 会保留；其余版本删除后无法从历史找回。`,
    items: versions.map(version => ({ id: String(version), version, label: `导演方案 v${version}` })),
  })
}

async function runDeletion(): Promise<void> {
  const request = pendingDeletion.value
  const projectId = project.value?.project.project_id
  if (!request || !projectId || deleting.value) return
  closeDeletion()
  deleting.value = true
  deletionFeedback.value = ''
  deletionFailures.value = []
  const failures: { label: string; reason: string }[] = []
  let succeeded = 0
  let version = project.value!.project.current_version
  try {
    for (const item of request.items) {
      try {
        if (request.kind === 'assets') {
          const updated = await deleteComicAsset(projectId, item.id, item.version, version)
          version = Number(updated.project_version ?? version + 1)
          selectedAssetIds.value = selectedAssetIds.value.filter(id => id !== item.id)
        } else if (request.kind === 'prompt-versions') {
          const result = await deleteComicPromptVersion(item.id, item.version, version)
          version = Number(result.project_version ?? version + 1)
          selectedPromptVersions.value = selectedPromptVersions.value.filter(value => value !== item.version)
        } else {
          const result = await deleteDirectorVersion(projectId, item.version, version)
          version = Number(result.project_version ?? version + 1)
          selectedDirectorVersions.value = selectedDirectorVersions.value.filter(value => value !== item.version)
        }
        succeeded++
      } catch (failure) {
        failures.push({ label: item.label, reason: failureText(failure) })
        // 作品版本冲突会让后续每一项都失败；先重新同步一次再继续，避免"一错全错"。
        const fresh = await getComicProject(projectId).catch(() => null)
        if (fresh) version = fresh.project.current_version
      }
    }
    deletionFailures.value = failures
    deletionFeedback.value = succeeded
      ? `已删除 ${succeeded} 项${failures.length ? `，${failures.length} 项未完成。` : '。'}`
      : ''
  } finally {
    deleting.value = false
    pendingDeletion.value = null
    await refresh()
    await loadPage()
  }
}
async function selectPromptShot(shotId: string): Promise<void> {
  if (shotId === selectedShot.value) return
  selectedShot.value = shotId
  selectedPromptVersions.value = []
  promptRestoreChoice.value = null
  promptPreview.value = null
  await loadPrompts()
}

function previewReference(media: { url: string }): void { window.open(media.url, '_blank', 'noopener') }
watch(section, () => { void loadPage() })
watch(() => [route.value.workspacePage, route.value.directorStage, route.value.directorRunId, route.value.workId, route.value.taskId], async ([page, stage, runId, workId, taskId]) => {
  applyingRoute = true
  const scoped = workspaceWorks.value.find(work => work.work_id === workId)
  if (!loading.value && workId && !workId.includes(':brief:') && (activeWork.value?.work_id !== workId || (taskId && activeTask.value?.task_id !== taskId))) {
    try {
      const work = scoped ?? await getComicWork(workId)
      if (!work) throw new Error('无法读取指定作品，请从作品列表明确选择。')
      await openWorkspace(work, taskId, true)
    } catch (failure) {
      conversationEpoch++; resetConversationView(); activeWork.value = null; activeTask.value = null; activeConversationId.value = ''
      error.value = failureText(failure)
    }
  }
  if (workId && workId !== selectedWorkId.value && works.value.some(item => item.work_id === workId)) await selectWork(workId)
  if (runId && runId !== selectedRun.value && executions.value.some(item => item.run_id === runId)) {
    await selectPlan(runId)
  }
  chatExpanded.value = !page || page === 'conversation'
  if (page && page !== 'conversation') {
    section.value = toInternalSection(page)
    if (page === 'plan') selectedStage.value = stage ?? 'director_assemble'
  }
  if (!loading.value && mode.value === 'fast' && selectedStage.value === 'director_critic') {
    navigate({ ...route.value, directorStage: 'director_assemble' }, true)
  }
  await nextTick()
  applyingRoute = false
}, { flush: 'sync' })
watch([section, selectedStage, chatExpanded, selectedRun, selectedWorkId], () => {
  if (applyingRoute || loading.value || !['workspace', 'workspace_run'].includes(route.value.name)) return
  const workspacePage = chatExpanded.value ? 'conversation' : toNavId(section.value)
  const directorStage = workspacePage === 'plan' ? selectedStage.value : undefined
  const workStory = !!activeWork.value && ['story', 'export'].includes(workspacePage)
  const directorRunId = workStory ? undefined : selectedRun.value || undefined
  const workId = activeWork.value?.work_id || selectedWorkId.value || undefined
  const taskId = workStory ? undefined : activeTask.value?.task_id
  if (route.value.workspacePage !== workspacePage || route.value.directorStage !== directorStage || route.value.directorRunId !== directorRunId || route.value.workId !== workId) {
    navigate({ ...route.value, workspacePage, directorStage, directorRunId, workId, taskId,
      conversationId: activeWork.value ? undefined : activeConversationId.value || undefined })
  }
})
watch(mode, value => {
  if (value === 'fast' && selectedStage.value === 'director_critic') selectedStage.value = 'director_assemble'
  if (project.value) sessionStorage.setItem(`kantoku-comic-view-mode:${project.value.project.project_id}`, value)
}, { flush: 'sync' })
watch([canDispatch, () => queuedInputs.value.length], () => { void dispatchQueued() })
/* 任务开始/结束时立即把轮询切到对应频率，不必等空闲周期走完。 */
watch([hasRunning, busy, submittingDirector, pendingTaskRequest], () => { if (!disposed) scheduleRefresh() })
watch(() => `${project.value?.project.project_id}:${mode.value}:${section.value}:${selectedStage.value}:${selectedRun.value}:${spec.value?.version}:${chatExpanded.value}`, async (key, old) => {
  if (stageScroll.value) scrollPositions.set(old, stageScroll.value.scrollTop)
  await nextTick()
  if (stageScroll.value) stageScroll.value.scrollTop = scrollPositions.get(key) ?? 0
})
watch(() => transcript.value.map(item => `${item.execution.run_id}:${item.execution.status}`).join('|') + pendingText.value + queuedInputs.value.length + productionRun.value?.current_node + productionRun.value?.status + conversationMessages.value.length, async () => { if (!nearBottom.value) return; await nextTick(); timeline.value?.scrollTo({ top: timeline.value.scrollHeight, behavior: 'auto' }) })
onMounted(async () => {
  scheduleRefresh()
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', pollReentry)
  }
  try {
    const worksReady = loadWorks()
    let historyFailure: unknown
    const historyReady = loadConversations(worksReady).catch(failure => {
      historyFailure = failure
      if (!disposed) refreshConnectionError.value = failureText(failure)
    })
    await worksReady
    const scoped = workspaceWorks.value.find(work => work.work_id === route.value.workId)
    if (scoped) { await Promise.all([openWorkspace(scoped, route.value.taskId, true), historyReady]); return }
    if (route.value.workId && !route.value.workId.includes(':brief:')) { error.value = '无法读取指定作品，请从作品列表明确选择。'; return }
    // 裸工作区入口只恢复已有作品；不能因现代作品的对话已从旧列表中过滤而新建。
    if (!route.value.workId && !route.value.conversationId && !route.value.directorRunId
      && !props.initialRunId && workspaceWorks.value.length) {
      await Promise.all([openWorkspace(workspaceWorks.value[0]!, route.value.taskId, true), historyReady])
      return
    }
    const legacy = props.initialRunId ? await getRun(props.initialRunId) : null
    const requestedPage = route.value.workspacePage
    const requestedStage = route.value.directorStage
    const requestedMode = route.value.creationMode
    const requestedRunId = route.value.directorRunId ?? (legacy?.workflow === 'comic.director' ? legacy.id : undefined)
    const stored = localStorage.getItem('kantoku-comic-active-conversation')
    // URL 已指定会话时立即打开；只有需要从历史选择默认会话时才等待侧栏。
    if (!route.value.conversationId && typeof legacy?.state.conversation_id !== 'string') {
      await historyReady
      if (historyFailure) throw historyFailure
    }
    const id = route.value.conversationId ?? (typeof legacy?.state.conversation_id === 'string' ? legacy.state.conversation_id : null)
      ?? (conversations.value.some(item => item.id === stored) ? stored : null) ?? conversations.value[0]?.id
    // 携带 workspacePage 进入 = 刷新恢复现场，不得回到对话页（UX-006）
    const restoring = !!requestedPage && requestedPage !== 'conversation'
    await Promise.all([id ? openConversation(id, requestedRunId, { restore: restoring }) : createNewWorkspace(), historyReady])
    if (requestedMode) mode.value = requestedMode
    if (restoring) {
      section.value = toInternalSection(requestedPage); selectedStage.value = requestedStage ?? 'director_assemble'; chatExpanded.value = false
      await loadPage()
    }
  } catch (failure) { error.value = failureText(failure) }
  finally { loading.value = false }
})
onBeforeUnmount(() => {
  disposed = true; pageRequest++; storyWorkbench.dispose()
  if (timer) clearTimeout(timer)
  if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', pollReentry)
  Object.values(referenceUrls.value).forEach(url => URL.revokeObjectURL(url))
})
</script>

<template>
  <WorkspaceShell v-model:navigation-open="navOpen" :navigation-collapsed="navCollapsed" hide-heading hide-navigation :navigation="navigation" :active-page="activeNavigation" :creating-conversation="conversationBusy" identity="Kantoku" subtitle="漫剧创作" workspace-label="Comic 漫剧工作台" new-conversation-label="新建作品" @update:navigation-collapsed="toggleNavCollapsed" @select-page="openSection" @new-conversation="createNewWorkspace">
    <template #toolbar>
      <div class="toolbar-identity">
        <strong class="toolbar-project" :title="projectTitle">{{ projectTitle }}</strong>
        <small v-if="activeWork" class="toolbar-task" :title="activeTask?.title">· {{ activeTask?.title || '尚未添加画面' }}</small>
      </div>
      <span class="toolbar-status" role="status">{{ executionLabel }}</span>
      <button type="button" class="ui-button quiet sm toolbar-history" :aria-expanded="historyDrawer" :aria-pressed="historyDrawer" title="作品与历史对话（Esc 关闭）" @click="historyDrawer = true">
        <History :size="14" /><span>历史</span><b v-if="historyEntries.length">{{ historyEntries.length }}</b>
      </button>
      <UiDropdown
        v-model="mode"
        class="toolbar-mode"
        label="创作模式"
        menu-label="创作模式"
        title="导演方案需人工确认 · 外部图片"
        :options="modeOptions"
        :disabled="busy || hasRunning || loading"
      />
      <button v-if="activeWork" class="ui-button quiet sm" :disabled="conversationBusy" @click="addImageTask()">新增画面</button>
      <button class="ui-button quiet sm toolbar-new" :disabled="busy || hasRunning" @click="newProject">新建作品</button>
    </template>
            <template #heading>
              <div><small>{{ chatExpanded ? 'AI 导演助手' : section === 'director' ? '导演工作区' : '项目工作区' }}</small><h2>{{ chatExpanded ? projectTitle : title }}</h2></div>
              <span v-if="!chatExpanded && section === 'director' && spec" class="node-version">v{{ spec.version }}<span v-if="dirty"> · 未保存修改</span></span>
              <span v-if="refreshConnectionError" class="workspace-connection-notice" role="status" :title="refreshConnectionError">连接中断，正在恢复<button class="ui-button quiet sm" @click="refresh().catch(failure => refreshConnectionError = failureText(failure))">重新读取</button></span>
            </template>
        <div class="workspace-content">
          <main class="workspace-stage" :class="{ 'is-chat': chatExpanded }">
            <div class="trackWrap">
              <ol class="level" aria-label="漫剧制作流程">
                <li>
                  <button type="button" class="step" :data-status="activeNavigation === 'conversation' ? 'current' : (activeWork || chatTurns.length ? 'done' : 'todo')" :aria-current="activeNavigation === 'conversation' ? 'step' : undefined" @click="chatExpanded = true">
                    <span class="block"><svg v-if="activeNavigation === 'conversation'" class="sprite" viewBox="0 0 18 22" aria-hidden="true"><path fill="#e43030" d="M3 0h12v4h3v5H0V4h3z"/><path fill="#ffe24b" d="M3 8h12v7H3z"/><path fill="#3a5bd9" d="M2 15h14v5H2z"/><path fill="#141414" d="M0 20h7v2H0zm11 0h7v2h-7z"/></svg><i v-else-if="activeWork || chatTurns.length" class="coin" /><span class="num">00</span>对话</span>
                  </button>
                </li>
                <li v-for="(step, index) in productionSteps" :key="step.id">
                  <button type="button" class="step" :data-status="step.status" :aria-current="activeNavigation === step.id ? 'step' : undefined" :title="step.hint" @click="openSection(step.id)">
                    <span class="block"><svg v-if="step.status === 'current'" class="sprite" viewBox="0 0 18 22" aria-hidden="true"><path fill="#e43030" d="M3 0h12v4h3v5H0V4h3z"/><path fill="#ffe24b" d="M3 8h12v7H3z"/><path fill="#3a5bd9" d="M2 15h14v5H2z"/><path fill="#141414" d="M0 20h7v2H0zm11 0h7v2h-7z"/></svg><i v-else-if="step.status === 'done'" class="coin" /><span class="num">{{ String(index + 1).padStart(2, '0') }}</span>{{ step.label }}</span>
                  </button>
                </li>
                <li class="goal" aria-hidden="true"><i class="pole" /></li>
              </ol>
              <div class="ground" aria-hidden="true" />
            </div>
              <div v-if="!activeWork && works.length" class="workspace-work-choice" aria-label="历史对话的方案来源">
                <UiDropdown :model-value="selectedWorkId" :options="workOptions" label="当前作品" menu-label="本对话的作品" :disabled="loading" @update:model-value="value => selectWork(value)" />
                <button v-if="chatExpanded" class="ui-button quiet sm" @click="openSection(currentWork?.has_director ? 'plan' : 'images')">打开作品 <ArrowRight :size="14" /></button>
                <small v-if="['story', 'export', 'video'].includes(section) && !chatExpanded">聊天故事：{{ storyWorkbench.state.view?.story?.title || '尚未编排' }} · 可跨作品选图</small>
                <small v-else-if="currentWork && !currentWork.has_director">导入图片作品 · 尚无导演方案</small>
              </div>
              <LegacyWorkImport v-if="!activeWork && activeConversationId" :conversation-id="activeConversationId" @imported="async work => { await loadWorks(); await loadConversations(); await openWorkspace(work) }" />
              <div v-if="activeWork?.migration && !activeWork.migration.completed" class="pane-note" role="status">历史归入尚未完成，已复制内容保留。<button class="ui-button sm" @click="async () => { if (!activeWork?.migration) return; try { const result = await importComicLegacy(activeWork.migration.request); await loadWorks(); await openWorkspace(result) } catch (failure) { error = failureText(failure) } }">继续原归入请求</button></div>
              <p v-if="pendingTaskRequest && !busy" class="pane-note" role="status">正在同步上一轮请求结果，不会重复提交。<button class="ui-button sm" @click="recoverTaskRequest">查询原请求结果</button></p>
              <p v-if="activeTask?.settings_updates?.length" class="pane-note" role="status">作品公共设定有 {{ activeTask.settings_updates.length }} 项可更新。<button class="ui-button sm" @click="openSection('assets')">查看并选择同步</button></p>
              <p v-if="activeTask?.legacy_source" class="pane-note">来源：历史方案 Brief {{ activeTask.legacy_source.brief_version ?? '未绑定' }} · <a :href="`/workspace/comic/plan?conversation=${encodeURIComponent(activeTask.legacy_source.conversation_id)}&work=${encodeURIComponent(activeTask.legacy_source.source_id)}${activeTask.legacy_source.director_run_id ? '&director_run=' + encodeURIComponent(activeTask.legacy_source.director_run_id) : ''}`">查看原方案、Prompt 和图片历史</a></p>

              <div class="panelWrap">
              <Transition name="ws-cross" mode="out-in">
              <div v-if="!chatExpanded" :key="`${section}:${selectedStage}:${selectedRun}`" ref="stageScroll" class="stage-scroll panel on" :class="{ 'director-stage-scroll': section === 'director', 'prompt-stage-scroll': section === 'storyboard' || section === 'prompt', 'image-workbench-stage': section === 'images', 'flow-stage-scroll': section === 'story' || section === 'export', 'video-stage-scroll': section === 'video' }" aria-label="当前节点工作区">
                <!-- 七步制作流程条：单行椭圆胶囊（与「导演流程」同一套视觉语言），
                     只占一行高度，避免此前两行卡片式导航挤压工作区。
                     三态语汇：已完成=实心勾；当前=白底滑块 + 强调色；待办=空心环。 -->
                <div v-if="section === 'images' && (busy || hasRunning)" class="execution-controls" role="status"><span>{{ executionLabel }}</span><button v-if="hasRunning" class="ui-button quiet sm" :disabled="cancelling" @click="cancelExecution">{{ cancelling ? '正在取消' : '取消当前任务' }}</button></div>
                <p v-if="section === 'images' && error" class="workspace-error" role="alert">{{ error }}</p>
                <template v-if="section === 'director'">
                  <div v-if="currentWork && !currentWork.has_director" class="pane-note" role="status"><p>这份作品从图片导入，尚未生成导演方案。可直接进入图片工作台，或将已采用图片加入聊天故事。</p><button class="ui-button sm" @click="openSection('images')">打开本作品图片</button><button class="ui-button sm" @click="openSection('story')">打开聊天故事</button></div>
<p class="pipeline-caption">导演流程 · {{ Object.keys(visibleNodes).length }} 个节点<span v-if="nodeCollapsed" class="pipeline-caption-hint">节点内容已收起 · 点击任一节点展开</span></p>
<div class="dir-grid">
  <div class="pipe-col">
    <nav class="director-flow" aria-label="导演流程">
      <template v-for="(label, stage, index) in visibleNodes" :key="stage">
        <div class="pnode" :class="nodeClass(String(stage))">
          <span class="pnode__rail" aria-hidden="true">
            <span class="pnode__blk">{{ String(index + 1).padStart(2, '0') }}</span>
            <i class="pnode__cord"></i>
          </span>
          <div class="pnode__box">
            <button type="button" class="pnode__hd" :aria-current="selectedStage === stage ? 'step' : undefined" :aria-expanded="isNodeOpen(String(stage))" :data-status="nodeStatus(String(stage))" @click="visitStage(String(stage))">
              <strong>{{ label }}</strong>
              <span class="mk-sticker" :class="nodeSticker(String(stage))">{{ nodeState(String(stage)) }}</span>
              <span v-if="spec && editableDirectorNode(String(stage))" class="mk-sticker" :class="nodeEditable(String(stage)) ? 'is-gold' : ''">{{ nodeEditable(String(stage)) ? '可编辑' : '只读' }}</span>
              <span class="pnode__cnt">{{ isNodeOpen(String(stage)) ? '展开中' : nodePreview(String(stage)) ? '点击展开' : '待生成' }}</span>
            </button>
            <p v-if="!isNodeOpen(String(stage))" class="pnode__sum">{{ nodePreview(String(stage)) || '尚未生成内容' }}</p>
            <div v-else class="pnode__body">
                  <Transition name="panel-collapse">
                  <section v-if="stale && spec" class="stale-plan" role="status">
                    <div class="stale-plan-head">
                      <div>
                        <strong>这是历史版本 v{{ spec.version }}</strong>
                        <small v-if="stalePlanSavedAt">保存于 {{ stalePlanSavedAt }}</small>
                      </div>
                      <button class="ui-button primary sm" :disabled="busy || hasRunning" @click="restore(Number(spec.version))">修改这份方案</button>
                    </div>
                    <p>作品已推进到 v{{ project?.project.director_version }}，历史版本只读。要再次微调，先把它恢复为新版本，即可在导演层继续修改；原版本与产出都会保留。</p>
                  </section>
                  </Transition>
                  <!-- UX-010：区分"正在看历史"与"正在看当前稿"，并给出回当前稿的唯一入口。 -->
                  <Transition name="panel-collapse">
                  <section v-if="currentDirectorIsNewer && mode === 'professional'" class="current-plan" role="status">
                    <div class="stale-plan-head">
                      <div>
                        <strong>正在查看历史版本 v{{ spec?.version }}</strong>
                        <small>作品当前稿是 v{{ currentDirector?.version }}，可继续编辑</small>
                      </div>
                      <button class="ui-button primary sm" :disabled="busy || hasRunning" @click="viewCurrentDirector">查看当前版本</button>
                    </div>
                    <p>历史版本只读，不会影响当前稿。切换到当前版本后可继续修改、审核与确认。</p>
                  </section>
                  </Transition>
                  <p v-if="(restoredSpec || currentDirectorIsNewer) && mode === 'professional'" class="pane-note">查看当前保存的版本；节点没有重新执行，不沿用其他版本的执行状态。</p>
                  <p v-if="summary?.mode === 'fast' && mode === 'professional' && selectedStage !== 'director_assemble'" class="pane-note">显示已保存方案的对应内容；此任务未公开逐节点执行记录。</p>
                  <p v-if="selectedStage === 'cinematography' && (spec?.cinematography as { status?: string })?.status && (spec?.cinematography as { status?: string })?.status !== 'complete'" class="review-notice">摄影方案待补充或调整。已保留真实草稿，不会自动进入制作。</p>
                  <p v-if="spec?.critic_status === 'unavailable'" class="review-notice" role="status">导演审核暂不可用，未产生合法审核结果。导演方案已保留；你可以查看并确认最终方案后进入分镜。</p>
                  <DirectorNodeView ref="directorNodeView" :mode="mode" :stage="selectedStage" :node="node" :spec="spec" :fields="fields" :editing="editing" :editable="editable" :rerunnable="mode === 'professional' && !restoredSpec && !!node && !!summary?.available_actions.includes('rerun_stage') && !dirty" :busy="busy || hasRunning" :critic="summary?.critic_result" :critic-feedback="summary?.critic_feedback" @edit="beginEdit" @field="updateField" @cancel="discardNode" @save="spec?.schema_version === 2 ? saveFinal() : rerun(true)" @rerun="rerun()" @revise="editFinal('visual_direction')" @open="visitStage" @locate="locateSuggestion" />
                  <p v-if="spec?.schema_version !== 2 && spec" class="pane-note">这是旧版方案，仅保留历史查看。请在对话中重新生成 v2 导演方案。</p>
                  <div v-if="spec && selectedStage !== 'director_assemble' && !editing && selectedStage !== 'director_critic'" class="pnode__acts">
                    <button class="ui-button quiet sm" @click="visitStage('director_assemble')">{{ mode === 'fast' ? '查看整体方案并确认' : '返回最终导演稿' }}</button>
                    <small>本节点只改这一段，其他节点保持不变</small>
                  </div>
            </div>
          </div>
        </div>
      </template>
    </nav>
  </div>

  <!-- 确认条是整页动作，横跨两栏放在关卡轨道之下：
       之前它 sticky 在轨道内部，会压住展开节点里的方案卡。 -->
  <div v-if="manualDirectorApproval && spec && selectedStage === 'director_assemble'" class="final-approval"><p>{{ stale ? '来源已变化，需要更新方案' : confirmed ? '当前版本已确认，可以进入分镜制作' : dirty ? '请先保存编辑，再检查并确认' : '这是可修改的草稿，确认后才进入下一阶段。' }}</p><div class="draft-actions"><button class="ui-button sm" :disabled="busy || hasRunning || stale || dirty" @click="reviewFinal">检查当前方案</button><button class="ui-button sm" :disabled="busy || hasRunning || stale || dirty" @click="reviseByInstruction">用对话修改</button><button class="ui-button sm" :disabled="busy || hasRunning || dirty" @click="regenerate">重新生成</button><button class="ui-button primary sm" :disabled="!confirmable || confirmed" @click="confirm">确认最终方案</button><button class="ui-button sm" :disabled="!confirmed || !productionEligible || busy || hasRunning" @click="enterStoryboard">进入下一步</button></div></div>

  <aside class="audit-col" aria-label="导演方案审核与约束">
    <section class="audit-sheet" :class="{ 'is-empty': !directorFindings.length, 'is-quiet': !directorFindings.length && spec, 'is-pass': !directorFindings.length && spec && spec.critic_status !== 'unavailable' }">
      <header>导演审核 <small v-if="auditStateLabel">{{ auditStateLabel }}</small></header>
      <div v-if="directorFindings.length" class="audit-body">
        <details v-for="finding in directorBlocking" :key="finding.affected_field + finding.message" class="audit-finding is-blocking" open>
          <summary><span class="mk-sticker is-brick">须改</span><span>{{ finding.affected_field_label }} · {{ finding.message }}</span></summary>
          <p class="audit-finding__why">{{ finding.reason }}</p>
          <p v-if="finding.suggestion" class="audit-finding__todo">{{ finding.suggestion }}</p>
          <button type="button" class="ui-button sm" :disabled="busy || hasRunning" @click="locateSuggestion(finding.affected_field)">定位修改</button>
        </details>
        <details v-if="directorAdvisory.length" class="audit-more">
          <summary>另有 {{ directorAdvisory.length }} 条优化建议</summary>
          <article v-for="finding in directorAdvisory" :key="finding.affected_field + finding.message" class="audit-finding is-advisory">
            <strong>{{ finding.affected_field_label }} · {{ finding.message }}</strong>
            <p>{{ finding.reason }}</p>
            <button type="button" class="ui-button quiet sm" :disabled="busy || hasRunning" @click="locateSuggestion(finding.affected_field)">定位修改</button>
          </article>
        </details>
      </div>
      <p v-else class="audit-quiet" :class="{ 'is-pass': spec && spec.critic_status !== 'unavailable' }">
        <span class="audit-quiet__mark" aria-hidden="true">{{ spec && spec.critic_status !== 'unavailable' ? '✓' : '!' }}</span>
        <span>{{ auditQuietText }}</span>
      </p>
    </section>
    <section class="audit-sheet" :class="{ 'is-empty': !directorConstraints.length }">
      <header>创作约束 <small v-if="directorConstraints.length">{{ directorConstraints.length }} 条</small></header>
      <ul v-if="directorConstraints.length" class="audit-body con-list"><li v-for="(constraint, index) in directorConstraints" :key="index">{{ constraint }}</li></ul>
      <p v-else class="audit-oneline">尚无已记录的硬约束</p>
    </section>
    <section class="audit-sheet" :class="{ 'is-empty': !spec }">
      <header>方案状态与产物 <small v-if="spec">v{{ spec.version }}</small></header>
      <div v-if="spec" class="audit-body">
        <dl>
          <div><dt>当前方案</dt><dd>{{ stale ? '来源已变化' : confirmed ? '已确认' : '草稿' }}</dd></div>
          <div><dt>方案来源</dt><dd>{{ specOriginLabel }}</dd></div>
          <div><dt>待处理意见</dt><dd>{{ directorFindings.length ? `${directorFindings.length} 条` : '无' }}</dd></div>
          <div><dt>已导入图片</dt><dd>{{ uploadedPrompts.length }}</dd></div>
        </dl>
        <p class="audit-note">初稿按需求一次生成时默认通过；你在节点里改动保存后会重新审核。</p>
      </div>
      <p v-else class="audit-oneline">尚未生成方案</p>
    </section>
  </aside>
</div>
                </template>
                <template v-else-if="section === 'assets'">
                  <details v-if="activeWork"><summary>引用或发布作品公共设定</summary><WorkSettings :key="activeWork.work_id + ':' + activeTask?.task_id" :work-id="activeWork.work_id" :task-id="activeTask?.task_id" :local-assets="assets" @changed="refresh(); loadWorks()" /></details>
                  <!-- 交付优先：先给"已经拿到的镜头图片"和它的下载入口，
                       空的分组折叠收起，不让"暂无"占据页面主导（UX-011）。 -->
                  <section v-if="featuredPrompt" class="delivery-hero" aria-label="当前镜头交付">
                    <header class="delivery-head">
                      <div>
                        <h3>当前镜头图片</h3>
                        <small>镜头 {{ featuredPrompt.shot_sequence_number }} · {{ featuredPrompt.shot_subject }} · Prompt v{{ featuredPrompt.version }}</small>
                      </div>
                      <a v-if="referenceUrls[uploadedArtifact(featuredPrompt)]" class="ui-button primary sm" :href="referenceUrls[uploadedArtifact(featuredPrompt)]" download="kantoku-shot.png">下载当前镜头图片</a>
                    </header>
                    <ChatImageAttachment v-if="referenceUrls[uploadedArtifact(featuredPrompt)]" :media="{ url: referenceUrls[uploadedArtifact(featuredPrompt)]!, filename: 'kantoku-shot.png' }" @open="previewReference" />
                    <p v-else class="pane-note">图片正在载入；若长时间未出现，请重新进入本页。</p>
                    <div class="draft-actions">
                      <button class="ui-button sm" @click="viewImagePrompt(shots.find(item => item.shot_id === String(featuredPrompt.shot_id)) ?? shots[0])">查看完整 Image Prompt</button>
                      <button class="ui-button quiet sm" @click="openSection('storyboard')">回到分镜页</button>
                    </div>
                    <p class="pane-note">已导入镜头图片 = 外部图片已登记为项目 Artifact，可用于后续环节；<strong>它不代表视觉质检已通过，也不代表视频已生成</strong>。本工作流只产出 Image Prompt 与图片资产，视频能力暂未开放。</p>
                  </section>
                  <p v-else-if="!projectPrompts.length" class="pane-note">当前项目还没有 Prompt 资产。先在分镜页生成完整 Image Prompt，再到外部模型出图并上传，这里就会出现可下载的镜头图片。</p>

                  <section v-if="uploadedPrompts.length > 1" class="asset-group">
                    <h3>其他已导入图片 · {{ uploadedPrompts.length - 1 }} 个镜头</h3>
                    <article v-for="prompt in uploadedPrompts.filter(item => item !== featuredPrompt)" :key="uploadedArtifact(prompt)" class="delivery-row">
                      <p>镜头 {{ prompt.shot_sequence_number }} · {{ prompt.shot_subject }} · Prompt v{{ prompt.version }}</p>
                      <ChatImageAttachment v-if="referenceUrls[uploadedArtifact(prompt)]" :media="{ url: referenceUrls[uploadedArtifact(prompt)]!, filename: `kantoku-shot-${prompt.shot_sequence_number}.png` }" @open="previewReference" />
                    </article>
                  </section>

                  <section v-if="pendingPrompts.length" class="asset-group">
                    <h3>待出图镜头 · {{ pendingPrompts.length }} 个</h3>
                    <p v-for="prompt in pendingPrompts" :key="String(prompt.artifact_id)" class="pane-note">镜头 {{ prompt.shot_sequence_number }} · {{ prompt.shot_subject }} · Prompt v{{ prompt.version }} —— 已生成 Prompt，等待外部出图后上传。</p>
                  </section>

                  <!-- 资产分组折叠保留，但每个资产补上勾选与删除入口。
                       顶部管理条汇总"已选 N 项 / 批量删除"，与 Prompt 版本、历史版本三处保持同一套。 -->
                  <div v-if="liveAssets.length" class="list-toolbar">
                    <strong>项目资产</strong>
                    <span class="list-toolbar-tag">{{ liveAssets.length }} 项</span>
                    <span class="list-toolbar-grow"></span>
                    <span v-if="selectedAssetIds.length" class="list-toolbar-count">已选 {{ selectedAssetIds.length }} 项</span>
                    <button class="ui-button sm" :disabled="deleting || busy || hasRunning || !selectedAssets.length" @click="askAssetDeletion(selectedAssets)">批量删除</button>
                  </div>
                  <p v-if="deletionFeedback" class="pane-note" role="status">{{ deletionFeedback }}</p>
                  <div v-if="deletionFailures.length" class="delete-failures" role="alert"><p>{{ deletionFailures.length }} 项未完成，请刷新核实后重试：</p><ul><li v-for="failure in deletionFailures" :key="failure.label">{{ failure.label }}：{{ failure.reason }}</li></ul></div>
                  <details v-for="(label, kind) in { character: '角色资产', scene: '场景资产', style: '风格资产' }" :key="kind" class="asset-group collapsible" :open="assets.some(item => item.details.kind === kind && item.state !== 'deleted')">
                    <summary>{{ label }} <small v-if="assets.some(item => item.details.kind === kind && item.state !== 'deleted')">{{ assets.filter(item => item.details.kind === kind && item.state !== 'deleted').length }} 项</small><small v-else>暂无</small></summary>
                    <details v-for="asset in assets.filter(item => item.details.kind === kind && item.state !== 'deleted')" :key="asset.asset_id" class="asset-item" :class="{ 'is-picked': selectedAssetIds.includes(asset.asset_id) }"><summary>{{ asset.name }} <small>v{{ asset.version }}{{ asset.pinned_version ? ` · 锁定 v${asset.pinned_version}` : '' }}</small></summary><dl><div v-for="(value, key) in Object.fromEntries(Object.entries(asset.details).filter(([key]) => key !== 'kind'))" :key="key"><dt>{{ assetFieldLabels[key] ?? key }}</dt><dd>{{ Array.isArray(value) ? value.join('；') : value }}</dd></div></dl><p>固定约束：{{ asset.fixed_constraints.join('；') || '未指定' }}</p><p>参考图：{{ asset.reference_artifact_ids.length }} 张</p><div class="asset-actions"><label class="asset-pick"><input type="checkbox" :checked="selectedAssetIds.includes(asset.asset_id)" :disabled="deleting" @change="toggleAssetSelection(asset.asset_id)" />选择</label><button class="ui-button sm danger" :disabled="deleting || busy || hasRunning" @click="askAssetDeletion([asset])">删除此资产</button></div></details>
                  </details>

                  <details class="asset-group collapsible" :open="assets.flatMap(asset => asset.reference_artifact_ids).length > 0">
                    <summary>参考素材 <small>{{ [...new Set(assets.flatMap(asset => asset.reference_artifact_ids))].length }} 张</small></summary>
                    <div v-for="id in [...new Set(assets.flatMap(asset => asset.reference_artifact_ids))]" :key="id"><button class="ui-button quiet sm" @click="openReference(id)">查看参考图 · {{ id }}</button><ChatImageAttachment v-if="referenceUrls[id]" :media="{ url: referenceUrls[id]!, filename: `reference-${id}.png` }" @open="previewReference" /><p v-if="referenceErrors[id]" role="alert">{{ referenceErrors[id] }}</p></div>
                    <p class="pane-note">参考素材沿用资产版本；外部图片由上传文件登记为真实 Artifact。</p>
                  </details>
                  <!-- Future capability: legacy internal production remains off the external path. -->
                  <template v-if="imageMode !== 'external'"><h3>生成结果</h3><slot name="works" /></template>
                </template>
                <template v-else-if="section === 'storyboard' || section === 'prompt'">
                  <header class="production-stage-heading"><div><span>02 · IMAGE PROMPT</span><h2>生图指令</h2><p>确认每个镜头的画面描述，复制指令后导入外部图片。</p></div><span class="production-stage-count">{{ shots.length }} 个镜头</span></header>
                  <div v-if="promptEdit" class="draft-actions prompt-editor">
                    <label>正向 Prompt<textarea v-model="promptEdit.positive_prompt" rows="6" /></label>
                    <label>负向约束<textarea v-model="promptEdit.negative_prompt" rows="3" /></label>
                    <button class="ui-button primary sm" :disabled="busy || hasRunning || !promptEdit.positive_prompt.trim()" @click="saveImagePrompt">保存为新版本</button>
                    <button class="ui-button quiet sm" @click="promptEdit = null">取消修改</button>
                  </div>
                  <p v-if="section === 'storyboard' && manualDirectorApproval && !confirmed" class="review-notice">请先确认当前导演方案再制作。</p>
                  <template v-if="section === 'storyboard'">
                    <div class="board-bar">
                      <label v-if="boards.length" class="board-picker"><span>分镜</span><select v-model="selectedBoard" @change="loadShots"><option v-for="board in boards" :key="board.storyboard_id" :value="board.storyboard_id">{{ board.title }} · v{{ board.version }}</option></select></label>
                      <span v-if="shots.length" class="board-count">共 {{ shots.length }} 个镜头</span>
                    </div>
                    <p v-if="!boards.length" class="pane-note">尚无作品分镜。确认导演方案后，可开始分镜制作。</p>
                    <article v-for="shot in shots" :key="shot.shot_id" class="shot-card" :class="{ 'is-selected': selectedShot === shot.shot_id }" :data-shot-id="shot.shot_id" :data-status="shot.status">
                      <header class="shot-head">
                        <button class="shot-select" :aria-pressed="selectedShot === shot.shot_id" @click="selectedShot = shot.shot_id">
                          <span class="shot-index">镜头 {{ shot.sequence_number }}</span>
                          <span class="shot-subject">{{ shot.subject }}</span>
                        </button>
                        <span class="shot-meta">v{{ shot.version }} · {{ directorStateLabels[shot.status] ?? shot.status }}</span>
                      </header>
                      <dl class="shot-brief">
                        <div><dt>意图</dt><dd>{{ shot.purpose }}</dd></div>
                        <div v-if="shot.action"><dt>动作</dt><dd>{{ shot.action }}</dd></div>
                      </dl>
                      <ComicShotGeneration :run="null" :image-mode="imageMode" :preparing="preparingShot === shot.shot_id" :external-prompt="externalPromptForShot(shot)" :external-image-url="referenceUrls[uploadedArtifact(externalPromptForShot(shot))]" :uploading="uploadingShot === shot.shot_id" :prompt-error="shotErrors[shot.shot_id] || promptReadErrors[shot.shot_id]" :disabled="busy || hasRunning || compiling" :copying="!!copyingPrompt" :copy-feedback="copiedPrompt === String(externalPromptForShot(shot)?.artifact_id) ? promptCopyFeedback : ''" @view-prompt="viewImagePrompt(shot)" @copy-prompt="copyImagePrompt(externalPromptForShot(shot)!)" @copy-text="copyImagePrompt(externalPromptForShot(shot)!, 'text')" @copy-codex-task="copyCodexTask(externalPromptForShot(shot)!)" @edit-prompt="editImagePrompt(externalPromptForShot(shot)!)" @upload="importExternalImage(shot, $event)" @preview="previewReference" />
                    </article>
                    <div v-if="section === 'storyboard' && productionEligible && selectedShot" class="shot-footer">
                      <p>{{ selectedShotHint }}</p>
                      <button class="ui-button primary sm" :disabled="compiling || busy || hasRunning || !confirmed" @click="compilePrompt">{{ compiling ? '正在生成完整 Prompt' : '生成当前镜头完整 Image Prompt' }}</button>
                    </div>
                    <div v-else-if="section === 'storyboard' && selectedShot" class="shot-footer is-locked">
                      <p><strong>暂时不能生成完整指令</strong>{{ productionLockReason }}</p>
                      <button class="ui-button sm" disabled>生成当前镜头完整 Image Prompt</button>
                      <button class="ui-button quiet sm" @click="openSection('plan')">先去确认导演方案</button>
                    </div>
                  </template>
                  <template v-else>
                    <!-- Prompt 资产库：镜头用标签切换，版本用卡片呈现。
                         卡片把"第几版 / 什么来源 / 绑定哪一镜"摆到明面上，正文默认收起。 -->
                    <div v-if="projectPrompts.length" class="list-toolbar">
                      <strong>Prompt 资产</strong>
                      <span class="list-toolbar-tag">{{ projectPrompts.length }} 个镜头</span>
                      <span class="list-toolbar-grow"></span>
                      <span v-if="selectedPromptVersions.length" class="list-toolbar-count">已选 {{ selectedPromptVersions.length }} 项</span>
                      <button class="ui-button sm" :disabled="deleting || busy || hasRunning || !selectedPromptVersions.length" @click="askPromptDeletion(prompts.filter(item => selectedPromptVersions.includes(Number(item.version))))">批量删除</button>
                    </div>
                    <p v-if="!projectPrompts.length" class="pane-note">当前项目暂无 Prompt 资产，请在分镜页生成完整 Prompt。</p>
                    <div v-if="projectPrompts.length" class="shot-chips" aria-label="切换镜头">
                      <button v-for="prompt in projectPrompts" :key="String(prompt.prompt_id)" type="button" class="shot-chip" :class="{ 'is-active': String(prompt.shot_id) === selectedShot }" :aria-pressed="String(prompt.shot_id) === selectedShot" @click="selectPromptShot(String(prompt.shot_id))">镜头 {{ prompt.shot_sequence_number }} · {{ prompt.shot_subject }}<small>当前 v{{ prompt.version }}</small></button>
                    </div>
                    <article v-for="prompt in prompts" :key="String(prompt.artifact_id)" class="version-card" :class="{ 'is-current': Number(prompt.version) === currentPromptVersion, 'is-picked': selectedPromptVersions.includes(Number(prompt.version)) }">
                      <header class="version-head">
                        <label v-if="Number(prompt.version) !== currentPromptVersion" class="version-pick">
                          <input type="checkbox" :checked="selectedPromptVersions.includes(Number(prompt.version))" :disabled="deleting" @change="togglePromptVersion(Number(prompt.version))" />
                          <span class="sr-only">选择 Prompt v{{ prompt.version }}</span>
                        </label>
                        <span v-else class="version-pick-spacer" aria-hidden="true"></span>
                        <div class="version-info">
                          <div class="version-title">
                            <strong>Prompt v{{ prompt.version }}</strong>
                            <span v-if="Number(prompt.version) === currentPromptVersion" class="version-tag is-current">当前版本</span>
                            <span class="version-tag">{{ prompt.model_target }}</span>
                            <span class="version-tag">{{ prompt.source }}</span>
                          </div>
                          <small class="version-meta">绑定 镜头 {{ prompt.shot_sequence_number }} · {{ prompt.shot_subject }} · 导演 v{{ prompt.director_spec_version }} · 镜头 v{{ prompt.shot_version }}</small>
                        </div>
                      </header>
                      <details class="version-prompt">
                        <summary>查看 Prompt 正文</summary>
                        <pre class="prompt-text">{{ finalImagePrompt(prompt) }}</pre>
                      </details>
                      <div class="version-actions">
                        <button class="ui-button sm" @click="promptPreview = prompt">查看完整 Prompt 与来源</button>
                        <button class="ui-button sm" :disabled="!!copyingPrompt" @click="copyImagePrompt(prompt)">{{ imagePromptReferences(prompt).length ? '复制图文' : '复制 Prompt' }}</button>
                        <button class="ui-button sm" title="本机资料交付，待实际出图验收" :disabled="!!copyingPrompt" @click="copyCodexTask(prompt)">复制 Codex 工作任务</button><button class="ui-button sm" @click="exportImagePrompt(prompt)">导出文字</button>
                        <button v-if="Number(prompt.version) === currentPromptVersion" class="ui-button sm" :disabled="busy || hasRunning" @click="editImagePrompt(prompt)">编辑为新版本</button>
                        <button v-else class="ui-button sm" :disabled="busy || hasRunning" @click="promptRestoreChoice = Number(prompt.version)">恢复为新版本</button>
                        <button v-if="Number(prompt.version) !== currentPromptVersion" class="ui-button sm danger" :disabled="deleting || busy || hasRunning" @click="askPromptDeletion([prompt])">删除此版本</button>
                        <span v-else class="version-locked" title="当前正在使用的版本不能删除">当前版本不可删除</span>
                      </div>
                      <div v-if="promptRestoreChoice === Number(prompt.version)" class="review-notice" role="status"><p>将 Prompt v{{ prompt.version }} 恢复为新版本 v{{ Number(prompts[0]?.version ?? 0) + 1 }}。历史版本全部保留，当前确认状态会清除，恢复后需重新确认。</p><p v-if="stalePromptSource(prompt)">{{ stalePromptSource(prompt) }}</p><button class="ui-button primary sm" :disabled="busy || hasRunning || !!stalePromptSource(prompt)" @click="restorePrompt(prompt)">确认恢复</button><button class="ui-button quiet sm" :disabled="busy" @click="promptRestoreChoice = null">取消</button></div>
                      <p v-if="copiedPrompt === String(prompt.artifact_id)" class="pane-note" role="status">{{ promptCopyFeedback }}</p>
                    </article>
                    <p v-if="deletionFeedback" class="pane-note" role="status">{{ deletionFeedback }}</p>
                    <div v-if="deletionFailures.length" class="delete-failures" role="alert"><p>{{ deletionFailures.length }} 项未完成，请刷新核实后重试：</p><ul><li v-for="failure in deletionFailures" :key="failure.label">{{ failure.label }}：{{ failure.reason }}</li></ul></div>
                    <section v-if="prompts.length > 1"><h3>对比版本</h3><label>旧版本<select v-model.number="compareVersions[0]"><option v-for="prompt in prompts" :key="Number(prompt.version)" :value="Number(prompt.version)">v{{ prompt.version }}</option></select></label><label>新版本<select v-model.number="compareVersions[1]"><option v-for="prompt in prompts" :key="Number(prompt.version)" :value="Number(prompt.version)">v{{ prompt.version }}</option></select></label><table><thead><tr><th>旧内容</th><th>新内容</th></tr></thead><tbody><tr v-for="(line, index) in comparison" :key="index"><td>{{ line.before }}</td><td>{{ line.after }}</td></tr></tbody></table><p v-if="!comparison.length">所选版本内容相同。</p></section>
                  </template>
                  <!-- 完整 Prompt 统一放到区块末尾：它由上方动作触发，紧邻操作位置，不必回滚页面。 -->
                  <section v-if="promptPreview && !promptEdit" class="prompt-preview" aria-label="完整 Image Prompt">
                    <header><strong>当前镜头说明 · v{{ promptPreview.version }}</strong><button class="ui-button quiet sm" @click="promptPreview = null">关闭</button></header>
                    <section v-if="imagePromptReferences(promptPreview).length" class="prompt-references" aria-label="本次生成需要的参考图">
                      <p>本轮参考图及用途如下。复制工作任务后，在能读取作品资料夹的 Codex 本机工作模式中执行；实际出图尚待验收。</p>
                      <article v-for="reference in imagePromptReferences(promptPreview)" :key="reference.artifact_id + reference.sha256">
                        <strong>{{ reference.filename }} · v{{ reference.version }}</strong><p v-if="reference.target">{{ reference.target }}</p>
                        <ChatImageAttachment v-if="referenceUrls[promptReferenceKey(reference)]" :media="{ url: referenceUrls[promptReferenceKey(reference)]!, filename: reference.filename }" @open="previewReference" />
                        <p v-else-if="referenceErrors[promptReferenceKey(reference)]" role="alert">{{ referenceErrors[promptReferenceKey(reference)] }}</p>
                        <p v-else role="status">正在载入参考图…</p>
                        <div class="draft-actions"><button class="ui-button sm" :disabled="!!copyingPrompt" @click="copyImagePrompt(promptPreview, 'image', reference)">复制这张参考图</button><button class="ui-button sm" @click="downloadPromptReference(promptPreview, reference)">下载原图</button><button v-if="referenceErrors[promptReferenceKey(reference)]" class="ui-button quiet sm" @click="openPromptReference(reference)">重新读取</button></div>
                      </article>
                    </section>
                    <section v-if="visualContinuityMessages(promptPreview).length" class="review-notice" role="status"><p>参考画面的设定核对</p><ul><li v-for="message in visualContinuityMessages(promptPreview)" :key="message">{{ message }}</li></ul></section><pre>{{ finalImagePrompt(promptPreview) }}</pre>
                    <div class="draft-actions"><button class="ui-button sm" title="本机资料交付，待实际出图验收" :disabled="!!copyingPrompt" @click="copyCodexTask(promptPreview)">复制 Codex 工作任务</button><button class="ui-button sm" :disabled="!!copyingPrompt" @click="copyImagePrompt(promptPreview)">{{ imagePromptReferences(promptPreview).length ? '复制图文' : '复制生图 Prompt' }}</button><button v-if="imagePromptReferences(promptPreview).length" class="ui-button sm" :disabled="!!copyingPrompt" @click="copyImagePrompt(promptPreview, 'text')">仅复制文字</button><button class="ui-button sm" @click="exportImagePrompt(promptPreview)">导出文字</button><button class="ui-button sm" :disabled="busy || hasRunning" @click="editImagePrompt(promptPreview)">编辑为新版本</button><button v-if="promptPreview.context_version === 'image-bibles-1'" class="ui-button sm" :disabled="busy || hasRunning || promptPreview.user_confirmed === true" @click="confirmImagePrompt(promptPreview)">{{ promptPreview.user_confirmed ? '当前 Prompt 已确认' : '确认当前 Prompt' }}</button></div>
                    <p v-if="copiedPrompt === String(promptPreview.artifact_id)" role="status">{{ promptCopyFeedback }}</p>
                    <details><summary>查看来源</summary><p>导演 v{{ promptPreview.director_spec_version }} · 镜头 v{{ promptPreview.shot_version }} · {{ promptPreview.compiler_version }}</p><pre>{{ promptPreview.positive_prompt }}</pre><pre>{{ JSON.stringify(promptPreview.context_sources, null, 2) }}</pre></details>
                  </section>
                </template>
                <template v-else-if="section === 'history'">
                  <h3>方案版本</h3>
                  <div v-if="versions.length" class="list-toolbar">
                    <strong>导演方案版本</strong>
                    <span class="list-toolbar-tag">{{ versions.length }} 个</span>
                    <span class="list-toolbar-grow"></span>
                    <span v-if="selectedDirectorVersions.length" class="list-toolbar-count">已选 {{ selectedDirectorVersions.length }} 项</span>
                    <button class="ui-button sm" :disabled="deleting || busy || hasRunning || !selectedDirectorVersions.length" @click="askDirectorDeletion(selectedDirectorVersions)">批量删除</button>
                  </div>
                  <article v-for="version in versions" :key="Number(version.version)" class="history-row" :class="{ 'is-picked': selectedDirectorVersions.includes(Number(version.version)) }"><header><label v-if="Number(version.version) !== currentDirectorVersion" class="version-pick"><input type="checkbox" :checked="selectedDirectorVersions.includes(Number(version.version))" :disabled="deleting" @change="toggleDirectorVersion(Number(version.version))" /><span class="sr-only">选择导演方案 v{{ version.version }}</span></label><span v-else class="version-pick-spacer" aria-hidden="true"></span><div class="history-info"><strong>导演方案 v{{ version.version }}</strong><small>{{ version.created_at }}<template v-if="Number(version.version) === currentDirectorVersion"> · 当前使用中</template></small></div><button class="ui-button sm" :disabled="busy || hasRunning" @click="restoreChoice = Number(version.version)">恢复为新版本</button><button v-if="Number(version.version) !== currentDirectorVersion" class="ui-button sm danger" :disabled="deleting || busy || hasRunning" @click="askDirectorDeletion([Number(version.version)])">删除</button><span v-else class="version-locked" title="当前正在使用的版本不能删除">当前版本不可删除</span></header><details><summary>查看版本摘要</summary><AssistantMessageBlock :content="directorSummary(version, mode) || '旧版方案（只读）'" :show-mark="false" /></details><div v-if="restoreChoice === Number(version.version)" class="review-notice" role="status"><p>将 v{{ version.version }} 恢复为新版本。历史保留，当前确认状态会清除。</p><button class="ui-button primary sm" :disabled="busy || hasRunning" @click="restore(Number(version.version))">确认恢复</button><button class="ui-button quiet sm" :disabled="busy" @click="restoreChoice = null">取消</button></div></article><p v-if="!versions.length" class="pane-note">暂无已保存方案版本。</p>
                  <p v-if="deletionFeedback" class="pane-note" role="status">{{ deletionFeedback }}</p>
                  <div v-if="deletionFailures.length" class="delete-failures" role="alert"><p>{{ deletionFailures.length }} 项未完成，请刷新核实后重试：</p><ul><li v-for="failure in deletionFailures" :key="failure.label">{{ failure.label }}：{{ failure.reason }}</li></ul></div>
                  <h3>真实执行记录</h3><button v-for="item in executions" :key="item.run_id" class="history-task" @click="openDraft(item.run_id)">{{ item.director_execution_summary.mode === 'fast' ? '普通模式' : '专业模式' }} · {{ directorStateLabels[item.status] ?? item.status }}<small>{{ item.run_id }}</small></button>
                </template>
                <template v-else-if="section === 'images'">
                  <ImageOptimizeStage v-if="project && activeConversationId" :key="project.project.project_id + ':' + activeConversationId + ':' + (currentWork?.brief_version ?? 0)"
                    :project-id="project.project.project_id" :conversation-id="activeConversationId" :task="currentWork?.original_request || project.creative_brief.original_request" :brief-version="currentWork ? currentWork.brief_version ?? 0 : undefined"
                    :legacy-images="reviewImages" :initial-view="route.workspacePage"
                    :new-import="newlyImportedImage?.projectId === project.project.project_id && newlyImportedImage?.conversationId === activeConversationId ? newlyImportedImage.image : undefined"
                    @import-consumed="newlyImportedImage = null" @changed="refresh().catch(failure => refreshConnectionError = failureText(failure))" @state="imageWorkbenchState = $event" />
                </template>
                <template v-else-if="section === 'story'">
                  <ComicFlowStage stage="story" :workbench="storyWorkbench" @copy="(text: string) => void copyText(text)" @go="openSection" @repair="repairStoryImage" />
                </template>
                <template v-else-if="section === 'export'">
                  <ComicFlowStage stage="export" :workbench="storyWorkbench" @copy="(text: string) => void copyText(text)" @go="openSection" @repair="repairStoryImage" />
                </template>
                <template v-else-if="section === 'video'">
                  <VideoStage :workbench="storyWorkbench" @go="openSection" />
                </template>
              </div>
              </Transition>
            <section v-show="chatExpanded" ref="timeline" class="workspace-messages" aria-label="连续创作对话" @scroll="scrollState">
              <!-- 载入对话期间只显示占位，避免先渲染"只有用户消息"的中间态再补上 AI 回复（视觉闪烁）。 -->
              <p v-if="loading" class="conversation-loading" role="status">正在载入对话…</p>
              <template v-else>
              <p v-if="!chatTurns.length && !pendingText" class="conversation-welcome">描述你的故事与画面，生成导演方案后可修改并确认，再进入分镜准备镜头 Prompt。</p>
              <article v-for="turn in chatTurns" :key="turn.id" class="creative-turn">
                <UserMessageBubble v-if="turn.role === 'user'" :content="turn.content" />
                <template v-else><AssistantMessageBlock :content="turn.content" :show-mark="false" /><ChatImageAttachment v-if="turn.artifactId && referenceUrls[turn.artifactId]" :media="{ url: referenceUrls[turn.artifactId]!, filename: `comic-${turn.artifactId}.png` }" @open="previewReference" /><p v-if="turn.artifactId && referenceErrors[turn.artifactId]" role="alert">{{ referenceErrors[turn.artifactId] }}</p></template>
                <div v-if="turn.role === 'assistant' && turn.runId && runs[turn.runId]?.workflow !== 'comic.shot.continue' && executions.some(item => item.run_id === turn.runId && item.director_spec)" class="draft-actions"><button class="ui-button sm" @click="openDraft(turn.runId)">进入导演方案 <ArrowRight :size="15" /></button></div>
              </article>
              <UserMessageBubble v-if="pendingText && !chatTurns.some(turn => turn.role === 'user' && turn.content === pendingText)" :content="pendingText" :pending="busy ? 'replying' : 'failed'" />
              <p v-if="busy && !active" role="status">正在提交创意，等待真实执行状态…</p>
              <p v-if="active?.status === 'cancelled'" class="pane-note" role="status">任务已取消。正在进行的模型请求可能仍需结束，但不会继续下一节点或保存导演方案。</p>
              <article v-for="input in queuedInputs" :key="input.id" class="queued-turn">
                <UserMessageBubble :content="input.text" />
                <div class="queued-note"><span>补充已排队 · 当前方案结束后处理 · {{ input.mode === 'professional' ? '专业模式' : '普通模式' }}</span><button class="ui-button quiet sm" @click="queuedInputs = queuedInputs.filter(item => item.id !== input.id)">撤回</button></div>
              </article>
              </template>
              <button v-if="queuedInputs.length && error && !busy && !hasRunning" class="ui-button sm" @click="dispatchQueued(true)">继续处理已发送的补充</button>
              <p v-if="error" class="workspace-error" role="alert">{{ error }}</p>
              <p v-if="dirty" class="pane-note">有未保存的节点修改。切换节点会保留草稿；保存并重新审核后才能确认。</p>
              <div v-if="manualDirectorApproval && restoredSpec" class="draft-actions"><span>方案 v{{ restoredSpec.version }} · {{ confirmed ? '已确认' : '草稿' }}</span><button class="ui-button sm" @click="openDraft()">进入导演方案 <ArrowRight :size="15" /></button></div>
              <button v-if="manualDirectorApproval && spec && !confirmable && !stale && !dirty" class="ui-button sm" :disabled="busy || hasRunning" @click="reviewFinal">{{ spec.review_required === false ? '重新执行内部检查与修正' : '审核当前草稿' }}</button>
              <p v-if="manualDirectorApproval && confirmed" class="pane-note">当前版本已确认并保存到后端。编辑或恢复为新版本后，需要重新审核与确认。</p>
            </section>
              </div>
            </main>
        </div>

        <!-- 删除确认：三处共用同一个弹窗。删除会真实写库且不可撤销，
             所以必须先把"删哪些"逐条列出来，再让用户确认。 -->
        <dialog ref="deletionDialog" class="delete-dialog" aria-label="删除确认" @close="pendingDeletion = null">
          <h2>{{ pendingDeletion?.title }}</h2>
          <p>{{ pendingDeletion?.hint }}</p>
          <ul>
            <li v-for="item in pendingDeletion?.items ?? []" :key="`${item.id}:${item.version}`">{{ item.label }}</li>
          </ul>
          <p class="delete-dialog-note">此操作不可撤销，删除后无法从历史里找回。</p>
          <div class="delete-dialog-actions">
            <button class="ui-button" type="button" :disabled="deleting" @click="closeDeletion">取消</button>
            <button class="ui-button danger" type="button" :disabled="deleting" @click="runDeletion">{{ deleting ? '正在删除' : '确认删除' }}</button>
          </div>
        </dialog>

        <template #composer>
<div v-if="!['images', 'video'].includes(section) || chatExpanded" class="workspace-composer" :class="{ 'is-chat': chatExpanded }">
          <div class="composer-context"><button class="ui-button quiet sm" :aria-expanded="chatExpanded" @click="chatExpanded = !chatExpanded"><ArrowLeft v-if="chatExpanded" :size="15" /><MessageSquareText v-else :size="15" />{{ chatExpanded ? '返回工作区' : '打开创作对话' }}</button><small>{{ revisionVersion !== null ? `修改当前草稿 v${revisionVersion}` : '当前对话' }}</small><button v-if="revisionVersion !== null" class="ui-button quiet sm" @click="invalidate">取消修改</button><button v-else-if="spec" class="ui-button quiet sm" :disabled="busy || hasRunning || stale || dirty" @click="reviseByInstruction">修改当前方案</button></div>
          <p v-if="legacyOnly" class="pane-note">此历史任务没有作品级 Project；点击"新作品"进入作品级创作。</p>
          <div v-if="busy || hasRunning" class="execution-controls" role="status"><span>{{ executionLabel }} · 可继续输入</span><button v-if="hasRunning" class="ui-button quiet sm" :disabled="cancelling" @click="cancelExecution">{{ cancelling ? '正在取消' : '取消当前任务' }}</button></div>
          <p v-if="!chatExpanded && error" class="workspace-error" role="alert">{{ error }}</p>
          <!-- 输入框只在「对话」页出现；其他工作区页面去掉输入框，避免每个页面底部都挂一个大聊天框。 -->
          <MessageComposer v-if="chatExpanded" :key="activeConversationId" ref="composer" :disabled="loading || legacyOnly" @send="sendInput" />
          <small v-if="chatExpanded">{{ revisionVersion !== null ? '发送将保存当前方案的新修订；不会创建新创意。' : queuedInputs.length ? '补充已排队，刷新会丢失未执行补充；可在对话中撤回。' : '确认导演方案，生成完整 Prompt，复制到外部模型后上传图片。' }}</small>
        </div>
</template>
  </WorkspaceShell>

  <HistoryDrawer :open="historyDrawer" title="作品与历史对话" subtitle="WORKS" :count="historyEntries.length" @close="historyDrawer = false">
    <div class="drawer-new-row">
      <button type="button" class="ui-button primary sm" :disabled="busy || hasRunning || conversationBusy" @click="historyDrawer = false; newProject()">
        <Plus :size="14" /> 新建作品
      </button>
      <small>新建不会删除旧作品，随时可以在这里切回去。</small>
    </div>
    <ConversationHistory list-only placeholder="搜索作品或对话" :conversations="historyEntries" :active-id="activeWork?.work_id ?? activeConversationId" :busy="conversationBusy" @select="historyDrawer = false; selectHistory($event)" @rename="finishConversationRename" @remove="removeConversation" />
  </HistoryDrawer>
</template>

<style scoped>
/* 关卡外壳沿用原型的像素语言；节点与状态仍来自真实工作流。 */
:deep(.ws-shell-top) { background:#1f1f1f; color:#fffdf5; border-bottom:4px solid #000; }
:deep(.ws-shell-top .toolbar-task), :deep(.ws-shell-top .toolbar-status) { color:#d0d7e7; }
:deep(.ws-shell-top .ui-button) { border:2px solid #111; border-radius:0; background:#fffdf5; color:#141414; }
:deep(.ws-shell-top .ui-button:disabled) { background:#333; color:#c1c1c1; opacity:1; }
/* 工作区的对话入口占独立空间，不能遮住正在编辑的内容。 */
:deep(.ws-shell-body:not(.is-flow) .ws-shell-composer) { position:static; flex:none; width:auto; transform:none; }
:deep(.ws-shell-content .stage-scroll), :deep(.ws-shell-content .workspace-messages) { padding-bottom:24px; scroll-padding-bottom:24px; mask-image:none; }
.workspace-composer { padding:8px 24px; background:#101f43; color:#fffdf5; }
.workspace-composer:empty { display:none; }
.workspace-composer .composer-context { max-width:1180px; margin:0 auto; justify-content:flex-start; }
.workspace-composer .composer-context > small { margin-right:auto; color:#cbd7ed; }
.workspace-composer > small { display:block; max-width:1180px; margin:5px auto 0; color:#cbd7ed; }
.workspace-composer :deep(.message-composer) { max-width:760px; margin:8px auto; }
.workspace-composer .composer-context .ui-button { color:#fffdf5; }
.workspace-composer .composer-context .ui-button:hover:not(:disabled) { color:#141414; background:#ffe24b; }
.workspace-stage { background:linear-gradient(180deg,#16295e 0%,#0e1e46 60%,#08122b 100%); }
.trackWrap { flex:none; position:relative; padding:6px 20px 0; overflow:hidden; background-image:linear-gradient(#ffffff0d 2px,transparent 2px),linear-gradient(90deg,#ffffff0d 2px,transparent 2px); background-size:34px 34px; }
.level { display:flex; align-items:center; gap:10px; list-style:none; margin:0; padding:0 2px; overflow-x:auto; scrollbar-width:thin; }
.level > li { flex:0 0 auto; }
.step { display:flex; flex-direction:row; align-items:center; min-width:0; padding:0; border:0; background:transparent; text-align:left; cursor:pointer; }
/* 精灵图现在贴在方块里（原来单独占 24px 一行，整条关卡因此高了一截）。 */
.step .sprite { width:14px; height:17px; flex:0 0 auto; }
.step .sprite { image-rendering:pixelated; }
.step[data-status='current'] .sprite { animation:level-hop 1.5s steps(2,end) infinite; }
@keyframes level-hop { 50% { transform:translateY(-2px); } }
.step[data-status='current'] .block { animation:block-blink 1.6s steps(2,end) infinite; }
@keyframes block-blink { 50% { background:var(--gold-light); } }
.step .block { display:flex; align-items:center; gap:6px; height:34px; padding:0 9px; border:3px solid #000; background:#22304f; color:#d3ddf0; font:600 11.5px var(--font-ui); white-space:nowrap; transition:transform 120ms steps(2,end), background 120ms, box-shadow 120ms; }
.step .num { font-size:9px; opacity:.8; }
.step .coin { display:block; width:11px; height:11px; border:2px solid #000; background:#ffe24b; }
.step[data-status='done'] .block { background:#c84c0c; color:#fff0dc; box-shadow:inset 0 4px #ffffff38,4px 4px #000; }
.step[data-status='current'] .block { background:#f8c000; color:#141414; box-shadow:inset 0 4px #ffffff88,5px 5px #000; }
.step[data-status='current'] .lbl { color:#ffe24b; }
.step[data-status='todo'] .block { border-style:dashed; border-color:#52678f; background:transparent; }
.step:hover .block { transform:translateY(-2px); }
.step:focus-visible { outline:3px solid #ffe24b; outline-offset:4px; }
.goal { align-self:flex-end; padding:0 8px 4px; }
.pole { position:relative; display:block; width:5px; height:40px; border:2px solid #000; background:#dde6f5; }
.pole::after { content:''; position:absolute; left:3px; top:2px; width:18px; height:12px; border:2px solid #000; background:#00a800; clip-path:polygon(0 0,100% 30%,0 70%); }
.ground { height:12px; margin:0 -20px; border-block:4px solid #000; background:repeating-linear-gradient(90deg,#c84c0c 0 30px,#8a2c08 30px 32px); box-shadow:inset 0 4px #ffffff30; }
.panelWrap { display:flex; flex:1; min-width:0; min-height:0; padding:14px 20px 12px; }
.panelWrap > .stage-scroll, .panelWrap > .workspace-messages { min-width:0; background:#fffdf5; color:#141414; border:4px solid #141414; box-shadow:6px 6px 0 #0009; }
.workspace-work-choice { background:#fffdf5; }
.stage-scroll :deep(.director-node-view) { max-width:none; }
.stage-scroll :deep(.director-node-view .plan-index) { gap:10px; }
.stage-scroll :deep(.director-node-view .plan-entry),
.stage-scroll :deep(.director-node-view .field-card) { padding:12px 14px; border:3px solid #141414; border-radius:0; background:#fffdf5; box-shadow:4px 4px 0 #141414; }
.stage-scroll :deep(.director-node-view .plan-entry:hover) { background:#fff6d9; }
.stage-scroll :deep(.director-node-view .field-card-label) { color:#8a2c08; font-weight:700; }
.stage-scroll :deep(.director-node-view textarea) { border:2px solid #141414; border-radius:0; background:#fffdf5; }
.stage-scroll :deep(.director-node-view .critic-finding) { border-radius:0; }
.panelWrap :deep(.ui-button) { border-radius:0; }
@media (max-width:600px) { .trackWrap { padding:8px 12px 0; } .ground { margin-inline:-12px; } .panelWrap { padding:10px 12px; } .step { min-width:106px; } }
.workspace-work-choice { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-3); padding:var(--space-3) var(--space-6); border-bottom:1px solid var(--border-muted); }
.workspace-work-choice :deep(.ui-dropdown) { max-width:min(440px,100%); }
.workspace-work-choice small { font-size:var(--font-meta); color:var(--text-secondary); }
.toolbar-project { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; font-size: var(--font-control); font-weight:600; }
.toolbar-identity { display:grid; flex:1; min-width:72px; gap:2px; }
.toolbar-task { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text-secondary); font-size:var(--font-kicker); }
/* 七步制作进度条：文档流里的一条横向步骤导航。
   三态用三种“语汇”而不是三种颜色深浅：
   · 已完成 —— 实心绿点 + 数字弱化，表示“已翻篇”；
   · 当前   —— 左侧 2px 强调竖条（复用 .critic-finding 语汇）+ 浅底，表示“就是这里”；
   · 待办   —— 空心灰环，表示“还没到”。
   竖条比背景色更耐看：不抢视线，也不依赖颜色区分状态。 */
/* 七步制作流程条（单行椭圆胶囊）：
   与下方 .director-flow 同一套语汇，但承担"全流程 7 步"的导航。
   三态用三种"语汇"而非三种色深：
   · 已完成 —— 实心勾 + 弱化文字，表示"已翻篇"；
   · 当前   —— 白底滑块 + 强调色文字，表示"就是这里"；
   · 待办   —— 空心序号环，表示"还没到"。
   交互：hover 轻微抬起 + 底色渐入；当前项白底滑块带投影；点击整颗胶囊下沉 1px。 */
.flow-steps { margin:0 0 var(--space-6); }
.flow-steps-rail { display:flex; flex-wrap:nowrap; align-items:center; gap:var(--space-1); max-width:100%; padding:var(--space-1); overflow-x:auto; scrollbar-width:none; border:1px solid var(--border-muted); border-radius:var(--radius-pill); background:var(--canvas-inset); -webkit-overflow-scrolling:touch; }
.flow-steps-rail::-webkit-scrollbar { display:none; }
.flow-steps button { position:relative; display:inline-flex; align-items:center; gap:var(--space-2); flex:0 0 auto; padding:var(--space-2) var(--space-3); border:0; border-radius:var(--radius-pill); background:transparent; color:var(--text-secondary); font:inherit; text-align:left; white-space:nowrap; cursor:pointer; transition:background var(--duration-med) var(--ease-out), color var(--duration-med) var(--ease-out), box-shadow var(--duration-med) var(--ease-out), transform var(--duration-fast) var(--ease-out); }
.flow-steps button:hover { background:var(--surface-subtle); color:var(--text-primary); transform:translateY(-1px); }
.flow-steps button:active { transform:translateY(1px); }
.flow-steps button:focus-visible { outline:2px solid var(--focus-ring-color); outline-offset:2px; }
.flow-steps button[aria-current] { background:#fff; color:var(--text-primary); box-shadow:var(--shadow-control); }
.flow-steps button[aria-current]:hover { transform:none; }
.flow-steps-index { display:grid; place-items:center; flex-shrink:0; width:16px; height:16px; border:1px solid var(--border-strong); border-radius:var(--radius-pill); font-size:10px; font-weight:600; color:var(--text-muted); font-variant-numeric:tabular-nums; transition:border-color var(--duration-med) var(--ease-out), color var(--duration-med) var(--ease-out), background var(--duration-med) var(--ease-out); }
.flow-steps button[data-status="done"] .flow-steps-index { border-color:transparent; background:var(--success-soft); color:var(--success); }
.flow-steps button[aria-current] .flow-steps-index { border-color:var(--accent); color:var(--accent); }
.flow-steps-body { display:inline-flex; align-items:baseline; gap:var(--space-2); }
.flow-steps-body strong { font-size: var(--font-control); font-weight:500; }
.flow-steps-body small { display:none; font-size: var(--font-kicker); color:var(--text-muted); }
/* 只有"当前"这一步展开显示副标题，其余收成单行标题 —— 单行高度的同时保留"下一步是什么"。 */
.flow-steps button[aria-current] .flow-steps-body small { display:inline; }
.flow-steps button[data-status="done"] { color:var(--text-secondary); }
.flow-steps button[data-status="todo"] { color:var(--text-muted); }
.toolbar-mode { flex-shrink:0; }
.toolbar-status { color:var(--text-secondary); font-size: var(--font-kicker); max-width:140px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.toolbar-new { flex-shrink:0; }
select { color:var(--text-primary); background:transparent; border:1px solid var(--border-muted); border-radius:var(--radius-control); padding:var(--space-1) var(--space-2); font:inherit; cursor:pointer; }
:deep(.ws-shell-heading) > div:first-child { flex:1; min-width:0; }
:deep(.ws-shell-heading) small { font-size: var(--font-kicker); color:var(--text-muted); }
:deep(.ws-shell-heading) h2 { font-size: var(--font-section); font-weight:600; margin:var(--space-1) 0 0; }
.node-version { font-size: var(--font-kicker); color:var(--text-muted); }
.workspace-content { display:flex; flex:1; min-width:0; min-height:0; }
.workspace-stage { display:flex; flex:1; min-width:0; min-height:0; flex-direction:column; }
.stage-scroll { flex:1; min-height:0; overflow:auto; padding:28px max(28px, calc((100% - 1100px) / 2)); font-size: var(--font-body); line-height:1.7; animation:node-enter 140ms ease-out; }
.stage-scroll.image-workbench-stage { display:flex; flex-direction:column; overflow:hidden; padding:var(--space-4) var(--space-6); container: image-stage / size; }
/* 视频阶段要看的是播放器：沿用图片工作台放宽阅读宽度的做法（780 → 1180），
   否则宽屏下 4:3 播放器只剩 460px，失去「平板预览」的意义。 */
.stage-scroll.video-stage-scroll { padding-inline: max(var(--space-6), calc((100% - 1180px) / 2)); }
.stage-scroll :deep(.ui-button) { transition:transform 120ms steps(2,end), box-shadow 120ms, background 120ms; }
.stage-scroll :deep(.ui-button:active:not(:disabled)) { transform:translate(2px,2px); box-shadow:none; }
.image-flow { display:flex; align-items:center; gap:var(--space-3); margin-bottom:var(--space-3); flex-shrink:0; }
.image-flow .flow-steps-rail { flex:1; min-width:0; }
.image-flow > .ui-button { flex-shrink:0; }
/* 标题块与步骤条同层：标题固定在最左，rail 吃掉中间剩余宽度，动作按钮固定在最右。 */
.image-flow-title { display:flex; align-items:baseline; gap:var(--space-3); flex-shrink:0; min-width:0; margin-right:var(--space-2); }
.image-flow-title small { font-size:var(--font-kicker); color:var(--text-muted); }
.image-flow-title h2 { font-size:var(--font-section); font-weight:600; margin:0; white-space:nowrap; }
.image-flow-toggle { display:none !important; }.workspace-connection-notice { color:var(--warning); font-size:var(--font-meta); }
@container image-stage (max-height: 600px) {
  .image-flow .flow-steps-rail { display:none; }
  .image-flow.flow-expanded .flow-steps-rail { display:flex; }
  .image-flow .image-flow-toggle { display:inline-flex !important; }
  .image-flow { flex-wrap:wrap; margin-bottom:var(--space-2); }
}
.stage-scroll h3 { font-size: var(--font-section); font-weight:600; margin:var(--space-6) 0 var(--space-4); }
/* 窄屏：标题与步骤条挤不进一行时，标题独占一行、rail 换行占满宽度。
   容器查询只管高度，宽度降级必须走视口查询。 */
@media(max-width:1000px) {
  .image-flow { flex-wrap:wrap; }
  /* 标题与动作按钮留在第一行，只有 rail 换到第二行占满宽度——
     否则 flex-wrap 会把按钮挤到第三行，表头白白多出一层。 */
  .image-flow-title { flex:1 1 auto; margin-right:0; }
  .image-flow .flow-steps-rail { order:3; flex:1 1 100%; }
  .image-flow .image-flow-toggle { order:2; }
}
@media(max-width:600px) { .image-flow-title h2 { font-size:var(--font-control); } }
/* 页眉在 images 下不再渲染，WorkspaceShell 里隐藏 small 的规则失效，这里自行补上。 */
@media(max-width:480px) { .image-flow-title small { display:none; } }
@keyframes node-enter { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:translateY(0); } }
@media(prefers-reduced-motion:reduce) { .stage-scroll { animation:none; } }
/* 导演流程：关卡轨道的容器。节点本身的样式在 styles/mario.css（.pnode / .pnode__box）。 */
.director-flow { display:flex; flex-direction:column; gap:6px; margin:0; padding:0; border:0; background:transparent; }
.workspace-messages { flex:1; min-height:0; overflow:auto; padding:var(--space-5) max(var(--space-6), calc((100% - 780px) / 2)); }
.creative-turn { margin-bottom:var(--space-7); }
.conversation-welcome { color:var(--text-secondary); font-size: var(--font-body); line-height:1.8; max-width:600px; padding:var(--space-8) 0; }
.conversation-loading { color:var(--text-muted); font-size: var(--font-control); padding:var(--space-8) 0; }
.workspace-composer { background:#101f43; }
.composer-context { display:flex; align-items:center; justify-content:space-between; gap:var(--space-2); min-height:var(--control-sm); margin-bottom:var(--space-2); }
.composer-context small, .workspace-composer > small { color:#cbd7ed; font-size: var(--font-kicker); }
.workspace-composer > small { display:block; margin-top:var(--space-2); text-align:center; }
.execution-controls, .queued-note { display:flex; align-items:center; justify-content:space-between; gap:var(--space-2); color:var(--text-secondary); font-size: var(--font-meta); }
.execution-controls { margin-bottom:var(--space-2); }
.queued-note { justify-content:flex-end; margin:var(--space-2) 0 var(--space-4); }
.draft-actions { display:flex; gap:var(--space-2); align-items:center; flex-wrap:wrap; margin:var(--space-4) 0; font-size: var(--font-meta); }
.draft-actions span { display:flex; gap:var(--space-1); align-items:center; color:var(--text-secondary); }
.final-approval { margin-top:var(--space-6); padding:var(--space-5) 0; }
.final-approval > p { color:var(--text-secondary); font-size: var(--font-control); margin:0 0 var(--space-3); }
.final-approval .draft-actions { margin:0; }
.asset-group, .history-row { padding-bottom:var(--space-4); margin-bottom:var(--space-5); border-bottom:1px solid var(--border-muted); }
/* 分镜：每个镜头是一张独立卡片。此前所有信息挤在一条底边线上，
   长文本没有换行余量，读起来像一堵墙。 */
.board-bar { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); flex-wrap:wrap; margin:var(--space-1) 0 var(--space-4); }
.board-picker { display:flex; align-items:center; gap:var(--space-2); font-size:var(--font-meta); color:var(--text-secondary); }
.board-picker select { max-width:320px; }
.board-count { font-size:var(--font-meta); color:var(--text-muted); }
/* 选中态从左描边（会被误读为"出错"）改为左侧一道短色条：既标记位置，又不抢内容。 */
.shot-card { position:relative; padding:var(--space-5); margin-bottom:var(--space-4); background:var(--surface); border:1px solid var(--border-muted); border-radius:var(--radius-panel); transition: border-color var(--duration-med) var(--ease-out), box-shadow var(--duration-med) var(--ease-out), transform var(--duration-med) var(--ease-out); }
.shot-card:hover { transform:translateY(-1px); box-shadow:var(--shadow-card); }
.shot-card.is-selected { border-color:var(--border-muted); box-shadow:var(--shadow-card); }
.shot-card.is-selected::before { content:''; position:absolute; left:0; top:var(--space-4); bottom:var(--space-4); width:3px; border-radius:0 var(--radius-tile) var(--radius-tile) 0; background:var(--accent); }
.shot-head { display:flex; align-items:flex-start; justify-content:space-between; gap:var(--space-4); }
.shot-select { flex:1; min-width:0; display:grid; gap:var(--space-1); padding:0; background:transparent; border:0; text-align:left; cursor:pointer; transition:transform var(--duration-fast) var(--ease-out); }
.shot-select:active { transform:translateY(1px); }
.shot-index { font-size:var(--font-kicker); color:var(--text-muted); letter-spacing:.02em; }
.shot-subject { font-size:var(--font-body); font-weight:var(--weight-medium); color:var(--text-primary); line-height:var(--line-tight); overflow-wrap:anywhere; }
.shot-select:hover .shot-subject { color:var(--accent); }
.shot-meta { flex:none; font-size:var(--font-meta); color:var(--text-muted); white-space:nowrap; }
.shot-brief { display:grid; gap:var(--space-2); margin:var(--space-4) 0 0; padding-top:var(--space-4); border-top:1px solid var(--border-muted); }
.shot-brief > div { display:grid; grid-template-columns:3.5em minmax(0, 1fr); gap:var(--space-3); align-items:start; }
.shot-brief dt { font-size:var(--font-meta); color:var(--text-muted); line-height:var(--line-body); }
.shot-brief dd { margin:0; font-size:var(--font-body); color:var(--text-secondary); line-height:var(--line-body); overflow-wrap:anywhere; }
.shot-footer { display:flex; align-items:center; justify-content:space-between; gap:var(--space-4); flex-wrap:wrap; margin-top:var(--space-6); padding-top:var(--space-5); border-top:1px solid var(--border); }
.shot-footer p { margin:0; font-size:var(--font-meta); color:var(--text-secondary); overflow-wrap:anywhere; }
.prompt-preview { border:1px solid var(--border); border-radius:var(--radius-panel); padding:0; margin:var(--space-4) 0; overflow:hidden; background:var(--surface); }
.prompt-preview header { display:flex; justify-content:space-between; align-items:center; gap:var(--space-3); padding:var(--space-4) var(--space-5); border-bottom:1px solid var(--border-muted); background:var(--surface-subtle); }
.prompt-preview header strong { font-size:var(--font-body); font-weight:var(--weight-medium); }
.prompt-preview > pre { margin:0; padding:var(--space-5); }
.prompt-references { padding:var(--space-4) var(--space-5); background:var(--surface-subtle); }
.prompt-references article { max-width:640px; margin-top:var(--space-3); }
.prompt-references article > strong { display:block; margin-bottom:var(--space-2); overflow-wrap:anywhere; }
.prompt-references .draft-actions { padding-inline:0; }
.prompt-preview .draft-actions { margin:0; padding:var(--space-4) var(--space-5); border-top:1px solid var(--border-muted); }
.prompt-preview details { padding:0 var(--space-5) var(--space-5); }
.prompt-preview pre, .prompt-text { white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; max-height:420px; overflow:auto; line-height:1.7; }
.prompt-editor { display:grid; }
.prompt-editor label { display:grid; gap:var(--space-2); }
.asset-group h3 { margin-top:0; }
.history-row header { display:flex; gap:var(--space-3); align-items:center; justify-content:space-between; }
.history-row small { display:block; color:var(--text-muted); font-size: var(--font-kicker); margin-top:var(--space-1); }
.history-task { display:block; background:transparent; color:var(--text-primary); border:0; border-radius:var(--radius-control); padding:var(--space-3) var(--space-2); margin:var(--space-1) 0; width:100%; text-align:left; cursor:pointer; transition:background var(--duration-fast) var(--ease-standard), transform var(--duration-fast) var(--ease-out); }
.history-task:hover { background:var(--surface-subtle); }
.history-task:active { transform:translateY(1px); }
.history-task small { display:block; color:var(--text-muted); margin-top:var(--space-1); overflow-wrap:anywhere; }
.review-notice { color:var(--text-secondary); border-left:2px solid var(--accent); padding:var(--space-2) var(--space-4); margin:var(--space-4) 0; }
/* 历史版本：过期不等于没用，给出真实原因、保存时间与唯一的恢复入口。 */
.stale-plan { padding:var(--space-4) var(--space-5); margin:var(--space-4) 0; background:var(--warning-soft); border:1px solid var(--warning-border); border-radius:var(--radius-control); }
.stale-plan-head { display:flex; align-items:center; justify-content:space-between; gap:var(--space-4); flex-wrap:wrap; }
.stale-plan-head strong { display:block; font-size:var(--font-body); font-weight:var(--weight-medium); color:var(--text-primary); }
.stale-plan-head small { display:block; margin-top:var(--space-1); font-size:var(--font-meta); color:var(--text-secondary); }
.stale-plan > p { margin:var(--space-3) 0 0; font-size:var(--font-meta); color:var(--text-secondary); line-height:var(--line-body); }
/* 正在看历史版本（UX-010）：与 stale-plan 同构，但用信息色区分
   —— "只读历史"是信息态，"来源已变化"才是警告态。 */
.current-plan { padding:var(--space-4) var(--space-5); margin:var(--space-4) 0; background:var(--info-soft); border:1px solid var(--info-border); border-radius:var(--radius-control); }
.current-plan .stale-plan-head strong { color:var(--info); }
.current-plan > p { margin:var(--space-3) 0 0; font-size:var(--font-meta); color:var(--text-secondary); line-height:var(--line-body); }
/* 交付页（UX-011）：当前镜头图片是页面主角，给它一块独立的、有分量但不喧哗的底。 */
.delivery-hero { padding:var(--space-5); margin-bottom:var(--space-5); border:1px solid var(--border); border-radius:var(--radius-panel); background:var(--surface); box-shadow:var(--shadow-card); }
.delivery-head { display:flex; align-items:flex-start; justify-content:space-between; gap:var(--space-4); flex-wrap:wrap; margin-bottom:var(--space-4); }
.delivery-head h3 { margin:0; font-size:var(--font-section); font-weight:var(--weight-semibold); color:var(--text-primary); }
.delivery-head small { display:block; margin-top:var(--space-1); font-size:var(--font-meta); color:var(--text-secondary); }
.delivery-hero .draft-actions { margin-top:var(--space-4); }
.delivery-hero .pane-note:last-child { margin-top:var(--space-3); padding-top:var(--space-3); border-top:1px solid var(--border-muted); }
.delivery-row { padding:var(--space-3) 0; border-bottom:1px solid var(--border-muted); }
.delivery-row:last-child { border-bottom:0; }
.delivery-row > p { margin:0 0 var(--space-2); font-size:var(--font-meta); color:var(--text-secondary); }
/* 可折叠资产组：summary 做成一行可点的标题，全空时收缩成一行，不再占据版面。 */
.asset-group.collapsible { padding-bottom:var(--space-3); }
.asset-group.collapsible > summary { display:flex; align-items:center; gap:var(--space-2); padding:var(--space-2) 0; cursor:pointer; font-size:var(--font-body); font-weight:var(--weight-medium); color:var(--text-primary); transition:color var(--duration-fast) var(--ease-standard); }
.asset-group.collapsible > summary:hover { color:var(--accent); }
.asset-group.collapsible > summary small { color:var(--text-muted); font-size:var(--font-kicker); font-weight:var(--weight-regular); }
.workspace-error { color:var(--danger); overflow-wrap:anywhere; margin:var(--space-2) 0; }
button:focus-visible, select:focus-visible, textarea:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }

/* 面板出现/消失：高度由布局自然撑开，这里只负责"淡入 + 轻微上移"，
   避免元素瞬间弹出造成的生硬感。 */
.panel-collapse-enter-active { transition: opacity var(--duration-med) var(--ease-out), transform var(--duration-med) var(--ease-out); }
.panel-collapse-leave-active { transition: opacity var(--duration-fast) var(--ease-out), transform var(--duration-fast) var(--ease-out); }
.panel-collapse-enter-from, .panel-collapse-leave-to { opacity:0; transform:translateY(-4px); }

/* 对话 ↔ 工作区切换：受 v-show 语义约束，只做单侧淡入上移。 */
.ws-cross-enter-active { transition: opacity var(--duration-med) var(--ease-out), transform var(--duration-med) var(--ease-out); }
.ws-cross-leave-active { transition: opacity var(--duration-fast) var(--ease-out); pointer-events:none; }
.ws-cross-enter-from { opacity:0; transform:translateY(6px); }
.ws-cross-leave-to { opacity:0; transform:translateY(-6px); }

/* 排队中的补充输入：出现时淡入上移，让"已排队"这一状态被看见。 */
.queued-turn { animation: queued-in var(--duration-med) var(--ease-out); }
@keyframes queued-in { from { opacity:0; transform:translateY(-4px); } to { opacity:1; transform:none; } }
@media(prefers-reduced-motion:reduce) {
  .panel-collapse-enter-active, .panel-collapse-leave-active,
  .ws-cross-enter-active, .ws-cross-leave-active { transition:none; }
  .queued-turn { animation:none; }
}
/* ---------------- 列表管理条：资产 / Prompt 版本 / 历史版本三处共用 ----------------
   与全局资产库页同一套观感：一条浅边框 + 微阴影的横条承载"全选信息 + 批量删除"，
   不新造控件，也不给页面再加一层视觉重量。 */
.list-toolbar { display:flex; align-items:center; gap:var(--space-2); padding:var(--space-2) var(--space-4); margin:0 0 var(--space-4); border:1px solid var(--border-muted); border-radius:var(--radius-panel); background:var(--surface); box-shadow:var(--shadow-card); }
.list-toolbar strong { font-size:var(--font-control); font-weight:var(--weight-medium); }
.list-toolbar-tag { padding:var(--space-1) var(--space-2); border-radius:var(--radius-pill); background:var(--canvas-inset); color:var(--text-secondary); font-size:var(--font-kicker); }
.list-toolbar-grow { flex:1; }
.list-toolbar-count { color:var(--text-secondary); font-size:var(--font-meta); }

.delete-failures { padding:var(--space-3) var(--space-4); margin:var(--space-3) 0; border:1px solid var(--danger-border); border-radius:var(--radius-control); background:var(--danger-soft); color:var(--danger); font-size:var(--font-meta); }
.delete-failures p { margin:0; }
.delete-failures ul { margin:var(--space-2) 0 0 var(--space-4); }

/* Prompt 资产库：镜头用标签而不是下拉框——项目里有几个镜头、各自到第几版，一眼可见。 */
.shot-chips { display:flex; flex-wrap:wrap; gap:var(--space-2); margin:0 0 var(--space-4); }
.shot-chip { display:inline-flex; align-items:baseline; gap:var(--space-2); padding:var(--space-2) var(--space-3); border:1px solid var(--border); border-radius:var(--radius-pill); background:var(--surface); color:var(--text-secondary); font:inherit; font-size:var(--font-meta); cursor:pointer; box-shadow:var(--shadow-control); transition: border-color var(--duration-fast) var(--ease-standard), background var(--duration-fast) var(--ease-standard), color var(--duration-fast) var(--ease-standard), transform var(--duration-fast) var(--ease-out); }
.shot-chip:hover { border-color:var(--border-hover); color:var(--text-primary); }
.shot-chip:active { transform:translateY(1px); }
.shot-chip small { color:var(--text-muted); font-size:var(--font-kicker); }
.shot-chip.is-active { border-color:var(--accent-ring); background:var(--accent-soft); color:var(--accent); font-weight:var(--weight-medium); }
.shot-chip.is-active small { color:var(--accent); }

/* Prompt 版本卡片：版本号升为主标题，来源与状态降为小标签，正文默认收起。 */
.version-card { padding:var(--space-4) var(--space-5); margin-bottom:var(--space-3); border:1px solid var(--border-muted); border-radius:var(--radius-panel); background:var(--surface); box-shadow:var(--shadow-card); transition: border-color var(--duration-med) var(--ease-standard), box-shadow var(--duration-med) var(--ease-standard); }
.version-card.is-current { border-color:var(--border); }
.version-card.is-picked { border-color:var(--accent-ring); box-shadow:0 0 0 2px var(--accent-ring), var(--shadow-card); }
.version-head { display:flex; align-items:flex-start; gap:var(--space-3); }
.version-pick, .version-pick-spacer { flex:none; display:grid; place-items:center; width:16px; height:16px; margin-top:var(--space-1); }
.version-pick input { width:15px; height:15px; accent-color:var(--accent); cursor:pointer; }
.version-info { flex:1; min-width:0; }
.version-title { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2); }
.version-title strong { font-size:var(--font-body); font-weight:var(--weight-medium); }
.version-tag { padding:var(--space-1) var(--space-2); border-radius:var(--radius-pill); background:var(--canvas-inset); color:var(--text-secondary); font-size:var(--font-kicker); }
.version-tag.is-current { background:var(--accent-soft); color:var(--accent); }
.version-meta { display:block; margin-top:var(--space-2); color:var(--text-muted); font-size:var(--font-meta); line-height:var(--line-body); overflow-wrap:anywhere; }
.version-prompt { margin-top:var(--space-3); }
.version-prompt > summary { padding:var(--space-2) var(--space-3); border:1px solid var(--border-muted); border-radius:var(--radius-control); background:var(--surface-subtle); color:var(--text-secondary); font-size:var(--font-meta); cursor:pointer; }
.version-prompt > pre { margin:var(--space-2) 0 0; padding:var(--space-3) var(--space-4); border-radius:var(--radius-control); background:var(--canvas-inset); }
.version-actions { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-2); margin-top:var(--space-4); padding-top:var(--space-3); border-top:1px solid var(--border-muted); }
.version-locked { color:var(--text-muted); font-size:var(--font-kicker); }

/* 资产条目：勾选与删除放在条目内部，展开详情的行为保持不变。 */
.asset-item { padding:var(--space-3) var(--space-4); margin-top:var(--space-2); border:1px solid var(--border-muted); border-radius:var(--radius-control); background:var(--surface); transition: border-color var(--duration-fast) var(--ease-standard), box-shadow var(--duration-fast) var(--ease-standard); }
.asset-item.is-picked { border-color:var(--accent-ring); box-shadow:0 0 0 2px var(--accent-ring); }
.asset-actions { display:flex; align-items:center; gap:var(--space-2); margin-top:var(--space-3); padding-top:var(--space-3); border-top:1px solid var(--border-muted); }
.asset-pick { display:inline-flex; align-items:center; gap:var(--space-2); color:var(--text-secondary); font-size:var(--font-meta); cursor:pointer; }
.asset-pick input { width:15px; height:15px; accent-color:var(--accent); cursor:pointer; }

/* 历史版本行：补上勾选列后仍然是"一行一个版本"的信息结构。 */
.history-row.is-picked { border-color:var(--accent-ring); box-shadow:0 0 0 2px var(--accent-ring); }
.history-row header { display:flex; align-items:flex-start; gap:var(--space-3); }
.history-info { flex:1; min-width:0; display:grid; gap:var(--space-1); }

/* 删除确认弹窗：沿用全局资产库页的 dialog 视觉。 */
.delete-dialog { width:min(460px, calc(100vw - 32px)); max-height:calc(100dvh - 48px); overflow:auto; padding:var(--space-6); border:1px solid var(--border); border-radius:var(--radius-dialog); color:var(--text-primary); background:var(--surface); box-shadow:var(--shadow-pop); }
.delete-dialog::backdrop { background:rgb(23 26 31 / .45); }
.delete-dialog h2 { margin:0; font-size:var(--font-section); font-weight:var(--weight-semibold); }
.delete-dialog > p { margin:var(--space-2) 0 0; color:var(--text-secondary); font-size:var(--font-meta); line-height:var(--line-body); }
.delete-dialog ul { margin:var(--space-2) 0 0 var(--space-4); color:var(--text-secondary); font-size:var(--font-meta); line-height:1.9; overflow-wrap:anywhere; }
.delete-dialog .delete-dialog-note { margin:var(--space-3) 0 0; padding-top:var(--space-3); border-top:1px solid var(--border-muted); color:var(--text-muted); font-size:var(--font-kicker); }
.delete-dialog-actions { display:flex; justify-content:flex-end; gap:var(--space-2); margin-top:var(--space-5); }

@media(max-width:1200px) { .toolbar-policy { display:none; } .toolbar-mode :deep(.ui-dropdown-label) { display:none; } }
@media(max-width:900px) { .toolbar-status { display:none; } .stage-scroll { padding-inline:var(--space-6); } }
@media(max-width:600px) { .toolbar-mode :deep(.ui-dropdown-label), .toolbar-new, .composer-context > small, .node-version { display:none; } .stage-scroll, .workspace-messages { padding-inline:var(--space-4); } .director-flow { gap:var(--space-1); } .director-flow button { padding:var(--space-2); } }

/* ===== 关卡舞台：深色底 + 只压一层纸卡（元件细节见 styles/mario.css） ===== */
.stage-scroll.director-stage-scroll { padding:22px clamp(18px, 2vw, 34px) 32px; }

.pipeline-caption { display:flex; align-items:center; gap:12px; margin:0 0 16px; color:#c2d3f3; font:700 12px var(--font-mono); letter-spacing:.08em; }
.pipeline-caption::after { content:''; flex:1; height:3px; background:repeating-linear-gradient(90deg,#3d557f 0 6px,transparent 6px 12px); }

/* 展开体里的字段：左标签右取值，两列铺开，像真表单而不是一排大卡片 */
.pnode__body :deep(.field-cards) { display:grid; grid-template-columns:1fr 1fr; gap:6px; }
.pnode__body :deep(.field-card) { display:grid; grid-template-columns:96px minmax(0, 1fr); gap:2px 10px; align-items:start; margin:0; padding:6px 10px 7px; border:3px solid var(--ink); background:var(--paper); box-shadow:3px 3px 0 rgba(0,0,0,.3); }
.pnode__body :deep(.field-card-label) { display:flex; flex-direction:column; gap:2px; min-width:0; color:var(--ink); font-size:11px; font-weight:800; line-height:1.3; }
.pnode__body :deep(.field-card-content) { margin:0; color:var(--text-primary); font-size:12px; line-height:1.6; overflow-wrap:anywhere; }
.pnode__body :deep(.field-card textarea) { grid-column:2; width:100%; border:3px solid var(--ink); background:var(--paper); box-shadow:inset 0 -3px 0 var(--paper-3); }
.pnode__body :deep(.located-field) { border-color:var(--danger); background:var(--danger-soft); box-shadow:3px 3px 0 rgba(142,19,19,.25); }
.pnode__body :deep(.plan-index) { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
.pnode__body :deep(.plan-entry) { display:flex; flex-direction:column; gap:4px; padding:10px 12px; border:3px solid var(--ink); background:var(--paper); box-shadow:3px 3px 0 rgba(0,0,0,.3); text-align:left; }
.pnode__body :deep(.critic-finding) { margin:0 0 8px; padding:10px 12px; border:3px solid var(--danger); background:var(--danger-soft); }
.pnode__body :deep(.critic-finding p) { margin:4px 0; }

/* 节点内的收尾操作行：一条虚线把"改这一段"和正文分开 */
.pnode__acts { display:flex; align-items:center; flex-wrap:wrap; gap:10px; margin-top:2px; padding-top:8px; border-top:3px dashed var(--paper-3); }
.pnode__acts small { margin-left:auto; color:var(--text-muted); font:400 10px/1.4 var(--font-mono); letter-spacing:.06em; }

.audit-note { margin:10px 0 0; padding-top:8px; border-top:2px dashed var(--paper-3); color:var(--text-muted); font:400 10.5px/1.6 var(--font-mono); letter-spacing:.04em; }

@media(max-width:900px) { .pnode__body :deep(.field-cards) { grid-template-columns:1fr; } .pnode__body :deep(.plan-index) { grid-template-columns:1fr; } }

/* 历史版本提示：纸卡内的一条横幅，靠左侧色带区分，不再层层加框 */
.stale-plan, .current-plan { margin:0; padding:14px 16px; border:3px solid var(--ink); border-left:10px solid var(--brick); background:var(--warning-soft); color:var(--ink); }
.current-plan { border-left-color:var(--blue); background:var(--info-soft); }
.stale-plan-head { display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; gap:10px; }
.stale-plan-head strong { display:block; font-size:15px; }
.stale-plan-head small { display:block; margin-top:4px; color:var(--text-muted); font-size:12px; }
.stale-plan p, .current-plan p { margin:10px 0 0; color:var(--text-secondary); font-size:13px; line-height:1.7; }

.review-notice { margin:0; padding:10px 14px; border:2px solid var(--ink); border-left:8px solid var(--gold); background:var(--accent-soft); color:var(--text-secondary); font-size:13px; line-height:1.65; }

/* 最终确认条：整行贯通，主操作只有一颗金砖 */
.final-approval { grid-column:1 / -1; display:flex; flex-wrap:wrap; align-items:center; gap:14px; margin:0; padding:14px 16px; border:4px solid var(--ink); background:var(--paper-2); color:var(--ink); box-shadow:4px 4px 0 rgba(0,0,0,.45); }
.final-approval p { flex:1 1 260px; margin:0; color:var(--text-secondary); font-size:13px; line-height:1.6; }
.final-approval .draft-actions { display:flex; flex-wrap:wrap; gap:8px; margin:0; }

/* 后续阶段（生图 / 图片 / 故事 / 视频）沿用同一套：纸卡 + 墨线 + 硬投影 */
.production-stage-heading { display:flex; justify-content:space-between; align-items:end; flex-wrap:wrap; gap:12px; margin-bottom:20px; padding:18px 20px; border:4px solid var(--ink); background:var(--ink-2); color:var(--paper); box-shadow:5px 5px 0 rgba(0,0,0,.55); }
.production-stage-heading span { color:var(--gold-light); font:700 11px var(--font-mono); letter-spacing:.14em; }
.production-stage-heading h2 { margin:6px 0; font-size:23px; }
.production-stage-heading p { margin:0; color:#d9e5f9; font-size:13px; line-height:1.6; }
.production-stage-heading .production-stage-count { align-self:center; padding:6px 10px 5px; border:2px solid var(--gold); color:var(--gold-light); font:700 11px var(--font-mono); letter-spacing:.1em; }

.prompt-stage-scroll .board-bar { margin:0 0 18px; padding:12px 16px; border:3px solid var(--ink); background:var(--paper-2); }
.prompt-stage-scroll .shot-card { margin:0 0 20px; padding:0; overflow:hidden; border:4px solid var(--ink); background:var(--paper); box-shadow:5px 5px 0 rgba(0,0,0,.55); }
.prompt-stage-scroll .shot-card.is-selected { box-shadow:5px 5px 0 var(--gold); }
.prompt-stage-scroll .shot-head { padding:12px 18px; background:var(--ink-2); color:var(--paper); }
.prompt-stage-scroll .shot-head :is(.shot-index,.shot-subject,.shot-meta) { color:inherit; }
.prompt-stage-scroll .shot-brief { padding:18px 20px; margin:0; }
.prompt-stage-scroll .shot-generation { margin:0 18px 18px; padding:16px; border:3px solid var(--ink); background:var(--paper-2); }
.prompt-stage-scroll .shot-footer { padding:16px 18px; border:4px solid var(--ink); background:var(--paper); box-shadow:5px 5px 0 rgba(0,0,0,.55); }
.prompt-stage-scroll > .pane-note, .prompt-stage-scroll > .review-notice { padding:14px 16px; border:3px solid var(--ink); background:var(--paper); color:var(--text-primary); }
.stage-scroll :deep(.shot-card) { margin:0 0 20px; padding:20px; border:4px solid var(--ink); background:var(--paper); box-shadow:5px 5px 0 var(--paper-3); }
.stage-scroll :deep(.shot-card.is-selected) { box-shadow:5px 5px 0 var(--gold); }
.stage-scroll :deep(.shot-generation) { margin-top:16px; padding:16px; border:3px solid var(--ink); background:var(--paper-2); }
.stage-scroll :deep(.shot-generation .draft-actions) { display:flex; flex-wrap:wrap; gap:8px; }
.stage-scroll :deep(.shot-brief dd) { overflow-wrap:anywhere; line-height:1.75; }

.image-workbench-stage { background:#0f1f3d !important; color:var(--paper) !important; }
.video-stage-scroll { color:var(--paper) !important; }

/* 对话回合：用户是金砖，导演是纸卡 */
.workspace-messages :deep(.chat-row.user) { justify-content:flex-end; }
.workspace-messages :deep(.user-bubble) { max-width:min(88%, 660px); padding:14px 16px; border:3px solid var(--ink); border-radius:0; background:var(--gold-light); color:var(--ink); box-shadow:4px 4px 0 var(--ink); }
.workspace-messages :deep(.chat-row.assistant) { align-items:flex-start; }
.workspace-messages :deep(.assistant-copy) { width:min(100%, 720px); max-width:100%; padding:18px 20px; border:3px solid var(--ink); background:var(--paper); color:var(--ink); box-shadow:4px 4px 0 var(--ink); }
.workspace-messages .creative-turn { margin-bottom:22px; }

@media(max-width:900px) {
  .dir-grid { grid-template-columns: minmax(0, 1fr); }
}
@media(max-width:600px) {
  .panelWrap > .stage-scroll.director-stage-scroll, .panelWrap > .stage-scroll.prompt-stage-scroll, .panelWrap > .stage-scroll.flow-stage-scroll, .panelWrap > .stage-scroll.video-stage-scroll { padding:12px; }
  .workspace-composer { padding:8px 12px; }
  .workspace-composer .composer-context { flex-wrap:wrap; }
}
@media(prefers-reduced-motion:reduce) {
  .step .block, .stage-scroll :deep(.ui-button) { transition:none; }
  .step:hover .block { transform:none; }
  .step[data-status='current'] .sprite { animation:none; }
}


/* 节点级折叠：收起即完全移除该节点面板，只留「导演流程」导航；标题旁给一条恢复提示。 */
.pipeline-caption-hint { padding:2px 8px; border:2px solid var(--gold); background:rgba(0,0,0,.28); color:var(--gold-light); font-size:11px; letter-spacing:.04em; }
</style>
