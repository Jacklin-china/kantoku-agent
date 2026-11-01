<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { History, MessageSquareText, Workflow } from 'lucide-vue-next'
import WorkspaceShell from '../components/workspace/WorkspaceShell.vue'
import HistoryDrawer from '../components/layout/HistoryDrawer.vue'
import { conversationTaskTitle } from '../components/chat/conversationPresentation'
import ApprovalCard from '../components/approval/ApprovalCard.vue'
import ChatMessageList from '../components/chat/ChatMessageList.vue'
import ConversationHistory from '../components/chat/ConversationHistory.vue'
import MessageComposer from '../components/chat/MessageComposer.vue'
import { visibleFastDomain, type FastDomain } from '../components/chat/fastDomainSelection'
import SystemStatusInline from '../components/chat/SystemStatusInline.vue'
import { domains } from '../domains'
import { presenterFor } from '../domains/presenters'
import { navigate, route } from '../router'
import { createConversation, decideApproval, decideMediaCost, deleteConversation, getApprovals, getArtifact, getArtifactContentUrl, getArtifacts, getConversation, getConversations, getEvents, getRun, getTaskImageUrl, renameConversation, setConversationFastDomain, streamConversationMessage, subscribeRunEvents, type RuntimeEvent } from '../services/core'
import type { Conversation, ConversationMessage, CoreApproval, CoreArtifact, CoreRun, MediaJob } from '../types'

const props = withDefaults(defineProps<{ runs?: CoreRun[]; approvals?: CoreApproval[]; busy?: boolean; domain?: string; embedded?: boolean; initialRunId?: string; studioFocus?: boolean; historySignal?: number }>(), { runs: () => [], approvals: () => [], busy: false, embedded: false, studioFocus: false })
const emit = defineEmits<{ refresh: []; runCreated: [run: CoreRun]; chatting: [active: boolean]; selectStage: [nodeId: string] }>()
const conversations = ref<Conversation[]>([])
const historyOpen = ref(false)
const navOpen = ref(!window.matchMedia('(max-width: 600px)').matches)
/* 桌面端图标条折叠态：与 navOpen（抽屉显隐）独立，跨页共用同一 key 保持状态一致。
   localStorage 在 SSR / 单元测试环境下不存在，必须容错，否则 setup 阶段直接抛错。 */
const navCollapsedKey = 'kantoku-ws-nav-collapsed'
function readNavCollapsed(): boolean {
  try { return localStorage.getItem(navCollapsedKey) === '1' } catch { return false }
}
const navCollapsed = ref(readNavCollapsed())
function toggleNavCollapsed(value: boolean): void {
  navCollapsed.value = value
  try { localStorage.setItem(navCollapsedKey, value ? '1' : '0') } catch { /* 忽略写入失败 */ }
}
const creatingConversation = ref(false)
/* 「最近对话 / 本次产物」是按需抽屉，不再常驻右栏。
   常驻栏会永久吃掉 258–340px：1080 宽窗口下对话被挤成一条缝，而它多数时候是空的。
   抽屉默认收起，对话独占整行；顶栏按钮带着条数，需要翻历史时再滑出。 */
const recentOpen = ref(false)
/* 左栏「历史」也开这一格：内嵌在其他领域里时不开，避免和工作区自己的抽屉打架。 */
watch(() => props.historySignal, (value, previous) => {
  if (value && value !== previous && !props.domain) recentOpen.value = true
})
/* 「去别的世界」已并入左侧常驻导航，右栏不再重复三个入口。 */
/* 第二层侧栏 = 当前领域的工作节点；文案来自 Domain Presenter，首页不显示。 */
const navigation = computed(() => [
  { id: 'conversation', label: '对话', icon: MessageSquareText },
  ...(props.domain ? presenterFor(props.domain).workflow.map((node) => ({ id: node.id, label: node.label, icon: Workflow })) : []),
])
const activeWorkspacePage = computed(() => selectedWorkspaceNode.value || 'conversation')
const conversationTitle = computed(() => conversations.value.find(item => item.id === conversationId.value)?.title ?? '新对话')
const shellIdentity = computed(() => props.domain ? domainLabel(props.domain).name : 'Kantoku')
const shellSubtitle = computed(() => props.domain ? domainLabel(props.domain).label : '对话工作区')
function domainLabel(domain: string): { name: string; label: string } {
  const found = domains.find((item) => item.id === domain)
  return { name: found?.name ?? domain, label: found?.label ?? '' }
}
const conversationId = ref('')
const messages = ref<ConversationMessage[]>([])
/* 会话内容是否还在读取：首屏闪一下空白引导就是它没被区分出来。 */
const conversationLoading = ref(true)
const mediaJobs = ref<MediaJob[]>([])
const streaming = ref('')
const sending = ref(false)
const error = ref('')
const activeRun = ref<CoreRun | null>(null)
const domainHint = ref<string | null>(props.domain ?? null)
const fastDomain = ref<FastDomain | null>(null)
const fastDomainTaskId = ref<string | null>(null)
const switchingFastDomain = ref(false)
const dispatchingFastDomain = ref<Record<string, boolean>>({})
const consumedFastDomains = new Set<string>()
const pendingFastDispatches = new Map<string, Promise<void>>()
const dataMode = ref<'demo' | 'production'>('production')
const enhancePrompt = ref(true)
const composer = ref<{ fill: (text: string) => void } | null>(null)
const activities = ref<RuntimeEvent[]>([])
const localApprovals = ref<CoreApproval[]>([])
const lastContent = ref('')
const imageUrl = ref('')
interface InlineRunState { run: CoreRun; activities: RuntimeEvent[]; approval: CoreApproval | null; artifact: CoreArtifact | null; imageUrl: string; videoUrl: string }
interface QueuedMessage { id: string; conversationId: string; content: string; domainHint: string | null; title?: string; onBound?: () => void }
const inlineRuns = ref<Record<string, InlineRunState>>({})
const pendingMessages = ref<Record<string, 'queued' | 'replying' | 'failed'>>({})
const activityByMessage = ref<Record<string, string>>({})
const publicActivities = ref<Record<string, { kind: string; label: string; detail: string }[]>>({})
const messageMedia = ref<Record<string, { type: 'image' | 'video'; url: string; filename: string }>>({})
const messageMediaErrors = ref<Record<string, boolean>>({})
const imagePhases = ref<Record<string, { status: 'prepared' | 'generating' | 'ready' | 'summarized' | 'failed'; width: number; height: number }>>({})
const streamingByMessage = ref<Record<string, string>>({})
const errorMessageId = ref('')
const deciding = ref(false)
const homeStops = new Map<string, () => void>()
const runAnchors = new Map<string, string>()
const loadingMedia = new Set<string>()
const unavailableMedia = new Set<string>()
let localMessageCounter = 0
let stopEvents: (() => void) | null = null
let lastSequence = 0
let imageLoadVersion = 0
let mediaPollTimer: ReturnType<typeof setInterval> | null = null
let selectionVersion = 0
const pendingApproval = computed(() => [...props.approvals, ...localApprovals.value].find((item) => item.run_id === activeRun.value?.id && item.decision === 'pending') ?? null)

async function refreshConversations(): Promise<void> {
  const list = await getConversations(props.domain)
  conversations.value = list.filter((item) => item.domain !== 'commerce').filter((item) => props.domain
    ? item.execution_mode === 'professional' && item.domain === props.domain
    : item.execution_mode === 'fast')
}

function syncFastDomain(detail: Conversation): void {
  if (props.domain || detail.id !== conversationId.value) return
  if (!detail.domain) consumedFastDomains.delete(detail.id)
  fastDomain.value = visibleFastDomain(detail.domain, detail.fast_domain_task_id, consumedFastDomains.has(detail.id))
  fastDomainTaskId.value = detail.fast_domain_task_id
  domainHint.value = fastDomain.value
}

async function refreshFastDomain(ownerId: string): Promise<void> {
  const detail = await getConversation(ownerId).catch(() => null)
  if (detail) syncFastDomain(detail)
}

async function selectConversation(id: string): Promise<void> {
  const currentSelection = ++selectionVersion
  conversationId.value = id
  if (!props.domain) {
    localStorage.setItem('kantoku-home-active-conversation', id)
    navigate({ name: 'home', conversationId: id }, true)
  }
  /* 清空到拉回之间会有一帧空列表，此时不能显示"从零开始"的引导，
     否则用户会看到一块空白引导闪一下再被消息顶掉。 */
  conversationLoading.value = true
  messages.value = []
  activeRun.value = null
  error.value = ''; errorMessageId.value = ''; activities.value = []; streaming.value = ''
  fastDomain.value = null; domainHint.value = props.domain ?? null; fastDomainTaskId.value = null
  composer.value?.fill('')
  if (mediaPollTimer) { clearInterval(mediaPollTimer); mediaPollTimer = null }
  stopEvents?.(); stopEvents = null
  for (const stop of homeStops.values()) stop()
  for (const state of Object.values(inlineRuns.value)) {
    if (state.imageUrl) URL.revokeObjectURL(state.imageUrl)
    if (state.videoUrl) URL.revokeObjectURL(state.videoUrl)
  }
  for (const media of Object.values(messageMedia.value)) URL.revokeObjectURL(media.url)
  messageMedia.value = {}
  messageMediaErrors.value = {}
  imagePhases.value = {}
  mediaJobs.value = []
  activityByMessage.value = {}
  publicActivities.value = {}
  streamingByMessage.value = {}
  homeStops.clear(); runAnchors.clear(); inlineRuns.value = {}
  const detail = await getConversation(id)
  if (!detail || currentSelection !== selectionVersion) {
    if (currentSelection === selectionVersion) conversationLoading.value = false
    return
  }
  conversationId.value = id
  syncFastDomain(detail)
  domainHint.value = props.domain ?? fastDomain.value
  messages.value = detail.messages ?? []
  conversationLoading.value = false
  restoreMediaJobs(detail.media_jobs ?? [])
  if (mediaJobs.value.some((job) => (job.status === 'pending' || job.status === 'generating') && job.approval_status !== 'pending')) {
    startMediaPolling(id)
  }
  for (const message of messages.value) if (message.artifact_id) void loadMessageMedia(message, id)
  streaming.value = ''
  error.value = ''
  errorMessageId.value = ''
  activities.value = []
  const restoredRun = detail.active_run_id ? await getRun(detail.active_run_id) : null
  if (currentSelection !== selectionVersion) return
  activeRun.value = restoredRun
  if (!props.domain) {
    const runIds = [...new Set(messages.value.map((message) => message.run_id).filter((runId): runId is string => !!runId))]
    for (const runId of runIds.slice(-10)) {
      if (runId === restoredRun?.id) continue
      const run = await getRun(runId)
      if (currentSelection !== selectionVersion) return
      const anchor = [...messages.value].reverse().find((message) => message.run_id === runId)?.id
      if (run && anchor) followHomeRun(run, anchor)
    }
  }
  if (activeRun.value) {
    if (props.domain) followRun(activeRun.value.id)
    else {
      const anchor = [...messages.value].reverse().find((item) => item.run_id === activeRun.value?.id)?.id
        ?? [...messages.value].reverse().find((item) => item.role === 'assistant')?.id
        ?? messages.value.at(-1)?.id
      if (anchor) followHomeRun(activeRun.value, anchor)
    }
  }
}

function startMediaPolling(ownerId: string): void {
  if (!mediaPollTimer) mediaPollTimer = setInterval(() => { void refreshActiveMedia(ownerId) }, 1200)
}

function restoreMediaJobs(jobs: MediaJob[]): void {
  mediaJobs.value = jobs
  const phases = { ...imagePhases.value }
  for (const job of jobs) {
    if (job.media_type !== 'image') continue
    phases[job.generation_request_id] = {
      status: job.status === 'completed' ? 'ready'
        : job.status === 'failed' ? 'failed'
          : job.status === 'generating' ? 'generating' : 'prepared',
      width: phases[job.generation_request_id]?.width ?? 1,
      height: phases[job.generation_request_id]?.height ?? 1,
    }
  }
  imagePhases.value = phases
}

async function refreshActiveMedia(ownerId: string): Promise<void> {
  try {
    const detail = await getConversation(ownerId)
    if (!detail || conversationId.value !== ownerId) return
    syncFastDomain(detail)
    if (error.value === '连接中断，正在恢复图片任务状态。') error.value = ''
    restoreMediaJobs(detail.media_jobs ?? [])
    const persisted = detail.messages ?? []
    const pendingLocal = messages.value.filter((message) =>
      message.id.startsWith('local-') && pendingMessages.value[message.id]
      && !persisted.some((item) => item.event_id === `generation-user:${message.id}`),
    )
    messages.value = [...persisted, ...pendingLocal]
    for (const message of persisted) {
      if (message.artifact_id && !messageMedia.value[message.id] && !messageMediaErrors.value[message.id]) {
        void loadMessageMedia(message, ownerId)
      }
    }
    if (!mediaJobs.value.some((job) => (job.status === 'pending' || job.status === 'generating') && job.approval_status !== 'pending') && mediaPollTimer) {
      clearInterval(mediaPollTimer)
      mediaPollTimer = null
    }
  } catch (pollError) {
    if (conversationId.value === ownerId && !error.value) error.value = pollError instanceof Error ? pollError.message : '无法更新图片状态'
  }
}

async function loadMessageMedia(message: ConversationMessage, ownerId: string): Promise<void> {
  if (!message.artifact_id) return
  const loadingKey = `conversation:${message.id}`
  if (loadingMedia.has(loadingKey) || messageMedia.value[message.id]) return
  loadingMedia.add(loadingKey)
  try {
    const [artifact, url] = await Promise.all([
      getArtifact(message.artifact_id).catch(() => null),
      getArtifactContentUrl(message.artifact_id).catch(() => null),
    ])
    if (!url) {
      if (conversationId.value === ownerId) messageMediaErrors.value = { ...messageMediaErrors.value, [message.id]: true }
      return
    }
    if (conversationId.value !== ownerId || artifact?.conversation_id !== ownerId ||
        !['image', 'video'].includes(artifact.type)) {
      URL.revokeObjectURL(url)
      return
    }
    messageMedia.value = {
      ...messageMedia.value,
      [message.id]: {
        type: artifact.type as 'image' | 'video', url,
        filename: `kantoku-${artifact.id}.${artifact.location?.match(/\.(png|jpe?g|webp)$/i)?.[1]?.toLowerCase() ?? 'png'}`,
      },
    }
  } finally {
    loadingMedia.delete(loadingKey)
  }
}

const selectedWorkspaceNode = ref('')
/** 点击第二层节点：交给领域 Workspace 打开对应阶段页面。 */
function selectWorkspaceNode(id: string): void {
  selectedWorkspaceNode.value = id === 'conversation' ? '' : id
  emit('selectStage', id)
}

async function newConversation(): Promise<void> {
  if (creatingConversation.value) return
  creatingConversation.value = true
  try {
    const created = await createConversation(props.domain ? 'guided' : 'autonomous', props.domain)
    await selectConversation(created.id)
    await refreshConversations()
  } catch (failure) { error.value = failure instanceof Error ? failure.message : '新建对话失败' }
  finally { creatingConversation.value = false }
}

async function selectFastDomain(domain: FastDomain | null): Promise<void> {
  if (props.domain || switchingFastDomain.value || pendingFastDispatches.has(conversationId.value)) return
  if (!conversationId.value) await initialize()
  if (!conversationId.value || domain === fastDomain.value) return
  switchingFastDomain.value = true
  try {
    const updated = await setConversationFastDomain(conversationId.value, domain)
    consumedFastDomains.delete(conversationId.value)
    fastDomain.value = updated.domain as FastDomain | null
    domainHint.value = fastDomain.value
    error.value = ''
    await refreshConversations()
  } catch (taskError) {
    error.value = taskError instanceof Error ? taskError.message : '快捷模式切换失败'
  } finally { switchingFastDomain.value = false }
}

async function renameChat(id: string, title: string): Promise<void> {
  await renameConversation(id, title)
  await refreshConversations()
}

async function removeChat(id: string): Promise<void> {
  if (!window.confirm('只删除这条聊天记录？关联的任务、产物和费用记录会保留。')) return
  await deleteConversation(id)
  await refreshConversations()
  if (id === conversationId.value) {
    if (conversations.value[0]) await selectConversation(conversations.value[0].id)
    else await newConversation()
  }
}

async function initialize(): Promise<void> {
  try {
    await refreshConversations()
    const storedId = route.value.conversationId ?? localStorage.getItem('kantoku-home-active-conversation')
    const conversation = (!props.domain ? conversations.value.find(item => item.id === storedId) : null)
      ?? conversations.value.find((item) => !!props.initialRunId && item.active_run_id === props.initialRunId) ?? conversations.value[0]
    if (conversation) await selectConversation(conversation.id)
    else await newConversation()
    if (props.initialRunId && activeRun.value?.id !== props.initialRunId) {
      activeRun.value = await getRun(props.initialRunId)
      if (activeRun.value) {
        if (props.domain) followRun(activeRun.value.id)
        else {
          const anchor = messages.value.at(-1)?.id
          if (anchor) followHomeRun(activeRun.value, anchor)
        }
      }
    }
  } catch (taskError) {
    error.value = taskError instanceof Error ? taskError.message : '无法载入对话'
  }
}

function updateInline(runId: string, patch: Partial<InlineRunState>): void {
  const anchor = runAnchors.get(runId)
  if (!anchor || !inlineRuns.value[anchor]) return
  inlineRuns.value = { ...inlineRuns.value, [anchor]: { ...inlineRuns.value[anchor], ...patch } }
}

async function refreshInlineRun(runId: string, includeDetails = true): Promise<void> {
  const [run, approvals, artifacts] = await Promise.all([
    getRun(runId).catch(() => null),
    includeDetails ? getApprovals(`home-approvals:${runId}`).catch(() => null) : Promise.resolve(null),
    includeDetails ? getArtifacts(runId).catch(() => null) : Promise.resolve(null),
  ])
  if (run) {
    activeRun.value = run
    updateInline(runId, { run })
    if (['completed', 'failed', 'cancelled'].includes(run.status) && conversationId.value) {
      void refreshFastDomain(conversationId.value)
    }
  }
  const artifact = artifacts?.find((item) => item.type === 'image' || item.type === 'video') ?? null
  const patch: Partial<InlineRunState> = {}
  if (approvals) patch.approval = approvals.find((item) => item.run_id === runId && item.decision === 'pending') ?? null
  if (artifacts) patch.artifact = artifact
  updateInline(runId, patch)
  const mediaKey = `${runId}:${artifact?.id ?? ''}`
  const current = inlineRuns.value[runAnchors.get(runId) ?? '']
  if (artifact && !loadingMedia.has(mediaKey) && !unavailableMedia.has(mediaKey) && !(artifact.type === 'image' ? current?.imageUrl : current?.videoUrl)) {
    loadingMedia.add(mediaKey)
    const media = await getArtifactContentUrl(artifact.id).catch(() => null)
    if (media && inlineRuns.value[runAnchors.get(runId) ?? '']) {
      updateInline(runId, artifact.type === 'image' ? { imageUrl: media } : { videoUrl: media })
    } else if (media) URL.revokeObjectURL(media)
    else unavailableMedia.add(mediaKey)
    loadingMedia.delete(mediaKey)
  }
  const state = run?.state ?? inlineRuns.value[runAnchors.get(runId) ?? '']?.run.state
  const requestId = state?.request_id
  const currentImage = inlineRuns.value[runAnchors.get(runId) ?? '']?.imageUrl
  if (!currentImage && typeof requestId === 'string' && typeof state?.image_path === 'string' && state.image_path) {
    const url = await getTaskImageUrl(requestId).catch(() => null)
    const anchor = runAnchors.get(runId)
    if (url && anchor && inlineRuns.value[anchor]) {
      const old = inlineRuns.value[anchor].imageUrl
      updateInline(runId, { imageUrl: url })
      if (old) URL.revokeObjectURL(old)
    } else if (url) URL.revokeObjectURL(url)
  }
}

function followHomeRun(run: CoreRun, anchor: string): void {
  homeStops.get(run.id)?.()
  runAnchors.set(run.id, anchor)
  inlineRuns.value = {
    ...inlineRuns.value,
    [anchor]: inlineRuns.value[anchor] ?? { run, activities: [], approval: null, artifact: null, imageUrl: '', videoUrl: '' },
  }
  void getEvents(run.id).then((events) => {
    const current = inlineRuns.value[runAnchors.get(run.id) ?? '']
    if (!current) return
    const merged = new Map([...events, ...current.activities].map((event) => [event.sequence, event]))
    updateInline(run.id, { activities: [...merged.values()].sort((a, b) => a.sequence - b.sequence) })
  }).catch(() => {})
  void refreshInlineRun(run.id).catch(() => {})
  let lastSeen = 0
  const stop = subscribeRunEvents(run.id, 0, {
    onEvent: (event) => {
      if (event.sequence <= lastSeen) return
      lastSeen = event.sequence
      const message = event.payload.message as ConversationMessage | undefined
      if (message?.conversation_id === conversationId.value && !messages.value.some(item => item.id === message.id)) {
        messages.value.push(message)
      }
      const current = inlineRuns.value[runAnchors.get(run.id) ?? '']
      if (current) updateInline(run.id, { activities: [...current.activities.filter((item) => item.sequence !== event.sequence), event].sort((a, b) => a.sequence - b.sequence) })
      void refreshInlineRun(run.id, ['approval_required', 'approval_resolved', 'artifact_created', 'run_completed', 'run_failed', 'run_cancelled'].includes(event.event_type)).catch(() => {})
      emit('refresh')
    },
    onEnd: () => { homeStops.get(run.id)?.(); homeStops.delete(run.id); void refreshInlineRun(run.id).catch(() => {}); if (conversationId.value) void refreshFastDomain(conversationId.value) },
  })
  homeStops.set(run.id, stop)
}

async function runHomeMessage(task: QueuedMessage): Promise<void> {
  pendingMessages.value = { ...pendingMessages.value, [task.id]: 'replying' }
  let anchor = task.id
  let persistedUserMessageId: string | null = null
  let bound = false
  try {
    if (task.title) await renameConversation(task.conversationId, task.title)
    await streamConversationMessage(task.conversationId, task.content, task.domainHint, {
        onIntent: (plan) => {
          persistedUserMessageId = plan.user_message_id ?? null
          bound = true
          task.onBound?.()
          void refreshFastDomain(task.conversationId)
          if (plan.tool === 'workspace.open' && plan.domain === 'comic' && conversationId.value === task.conversationId) {
            navigate({ name: 'workspace', domain: 'comic', workspacePage: 'director',
              directorStage: 'creative_understanding', conversationId: task.conversationId, creationMode: 'professional' })
          }
        },
        onDelta: (delta) => {
          if (conversationId.value === task.conversationId) {
            streamingByMessage.value = {
              ...streamingByMessage.value,
              [task.id]: (streamingByMessage.value[task.id] ?? '') + delta,
            }
          }
        },
        onMessage: (message) => {
          if (conversationId.value !== task.conversationId) return
          const index = messages.value.findIndex((item) => item.id === anchor)
          if (!messages.value.some((item) => item.id === message.id)) {
            messages.value.splice(index < 0 ? messages.value.length : index + 1, 0, message)
          }
          if (message.artifact_id) void loadMessageMedia(message, task.conversationId)
          const nextActivity = { ...activityByMessage.value }
          if (message.role === 'assistant') delete nextActivity[task.id]
          activityByMessage.value = nextActivity
          for (const [runId, id] of runAnchors) {
            if (id !== anchor) continue
            runAnchors.set(runId, message.id)
            inlineRuns.value = { ...inlineRuns.value, [message.id]: inlineRuns.value[anchor] }
            const copy = { ...inlineRuns.value }; delete copy[anchor]; inlineRuns.value = copy
          }
          anchor = message.id
          const nextStreaming = { ...streamingByMessage.value }
          delete nextStreaming[task.id]
          streamingByMessage.value = nextStreaming
          void refreshConversations()
        },
        onRun: (run) => {
          if (conversationId.value === task.conversationId) {
            activeRun.value = run
            followHomeRun(run, anchor)
            emit('refresh'); emit('runCreated', run)
          }
        },
        onActivity: (activity) => {
          if (conversationId.value === task.conversationId && activity.generation_request_id === task.id) {
            activityByMessage.value = { ...activityByMessage.value, [task.id]: activity.label }
          }
        },
        onPublicActivity: (activity) => {
          if (conversationId.value !== task.conversationId) return
          const previous = publicActivities.value[task.id] ?? []
          const next = [...previous, activity]
          publicActivities.value = {
            ...publicActivities.value,
            [task.id]: next,
            ...(persistedUserMessageId ? { [persistedUserMessageId]: next } : {}),
          }
        },
        onImageEvent: (name, event) => {
          if (conversationId.value !== task.conversationId) return
          if (name === 'cost_approval') {
            void refreshActiveMedia(task.conversationId)
            return
          }
          if (name === 'image_generating') startMediaPolling(task.conversationId)
          if (event.generation_request_id !== task.id) return
          if (name === 'prompt_prepared' || name === 'image_generating') {
            imagePhases.value = { ...imagePhases.value, [task.id]: {
              status: name === 'prompt_prepared' ? 'prepared' : 'generating',
              width: event.width ?? imagePhases.value[task.id]?.width ?? 1,
              height: event.height ?? imagePhases.value[task.id]?.height ?? 1,
            } }
          } else if (name === 'image_ready' || name === 'image_summary' || name === 'image_failed') {
            const previous = imagePhases.value[task.id] ?? { width: 1, height: 1 }
            imagePhases.value = { ...imagePhases.value, [task.id]: {
              ...previous,
              status: name === 'image_failed' ? 'failed' : name === 'image_summary' ? 'summarized' : 'ready',
            } }
          }
        },
      }, task.domainHint === 'commerce' ? 'demo' : dataMode.value, enhancePrompt.value, task.id, {
        selected_domain: task.domainHint, execution_mode: task.domainHint ? 'fast' : 'normal',
      })
    const next = { ...pendingMessages.value }; delete next[task.id]; pendingMessages.value = next
  } catch (taskError) {
    const imageStarted = imagePhases.value[task.id]?.status === 'prepared' || imagePhases.value[task.id]?.status === 'generating'
    if (conversationId.value === task.conversationId) {
      error.value = imageStarted ? '连接中断，正在恢复图片任务状态。' : taskError instanceof Error ? taskError.message : '消息没有发送成功'
      errorMessageId.value = task.id
    }
    pendingMessages.value = { ...pendingMessages.value, [task.id]: imageStarted ? 'replying' : 'failed' }
    if (imageStarted) startMediaPolling(task.conversationId)
    const nextActivity = { ...activityByMessage.value }; delete nextActivity[task.id]; activityByMessage.value = nextActivity
  } finally {
    if (!bound && task.domainHint) {
      const detail = await getConversation(task.conversationId).catch(() => null)
      if (detail?.domain === task.domainHint && !detail.fast_domain_task_id) {
        await setConversationFastDomain(task.conversationId, null).catch(() => null)
      }
    }
    task.onBound?.()
    const nextStreaming = { ...streamingByMessage.value }
    delete nextStreaming[task.id]
    streamingByMessage.value = nextStreaming
    if (conversationId.value === task.conversationId) {
      void refreshConversations()
      void refreshFastDomain(task.conversationId)
      void refreshActiveMedia(task.conversationId)
    }
  }
}

async function send(content: string): Promise<void> {
  if (!props.domain) {
    if (!conversationId.value) await initialize()
    if (!conversationId.value) return
    const ownerId = conversationId.value
    const current = conversations.value.find(item => item.id === ownerId)
    let taskTitle: string | undefined
    if (current?.title === '新对话') {
      taskTitle = conversationTaskTitle(content)
      current.title = taskTitle
    }
    const selectedDomain = fastDomain.value
    const earlierDispatch = pendingFastDispatches.get(ownerId)
    const id = `local-${Date.now()}-${++localMessageCounter}`
    messages.value.push({ id, conversation_id: ownerId, role: 'user', type: 'text', content, run_id: null, event_id: null, created_at: new Date().toISOString() })
    pendingMessages.value = { ...pendingMessages.value, [id]: earlierDispatch ? 'queued' : 'replying' }
    let onBound: (() => void) | undefined
    if (selectedDomain) {
      consumedFastDomains.add(ownerId)
      fastDomain.value = null
      domainHint.value = null
      let release!: () => void
      const gate = new Promise<void>((resolve) => { release = resolve })
      pendingFastDispatches.set(ownerId, gate)
      dispatchingFastDomain.value = { ...dispatchingFastDomain.value, [ownerId]: true }
      onBound = () => {
        if (pendingFastDispatches.get(ownerId) !== gate) return
        pendingFastDispatches.delete(ownerId)
        const next = { ...dispatchingFastDomain.value }; delete next[ownerId]; dispatchingFastDomain.value = next
        release()
      }
    }
    if (earlierDispatch) {
      try {
        await earlierDispatch
        const detail = await getConversation(ownerId)
        if (!detail) throw new Error('无法确认上一条快捷任务状态，请稍后重试')
        if (detail.domain && !detail.fast_domain_task_id && consumedFastDomains.has(ownerId)) {
          await setConversationFastDomain(ownerId, null)
        }
      } catch (taskError) {
        if (conversationId.value === ownerId) {
          error.value = taskError instanceof Error ? taskError.message : '无法确认上一条快捷任务状态'
          errorMessageId.value = id
        }
        pendingMessages.value = { ...pendingMessages.value, [id]: 'failed' }
        return
      }
    }
    void runHomeMessage({ id, content, conversationId: ownerId, domainHint: selectedDomain, title: taskTitle, onBound })
    return
  }
  if (sending.value) return
  if (!conversationId.value) await initialize()
  lastContent.value = content
  error.value = ''
  sending.value = true
  messages.value.push({ id: `local-${Date.now()}`, conversation_id: conversationId.value, role: 'user', type: 'text', content, run_id: null, event_id: null, created_at: new Date().toISOString() })
  streaming.value = ''
  try {
    await streamConversationMessage(conversationId.value, content, domainHint.value, {
      onDelta: (delta) => { streaming.value += delta },
      onMessage: (message) => { messages.value.push(message); streaming.value = ''; void refreshConversations() },
      onRun: (run) => { activeRun.value = run; followRun(run.id); emit('refresh'); emit('runCreated', run) },
    }, dataMode.value, enhancePrompt.value)
  } catch (taskError) {
    error.value = taskError instanceof Error ? taskError.message : '消息没有发送成功'
  } finally { sending.value = false }
}

function followRun(runId: string): void {
  stopEvents?.()
  lastSequence = 0
  void getEvents(runId).then((events) => { activities.value = events })
  void getApprovals().then((items) => { localApprovals.value = items ?? [] })
  stopEvents = subscribeRunEvents(runId, 0, {
    onEvent: (event) => {
      if (event.sequence <= lastSequence) return
      lastSequence = event.sequence
      activities.value = [...activities.value.filter((item) => item.sequence !== event.sequence), event].sort((a, b) => a.sequence - b.sequence)
      void getRun(runId).then((run) => { if (run) activeRun.value = run })
      if (event.event_type === 'approval_required' || event.event_type === 'approval_resolved') void getApprovals().then((items) => { localApprovals.value = items ?? [] })
      emit('refresh')
    },
    onEnd: () => { stopEvents?.(); stopEvents = null; emit('refresh') },
  })
}

async function decide(action: 'approve' | 'reject' | 'revise', response: Record<string, unknown>): Promise<void> {
  if (!pendingApproval.value) return
  sending.value = true
  try {
    activeRun.value = await decideApproval(pendingApproval.value.id, action, response)
    localApprovals.value = await getApprovals() ?? []
    if (activeRun.value) followRun(activeRun.value.id)
    emit('refresh')
  } catch (taskError) {
    error.value = taskError instanceof Error ? taskError.message : '审批没有提交成功'
  } finally { sending.value = false }
}

async function decideInline(messageId: string, action: 'approve' | 'reject' | 'revise', response: Record<string, unknown>): Promise<void> {
  const approval = inlineRuns.value[messageId]?.approval
  if (!approval || deciding.value) return
  deciding.value = true
  try {
    const run = await decideApproval(approval.id, action, response)
    updateInline(run.id, { run, approval: null })
    followHomeRun(run, messageId)
    emit('refresh')
  } catch (taskError) {
    error.value = taskError instanceof Error ? taskError.message : '确认没有提交成功'
    errorMessageId.value = messageId
  } finally { deciding.value = false }
}

async function decideMedia(requestId: string, decision: 'approve' | 'reject'): Promise<void> {
  if (!conversationId.value || deciding.value) return
  deciding.value = true
  try {
    await decideMediaCost(conversationId.value, requestId, decision)
    await refreshActiveMedia(conversationId.value)
    if (decision === 'approve') startMediaPolling(conversationId.value)
    else await refreshFastDomain(conversationId.value)
  } catch (taskError) {
    error.value = taskError instanceof Error ? taskError.message : '费用确认失败'
  } finally { deciding.value = false }
}

function openRun(run: CoreRun): void { navigate({ name: 'workspace_run', domain: run.domain, runId: run.id }) }
onMounted(() => {
  void initialize()
})
onBeforeUnmount(() => {
  if (mediaPollTimer) clearInterval(mediaPollTimer)
  stopEvents?.()
  for (const stop of homeStops.values()) stop()
  for (const state of Object.values(inlineRuns.value)) {
    if (state.imageUrl) URL.revokeObjectURL(state.imageUrl)
    if (state.videoUrl) URL.revokeObjectURL(state.videoUrl)
  }
  for (const media of Object.values(messageMedia.value)) URL.revokeObjectURL(media.url)
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
})
watch(() => props.domain, (value) => { domainHint.value = value ?? null })
watch(
  () => [activeRun.value?.id, activeRun.value?.state?.request_id, activeRun.value?.state?.image_path] as const,
  async ([, requestId, imagePath]) => {
    const version = ++imageLoadVersion
    if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
    imageUrl.value = ''
    if (typeof requestId !== 'string' || typeof imagePath !== 'string' || !imagePath) return
    const loaded = await getTaskImageUrl(requestId).catch(() => null)
    if (version !== imageLoadVersion) { if (loaded) URL.revokeObjectURL(loaded); return }
    imageUrl.value = loaded ?? ''
  },
)
watch(
  () => [messages.value.length, activeRun.value?.id] as const,
  ([count, runId]) => emit('chatting', count > 0 && !runId),
)

/* ---------- 首页右栏：本次对话产物（真实 Artifact） ----------
   只取当前对话的图片产物，最多 3 张缩略图；拿不到内容地址就不占位，
   不生成占位图，也不回填假名称。 */
interface ConversationThumb { id: string; url: string; label: string; version: number }
const conversationThumbs = ref<ConversationThumb[]>([])
const thumbsLoading = ref(false)
let thumbsEpoch = 0

async function loadConversationThumbs(): Promise<void> {
  const owner = conversationId.value
  const epoch = ++thumbsEpoch
  if (!owner) { conversationThumbs.value = []; return }
  thumbsLoading.value = true
  try {
    const all = await getArtifacts()
    if (epoch !== thumbsEpoch) return
    const mine = (all ?? [])
      .filter((item) => item.conversation_id === owner && item.type === 'image')
      .sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
      .slice(0, 3)
    const resolved = await Promise.all(mine.map(async (item) => {
      const url = await getArtifactContentUrl(item.id).catch(() => null)
      return url ? { id: item.id, url, label: `v${item.version} 图片`, version: item.version } : null
    }))
    if (epoch !== thumbsEpoch) return
    conversationThumbs.value = resolved.filter((item): item is ConversationThumb => item !== null)
  } catch {
    if (epoch === thumbsEpoch) conversationThumbs.value = []
  } finally {
    if (epoch === thumbsEpoch) thumbsLoading.value = false
  }
}

watch(conversationId, () => { void loadConversationThumbs() }, { immediate: true })

/* 正在生成的媒体任务：只报后端给的状态，不推算百分比。 */
const runningJobs = computed(() =>
  mediaJobs.value.filter((job) => job.status === 'pending' || job.status === 'generating'),
)

/* 本轮费用：只用 run 上已有的 cost_fen，拿不到就显示"未产生"，不折算不确定的账。 */
const runCostLabel = computed(() => {
  const fen = activeRun.value?.cost_fen
  if (typeof fen !== 'number' || fen <= 0) return '未产生'
  return `¥${(fen / 100).toFixed(2)}`
})

const executionModeLabel = computed(() =>
  conversations.value.find((item) => item.id === conversationId.value)?.execution_mode === 'professional' ? '专业' : '快捷',
)
</script>

<template>
  <!-- ============================================================
       首页 · 对话即主界面（原型 .homeGrid）
       左：标题 + 对话流 + 问号砖块输入区
       右：常驻「最近对话 / 正在生成 / 本次对话产物 / 去别的世界 / 状态」
       右栏的每一项都来自真实接口：会话列表、media_jobs、artifacts、run.cost_fen。
       ============================================================ -->
  <div v-if="!domain" class="home-page" :class="{ 'no-recent': !recentOpen }">
    <div class="home-grid">
      <div class="home-stream-col">
        <div class="home-stream-hd">
          <div>
            <span class="home-kick">AUTONOMOUS CONVERSATION · 本机</span>
            <h1>和 Kantoku 一起工作</h1>
          </div>
          <div class="home-tools">
            <button class="home-tbtn is-lite" type="button" :disabled="creatingConversation" @click="newConversation">+ 新建对话</button>
            <button class="home-tbtn home-tbtn--history" type="button" :aria-expanded="recentOpen" :aria-pressed="recentOpen" @click="recentOpen = !recentOpen"><History :size="13" /> 最近对话<b v-if="conversations.length">{{ conversations.length }}</b></button>
          </div>
        </div>

        <div class="home-stream">
          <ChatMessageList :messages="messages" :loading="conversationLoading" :media-jobs="mediaJobs" :streaming="streaming" :streaming-by-message="streamingByMessage" :run="activeRun" :activities="activities" :public-activities="publicActivities" :image-url="imageUrl" :inline-runs="inlineRuns" :pending-messages="pendingMessages" :activity-by-message="activityByMessage" :image-phases="imagePhases" :message-media="messageMedia" :message-media-errors="messageMediaErrors" :error-message-id="errorMessageId" home-mode :error="error" :approval-busy="deciding" @retry="lastContent && send(lastContent)" @open-run="openRun" @decide-inline="decideInline" @decide-media="decideMedia" />
        </div>

        <div class="home-composer">
          <MessageComposer :key="conversationId" ref="composer" fast-domains :fast-domain="fastDomain" :fast-domain-busy="switchingFastDomain || !!dispatchingFastDomain[conversationId]" @select-fast-domain="selectFastDomain" @send="send" />
          <p class="home-chint">
            <span v-if="fastDomain === 'comic'">自动完成分析与生图，图片会保留在当前对话。</span>
            <span v-else-if="fastDomain">快捷模式会自动处理；只有费用或必要审核才会请你决定。</span>
            <span v-else>直接提问或描述创意，结果会保留在当前对话。</span>
            <span class="sp" />
            <span class="home-chint-key">ENTER 发送</span>
          </p>
        </div>
      </div>

      <HistoryDrawer :open="recentOpen" title="最近对话" subtitle="RECENT" :count="conversations.length" @close="recentOpen = false">
        <div class="home-recent-list drawer-history">
          <ConversationHistory list-only :conversations="conversations" :active-id="conversationId" :busy="creatingConversation" @select="selectConversation" @rename="renameChat" @remove="removeChat" />
        </div>

        <div v-if="runningJobs.length" class="home-block">
          <div class="home-block-hd is-deep">
            <span class="lbl">正在生成</span>
            <span class="n">{{ runningJobs.length }}</span>
          </div>
          <div class="home-block-bd">
            <div v-for="job in runningJobs" :key="job.generation_request_id" class="home-job">
              <div class="home-job-top">
                <strong>{{ job.media_type === 'video' ? '视频' : '图片' }}任务</strong>
                <span class="home-tag is-warn">{{ job.status === 'pending' ? '等待排期' : '生成中' }}</span>
              </div>
              <div class="home-prog"><i /></div>
              <small>
                {{ job.status === 'pending' ? '排队中' : '进行中' }}
                <template v-if="typeof job.estimate_fen === 'number'"> · 预估 ¥{{ (job.estimate_fen / 100).toFixed(2) }}</template>
              </small>
            </div>
          </div>
        </div>

        <div class="home-block">
          <div class="home-block-hd is-deep">
            <span class="lbl">本次对话产物</span>
            <span class="n">{{ conversationThumbs.length }} 件</span>
          </div>
          <div v-if="conversationThumbs.length" class="home-thumbs">
            <figure v-for="thumb in conversationThumbs" :key="thumb.id">
              <img :src="thumb.url" :alt="thumb.label" loading="lazy" />
              <figcaption>{{ thumb.label }}</figcaption>
            </figure>
          </div>
          <p v-else class="home-empty">{{ thumbsLoading ? '正在读取产物…' : '这次对话还没有图片产物。' }}</p>
        </div>

        <div class="home-recent-foot">
          <div class="kv"><span>执行模式</span><b>{{ executionModeLabel }}</b></div>
          <div class="kv"><span>本轮费用</span><b>{{ runCostLabel }}</b></div>
        </div>
      </HistoryDrawer>
    </div>
  </div>

  <!-- ============================================================
       领域工作区形态：沿用 WorkspaceShell（二级导航 + 舞台 + 输入）
       ============================================================ -->
  <WorkspaceShell
    v-else
    class="home-workspace"
    :navigation="navigation"
    :active-page="activeWorkspacePage"
    v-model:navigation-open="navOpen"
    :navigation-collapsed="navCollapsed"
    :hide-navigation="!domain"
    :flow-composer="!domain"
    :creating-conversation="creatingConversation"
    :identity="shellIdentity"
    :subtitle="shellSubtitle"
    :workspace-label="domain ? `${shellIdentity} 工作台` : '首页对话工作台'"
    new-conversation-label="新建对话"
    hide-heading
    @update:navigation-collapsed="toggleNavCollapsed"
    @new-conversation="newConversation"
    @select-page="selectWorkspaceNode"
  >
    <template #toolbar>
      <strong class="home-conversation-title">{{ conversationTitle }}</strong>
      <slot name="workspace-toolbar" />
    </template>
    <template #stage><slot name="workspace-stage" /></template>
    <template #recent><ConversationHistory list-only :conversations="conversations" :active-id="conversationId" :busy="creatingConversation" @select="selectConversation" @rename="renameChat" @remove="removeChat" /></template>
    <template #composer>
      <div v-if="domain" class="composer-options"><label class="enhance-toggle"><input v-model="enhancePrompt" type="checkbox" />AI 优化提示词</label><small>先优化提示词，再进入专业制作流程</small></div>
      <MessageComposer v-if="!domain" :key="conversationId" ref="composer" fast-domains :fast-domain="fastDomain" :fast-domain-busy="switchingFastDomain || !!dispatchingFastDomain[conversationId]" @select-fast-domain="selectFastDomain" @send="send" />
      <MessageComposer v-else :key="conversationId" ref="composer" :disabled="sending" @send="send" />
      <p class="home-composer-hint">{{ domain ? '描述目标，监督酱会逐步确认细节与结果。' : fastDomain === 'comic' ? '自动完成分析与生图，图片会保留在当前对话。' : fastDomain ? '快捷模式会自动处理；只有费用或必要审核才会请你决定。' : '直接提问或描述创意，结果会保留在当前对话。' }}</p>
    </template>
    <div class="chat-home" :class="{ embedded, 'fast-chat': !domain }">
    <header v-if="domain" class="chat-home-head">
      <div v-if="studioFocus"><h1>AI 导演</h1></div>
      <div v-else><span class="section-kicker">{{ domain ? `${domain.toUpperCase()} WORKSPACE` : 'AUTONOMOUS CONVERSATION' }}</span><h1>{{ domain ? '与 Kantoku 协作' : '和 Kantoku 一起工作' }}</h1><p>{{ domain ? '描述目标，逐步确认制作细节与结果。' : '直接提问或描述你想制作的内容，结果会留在当前聊天。' }}</p></div>
      <button v-if="studioFocus" type="button" class="studio-history-toggle" :aria-expanded="historyOpen" aria-label="查看历史聊天" @click="historyOpen = !historyOpen"><History :size="15" /> 历史聊天</button>
      <SystemStatusInline :label="sending || Object.keys(streamingByMessage).length ? 'Kantoku 正在回复' : '就绪'" :tone="sending || Object.keys(streamingByMessage).length ? 'active' : 'neutral'" />
    </header>
    <div v-if="domain === 'commerce'" class="commerce-mode"><span>商品数据</span><button type="button" :aria-pressed="dataMode === 'production'" @click="dataMode = 'production'">Production</button><button type="button" :aria-pressed="dataMode === 'demo'" @click="dataMode = 'demo'">DEMO · Mock Data</button><strong v-if="dataMode === 'demo'">模拟数据，不代表真实市场商品</strong></div>
    <ChatMessageList :messages="messages" :loading="conversationLoading" :media-jobs="mediaJobs" :streaming="streaming" :streaming-by-message="streamingByMessage" :run="activeRun" :activities="activities" :public-activities="publicActivities" :image-url="imageUrl" :inline-runs="inlineRuns" :pending-messages="pendingMessages" :activity-by-message="activityByMessage" :image-phases="imagePhases" :message-media="messageMedia" :message-media-errors="messageMediaErrors" :error-message-id="errorMessageId" :home-mode="!domain" :error="error" :empty-hint="domain ? presenterFor(domain).guideHint : undefined" :examples="domain ? presenterFor(domain).examples : undefined" :approval-busy="deciding" @retry="lastContent && send(lastContent)" @open-run="openRun" @decide-inline="decideInline" @decide-media="decideMedia" @example="(text) => composer?.fill(text)" />
    <div v-if="domain && pendingApproval" class="home-approval"><ApprovalCard :approval="pendingApproval" :domain="activeRun?.domain ?? 'studio'" :busy="sending" :image-url="imageUrl" @decide="decide" /></div>
    </div>
  </WorkspaceShell>
</template>

<style scoped>
/* ==========================================================================
   首页 · 原型 .homeGrid
   天空由 App 外壳的 PixelSky 统一提供（fixed 层），所以本页保持透明，
   不再自己画一遍渐变与草地——两套背景叠在一起会出现两道草地。
   ========================================================================== */
.home-page {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: transparent;
}

.home-grid {
  position: relative;
  z-index: 2;
  display: grid;
  flex: 1;
  /* 单列：历史/产物改走抽屉，主内容独占整行。 */
  grid-template-columns: minmax(0, 1fr);
  min-height: 0;
}

/* ---------- 左：标题 + 对话流 + 输入 ---------- */
.home-stream-col {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 16px 22px 0;
}

.home-stream-hd {
  display: flex;
  flex: 0 0 auto;
  align-items: flex-end;
  gap: 14px;
  padding-bottom: 12px;
}

.home-kick {
  display: inline-block;
  padding: 0;
  border: 0;
  background: transparent;
  color: #0b2452;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: .2em;
  box-shadow: none;
}

.home-stream-hd h1 {
  margin: 4px 0 0;
  color: var(--paper);
  font-size: 30px;
  font-weight: 900;
  letter-spacing: -.005em;
  -webkit-text-stroke: 3px var(--ink);
  paint-order: stroke fill;
  text-shadow: 4px 4px 0 var(--ink);
}

.home-tools { display: flex; align-items: center; gap: 8px; margin-left: auto; }

.home-tbtn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 10px 5px;
  border: 3px solid var(--ink);
  background: var(--ink);
  color: var(--paper);
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: .14em;
  white-space: nowrap;
  box-shadow: 4px 4px 0 rgba(20, 20, 20, .35);
  cursor: pointer;
  transition: transform 100ms steps(2, end), box-shadow 100ms steps(2, end), background 100ms;
}

.home-tbtn:hover { background: var(--brick); }
.home-tbtn:active { transform: translate(3px, 3px); box-shadow: 1px 1px 0 rgba(20, 20, 20, .5); }
.home-tbtn.is-lite { background: var(--paper); color: var(--ink); }
.home-tbtn.is-lite:hover { background: var(--gold-light); }
.home-tbtn[aria-pressed='true'] { background: var(--gold); border-color: var(--ink); color: var(--ink); }
.home-tbtn--history b { display: inline-grid; place-items: center; min-width: 16px; padding: 0 3px; background: var(--gold); color: var(--ink); font-size: 9px; line-height: 1.6; }
.home-tbtn[aria-pressed='true'] b { background: var(--ink); color: var(--gold-light); }

.home-stream {
  display: flex;
  flex: 1;
  min-height: 0;
  padding: 4px 6px 18px 2px;
  overflow: hidden;
}

.home-stream :deep(.message-scroller) { flex:1; min-width:0; }

.home-stream::-webkit-scrollbar { width: 9px; }
.home-stream::-webkit-scrollbar-track { background: transparent; }
.home-stream::-webkit-scrollbar-thumb { border: 3px solid var(--sky-2); background: var(--brick); }

/* 右栏收起后主区变宽，输入区跟着拉成一条长条不好看：与消息列同宽居中。 */
.home-composer { flex: 0 0 auto; width: 100%; max-width: 880px; margin: 0 auto; padding: 0 0 22px; }

/* 提示语是说明，不是控件：只留字，不再画一个长得像按钮的框。 */
.home-chint {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  max-width: 760px;
  margin: 10px 0 0;
  padding: 0 2px;
  border: 0;
  background: transparent;
  color: #0b1e45;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .12em;
  box-shadow: none;
  opacity: .78;
}

.home-chint .sp { flex: 1; }
.home-chint-key { opacity: .65; }

/* 输入区里的通用控件按原作改成砖块语言 */
.home-composer :deep(.composer-field),
.home-composer :deep(.composer-dock) { background: transparent; box-shadow: none; border: 0; padding: 0; }

/* ---------- 右：历史与产物（已改为抽屉，样式见 mario.css 段 16） ---------- */
.home-block-hd {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
}

.home-block-hd.is-dark { background: var(--ink-2); color: var(--paper); }
.home-block-hd.is-deep { background: var(--ink-3); color: var(--paper); }

.home-block-hd .lbl {
  color: var(--gold-light);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: .22em;
  text-transform: uppercase;
}

.home-block-hd .n {
  margin-left: auto;
  color: #8c8c8c;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: .12em;
}

.home-recent-list.drawer-history { flex: 1; min-height: 0; padding: 0; overflow: hidden; }

.home-block { flex: 0 0 auto; border-top: 4px solid var(--ink); }
.home-block-bd { display: grid; gap: 7px; padding: 8px 10px 10px; }

.home-job { display: grid; gap: 5px; }
.home-job-top { display: flex; align-items: center; gap: 6px; }
.home-job-top strong { font-size: 10.5px; font-weight: 700; }

.home-job small {
  color: #7a7263;
  font-family: var(--font-mono);
  font-size: 8px;
  letter-spacing: .05em;
}

.home-tag {
  margin-left: auto;
  padding: 1px 5px;
  border: 2px solid var(--ink);
  background: var(--paper-2);
  color: #5f5747;
  font-family: var(--font-mono);
  font-size: 8px;
  letter-spacing: .1em;
  white-space: nowrap;
}

.home-tag.is-warn { background: #ffe7a8; color: #7a5200; }

/* 进度条：后端只给状态、不给百分比，所以画的是"进行中"的走动条纹，不写数字 */
.home-prog {
  height: 13px;
  border: 3px solid var(--ink);
  background: var(--paper-2);
  overflow: hidden;
}

.home-prog i {
  display: block;
  width: 60%;
  height: 100%;
  background: repeating-linear-gradient(90deg, var(--grass) 0 7px, var(--grass-dark) 7px 9px);
  animation: home-prog-move 1.4s steps(8, end) infinite;
}

@keyframes home-prog-move {
  from { transform: translateX(-100%); }
  to { transform: translateX(180%); }
}

@media (prefers-reduced-motion: reduce) { .home-prog i { animation: none; width: 100%; } }

.home-thumbs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 5px;
  padding: 8px 10px 10px;
}

.home-thumbs figure { position: relative; margin: 0; overflow: hidden; border: 3px solid var(--ink); background: var(--canvas-inset); }

.home-thumbs img {
  display: block;
  width: 100%;
  aspect-ratio: 4 / 3;
  object-fit: cover;
}

.home-thumbs figcaption {
  position: absolute;
  bottom: 0;
  left: 0;
  padding: 1px 3px;
  background: var(--ink);
  color: var(--gold-light);
  font-family: var(--font-mono);
  font-size: 7.5px;
  letter-spacing: .06em;
}

.home-empty {
  margin: 0;
  padding: 10px 12px 12px;
  color: #7a7263;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: .06em;
  line-height: 1.7;
}

.home-quick {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 5px;
  padding: 8px 10px 10px;
}

.home-quick button {
  padding: 5px 2px;
  border: 3px solid var(--ink);
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 8.5px;
  letter-spacing: .06em;
  box-shadow: 3px 3px 0 rgba(20, 20, 20, .4);
  cursor: pointer;
}

.home-quick button:hover { background: var(--gold-light); }
.home-quick button:active { transform: translate(2px, 2px); box-shadow: 0 0 0 rgba(0, 0, 0, 0); }

.home-recent-foot {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  gap: 6px;
  padding: 9px 12px;
  border-top: 3px solid var(--paper-3);
}

.home-recent-foot .kv {
  display: flex;
  justify-content: space-between;
  color: #6b6355;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: .1em;
}

.home-recent-foot .kv b { color: var(--ink); font-weight: 700; }

.home-recent-foot .note {
  margin: 0;
  color: #7a7263;
  font-size: 10.5px;
  line-height: 1.6;
}

/* ---------- 领域工作区形态（沿用 WorkspaceShell） ---------- */
.home-workspace .chat-home { height: 100%; min-height: 0; width: 100%; }
.home-conversation-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; font-weight: 600; }
.home-composer-hint { margin: 8px 0 0; color: var(--text-muted); font-size: 11px; text-align: center; }
/* 对话区高度交给内容决定，滚动由 WorkspaceShell 的 .ws-shell-body.is-flow 承担。
   必须用 :deep() —— .ws-shell-body 属于 WorkspaceShell，本组件的 scoped 属性不落在它身上。 */
.home-workspace :deep(.ws-shell-body.is-flow) .chat-home { height: auto; }

/* ---------- 流内消息：原型 .turn / .face / .bub ---------- */
.home-stream :deep(.message-list) {
  max-width: none;
  min-height: 100%;
  margin: 0;
  padding: 4px 2px 8px;
  gap: 14px;
  justify-content: flex-start;
}

/* 空状态：原型里这是一条"KANTOKU · 就绪"的开场气泡，不是一个居中的灰字 */
.home-stream :deep(.chat-empty-brand) {
  justify-content: flex-start;
  gap: 10px;
  margin: 0;
}

/* 名字做成小铭牌：四个时段（清晨/白天/傍晚/夜晚）都有足够对比度，
   不依赖 --sky-text 随天色翻转。 */
.home-stream :deep(.chat-empty-brand span) {
  padding: 2px 7px 1px;
  border: 3px solid var(--ink);
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: .22em;
  box-shadow: 3px 3px 0 rgba(20, 20, 20, .4);
}

.home-stream :deep(.chat-empty-brand .kantoku-mark) {
  width: 30px;
  height: 30px;
  flex-basis: 30px;
  border: 3px solid var(--ink);
  background: var(--paper);
  box-shadow: 3px 3px 0 rgba(20, 20, 20, .35);
}

.home-stream :deep(.chat-empty-brand + .list-empty-hint) {
  max-width: 560px;
  margin: 0;
  padding: 11px 13px 12px;
  border: 4px solid var(--ink);
  background: var(--paper);
  box-shadow: 6px 6px 0 var(--ink);
  color: var(--ink);
  font-size: 13px;
  font-weight: 400;
  line-height: 1.72;
  text-align: left;
}

.home-stream :deep(.chat-empty-brand + .list-empty-hint + .list-examples) { margin: 0; justify-content: flex-start; }

.home-stream :deep(.chat-row.assistant) {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  max-width: min(80%, 760px);
}

.home-stream :deep(.assistant-mark) {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 30px;
  height: 30px;
  border: 3px solid var(--ink);
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  box-shadow: 3px 3px 0 rgba(20, 20, 20, .35);
}

.home-stream :deep(.assistant-copy) {
  min-width: 0;
  padding: 11px 13px 12px;
  border: 4px solid var(--ink);
  background: var(--paper);
  box-shadow: 6px 6px 0 var(--ink);
  color: var(--ink);
  font-size: 13px;
  line-height: 1.72;
}

.home-stream :deep(.chat-row.user) { align-items: flex-start; gap: 11px; }

.home-stream :deep(.user-bubble) {
  max-width: min(76%, 620px);
  padding: 11px 13px 12px;
  border: 4px solid var(--ink);
  border-radius: 0;
  background: var(--gold-light);
  box-shadow: 6px 6px 0 var(--ink);
  color: var(--ink);
  font-size: 13px;
  line-height: 1.72;
}

/* 「你」的方块头像：原型 .turn.me .face（砖红底 + 纸色字） */
.home-stream :deep(.chat-row.user)::after {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 30px;
  height: 30px;
  border: 3px solid var(--ink);
  background: var(--brick);
  color: var(--paper);
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  box-shadow: 3px 3px 0 rgba(20, 20, 20, .35);
  content: '你';
}

.home-stream :deep(.chat-cost-card),
.home-stream :deep(.chat-image-generation) {
  border: 4px solid var(--ink);
  border-radius: 0;
  background: #fff6de;
  box-shadow: 6px 6px 0 var(--ink);
}

/* ---------- 输入区：纸卡 + 金砖发送键 ---------- */
.home-composer :deep(.message-composer) {
  position: relative;
  display: grid;
  gap: 6px;
  max-width: 920px;
  margin-left: 56px;
  padding: 10px 12px;
  border: 4px solid var(--ink);
  border-radius: 0;
  background: var(--paper);
  box-shadow: 6px 6px 0 var(--ink);
}

.home-composer :deep(.message-composer textarea) {
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.7;
  resize: none;
}

.home-composer :deep(.message-composer:focus-within) { outline: 3px solid var(--gold); outline-offset: 3px; }
.home-composer :deep(.message-composer textarea::placeholder) { color: #8e8e8e; font-weight: 500; }

.home-composer :deep(.message-composer footer) { align-items: center; gap: 8px; }
.home-composer :deep(.message-composer footer > span) {
  color: #8a8272;
  font-family: var(--font-mono);
  font-size: 8.5px;
  letter-spacing: .1em;
}

.home-composer :deep(.composer-tool) {
  width: 28px;
  height: 28px;
  border: 3px solid transparent;
  border-radius: 0;
  color: var(--text-muted);
}

.home-composer :deep(.composer-tool:hover:not(:disabled)) { border-color: var(--ink); background: var(--paper-2); color: var(--ink); }

/* 问号砖块仍是实际发送按钮，但回到输入卡内部：不再浮到卡外抢第二个视觉重心。 */
#app .home-composer :deep(.composer-send) {
  position: absolute;
  right: 10px;
  bottom: 10px;
  top: auto;
  left: auto;
  width: 40px;
  height: 40px;
  border: 3px solid var(--ink);
  border-radius: 0;
  background: var(--gold);
  color: var(--ink);
  box-shadow: 3px 3px 0 var(--ink);
  transition: transform 90ms steps(2, end), box-shadow 90ms steps(2, end), background 120ms steps(2, end);
}
#app .home-composer :deep(.composer-send svg) { display: none; }
#app .home-composer :deep(.composer-send)::after {
  content: '?';
  font-family: var(--font-mono);
  font-size: 17px;
  font-weight: 900;
}
#app .home-composer :deep(.composer-send:focus-visible) { outline: 3px solid var(--sky-1); outline-offset: 3px; }

.home-composer :deep(.composer-send:hover:not(:disabled)) { background: var(--gold-light); }
.home-composer :deep(.composer-send:active:not(:disabled)) { transform: translate(4px, 4px); box-shadow: 1px 1px 0 var(--ink); }

.home-composer :deep(.composer-send:disabled) {
  border-color: var(--border-soft);
  background: var(--paper-2);
  color: var(--text-muted);
  box-shadow: none;
  opacity: 1;
}

.home-composer :deep(.composer-domain-chip) {
  border: 3px solid var(--ink);
  border-radius: 0;
  background: var(--paper-2);
  box-shadow: 3px 3px 0 rgba(20, 20, 20, .35);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 9px;
}

.home-composer :deep(.composer-domain-menu) {
  border: 4px solid var(--ink);
  border-radius: 0;
  background: var(--paper);
  box-shadow: 6px 6px 0 rgba(20, 20, 20, .45);
}

@media (max-width: 1280px) {
}

@media (max-width: 1040px) {
}

@media (max-width: 700px) {
  .home-stream-col { padding: 14px 14px 0; }
  .home-stream-hd { align-items: flex-start; flex-wrap: wrap; }
  .home-stream-hd h1 { font-size: 25px; }
  .home-tools { margin-left: 0; }
  .home-stream :deep(.chat-row.assistant) { max-width: 100%; }
  .home-composer :deep(.message-composer textarea) { min-height:64px; }
}
</style>
