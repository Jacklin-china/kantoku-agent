<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppHud from './components/layout/AppHud.vue'
import AppConsole from './components/layout/AppConsole.vue'
import CommandPalette from './components/layout/CommandPalette.vue'
import PixelSky from './components/layout/PixelSky.vue'
import SidebarNav from './components/layout/SidebarNav.vue'
import HomeView from './views/HomeView.vue'
import { navigate, parse, route, workspaceEntry } from './router'
import { disposeAll, getApprovals, getRuns } from './services/core'
import type { CoreApproval, CoreRun } from './types'

const ProductionWorkspace = defineAsyncComponent(() => import('./views/ProductionWorkspace.vue'))
const TasksCenter = defineAsyncComponent(() => import('./views/TasksCenter.vue'))
const AssetsLibrary = defineAsyncComponent(() => import('./views/AssetsLibrary.vue'))
const AccountView = defineAsyncComponent(() => import('./views/AccountView.vue'))

const hasAccount = ref(typeof document !== 'undefined'
  && document.documentElement.dataset.kantokuRegistered === 'true')
function requestLogout(): void {
  window.dispatchEvent(new Event('kantoku-auth-logout'))
}

/* 原有排版骨架：侧栏导航 + 顶栏 + 页面舞台；工作逻辑全部来自真实 Core 数据。 */
const runs = ref<CoreRun[]>([])
const approvals = ref<CoreApproval[]>([])
/* HUD 的「本机已连接」不是装饰：由 Core 轮询结果驱动。
   轮询失败只改变连接状态，不伪造成功；错误原因保留在 coreError 里供标题提示。 */
const coreOnline = ref(true)
const coreError = ref('')
/* HUD 上的世界铭牌 = 当前路由，全部来自真实路由表。 */
const WORLD_LABELS: Record<string, string> = {
  home: 'WORLD 1-1 · 首页对话',
  workspace: 'WORLD 1-2 · 工作区',
  workspace_run: 'WORLD 1-2 · 工作区',
  tasks: 'WORLD 3-1 · 任务',
  task_run: 'WORLD 3-1 · 任务',
  task_batch: 'WORLD 3-1 · 任务',
  assets: 'WORLD 2-1 · 资产',
  account: 'ACCOUNT · 个人中心',
  settings: 'SETTINGS · 设置',
}
const worldLabel = computed(() => WORLD_LABELS[route.value.name] ?? 'KANTOKU')
/* 用户主动折叠偏好：持久化到 localStorage（与既有"会话 ID 存 localStorage"的做法一致，不引入状态库）。
   首次访问时按窗口宽度给默认值：窄窗默认收起，宽屏默认展开。 */
const STORAGE_KEY = 'kantoku-sidebar-collapsed'
function readCollapsed(): boolean {
  // SSR / 单元测试环境没有 localStorage 与 window，必须整体容错。
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved !== null) return saved === '1'
  } catch { /* 隐私模式或非浏览器环境：忽略，退回宽度判断 */ }
  try {
    return window.matchMedia('(max-width: 800px)').matches
  } catch { return false }
}
const sidebarCollapsed = ref(readCollapsed())
function toggleSidebar(): void {
  sidebarCollapsed.value = !sidebarCollapsed.value
  try { localStorage.setItem(STORAGE_KEY, sidebarCollapsed.value ? '1' : '0') } catch { /* 忽略写入失败 */ }
}
const commandOpen = ref(false)
/* 左栏「历史」是一个跨页动作：首页与工作区各自持有自己的历史抽屉，
   计数信号递增一次就让当前页面的抽屉滑出。用递增数字而不是布尔，
   是因为连续点两次也要有反应（用户以为没点中时的下意识重试）。 */
const historySignal = ref(0)
let timer: number | undefined
let coreEpoch = 0, coreMounted = false, disposed = false
let coreRequest: { epoch: number; promise: Promise<void> } | null = null

const activeDomain = computed(() => route.value.domain ?? 'comic')
const comicWorkspace = computed(() => ['workspace', 'workspace_run'].includes(route.value.name) && route.value.domain === 'comic')
/* 工作区与首页共用同一套侧栏形态。
   旧实现在工作区强行折叠成"只有图标"的窄条（86px → 62px），
   结果是同一块导航在两个页面长得不一样、还丢掉了个人中心/设置/退出登录与 Core 状态。
   现在只保留用户自己的折叠偏好，切换页面时侧栏观感完全一致。 */
const conversationWorkspace = computed(() => comicWorkspace.value)
/* 合成值：用户主动折叠时，外壳列才收窄。 */
const shellCollapsed = computed(() => sidebarCollapsed.value)

async function refreshCore(): Promise<void> {
  if (disposed || comicWorkspace.value) return
  const epoch = coreEpoch
  if (coreRequest?.epoch === epoch) return coreRequest.promise
  const pending = Promise.resolve().then(async () => {
    try {
      if (disposed || comicWorkspace.value || epoch !== coreEpoch) return
      const [runData, approvalData] = await Promise.all([getRuns(), getApprovals()])
      if (disposed || comicWorkspace.value || epoch !== coreEpoch) return
      if (runData) {
        const hiddenIds = new Set(runData.filter((run) => run.domain === 'commerce').map((run) => run.id))
        runs.value = runData.filter((run) => run.domain !== 'commerce')
        if (approvalData) approvals.value = approvalData.filter((item) => !hiddenIds.has(item.run_id))
      }
      coreOnline.value = true
      coreError.value = ''
    } catch (error) {
      /* 轮询失败是「连接状态」，不是业务错误：如实标记到 HUD，不吞掉原因。 */
      coreOnline.value = false
      coreError.value = error instanceof Error ? error.message : 'Core 连接失败'
    } finally { if (coreRequest?.promise === pending) coreRequest = null }
  })
  coreRequest = { epoch, promise: pending }
  return pending
}

function syncCorePolling(): void {
  if (timer !== undefined) window.clearInterval(timer)
  timer = undefined
  // Comic 工作区自行读取所属任务；全局列表不参与其首屏和执行状态。
  if (disposed || comicWorkspace.value) return
  void refreshCore()
  timer = window.setInterval(() => void refreshCore(), 4000)
}
watch(comicWorkspace, () => {
  coreEpoch++
  if (coreMounted) syncCorePolling()
}, { flush: 'sync' })

function go(path: string): void {
  const next = parse(path)
  navigate(next.name === 'workspace' && !next.workId && !next.conversationId
    && !next.workspacePage ? workspaceEntry(next.domain) : next)
}

function shortcut(event: KeyboardEvent): void {
  const target = event.target
  if (target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))) return
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    commandOpen.value = !commandOpen.value
  }
}

onMounted(() => {
  coreMounted = true
  window.addEventListener('keydown', shortcut)
  syncCorePolling()
})
onBeforeUnmount(() => {
  disposed = true; coreEpoch++
  window.removeEventListener('keydown', shortcut)
  if (timer !== undefined) window.clearInterval(timer)
  disposeAll()
})
</script>

<template>
  <div class="px-app">
    <PixelSky />
    <AppHud
      :world="worldLabel"
      :online="coreOnline"
      :reason="coreError"
      @open-command="commandOpen = true"
      @home="go('/')"
    />
    <div class="kantoku-shell" :class="{ 'sidebar-collapsed': shellCollapsed, 'comic-app-shell': conversationWorkspace }">
    <SidebarNav
      :route-name="route.name"
      :active-domain-id="activeDomain"
      :task-count="runs.filter((item) => ['pending', 'running', 'waiting'].includes(item.status)).length"
      :collapsed="shellCollapsed"
      :has-account="hasAccount"
      :online="coreOnline"
      @navigate="go"
      @toggle="toggleSidebar"
      @logout="requestLogout"
      @history="historySignal += 1"
    />
    <section class="app-stage">
      <Transition name="route" mode="out-in">
        <HomeView
          v-if="route.name === 'home'"
          key="home"
          :runs="runs"
          :approvals="approvals"
          :busy="false"
          :history-signal="historySignal"
          @refresh="refreshCore"
        />
        <ProductionWorkspace
          v-else-if="route.name === 'workspace' || route.name === 'workspace_run'"
          :key="`${route.domain}:${route.runId ?? 'new'}`"
          :domain="route.domain ?? 'studio'"
          :run-id="route.runId"
          :history-signal="historySignal"
        />
        <TasksCenter
          v-else-if="route.name === 'tasks'"
          :key="`tasks:${route.tab ?? 'running'}`"
          :tab="route.tab"
        />
        <TasksCenter
          v-else-if="route.name === 'task_run'"
          :key="`task-run:${route.runId}`"
          tab="running"
          :focus-run-id="route.runId"
        />
        <TasksCenter
          v-else-if="route.name === 'task_batch'"
          :key="`task-batch:${route.batchId}`"
          tab="batches"
          :focus-batch-id="route.batchId"
        />
        <AssetsLibrary v-else-if="route.name === 'assets'" key="assets" />
        <AccountView v-else-if="route.name === 'account'" key="account" />
        <main v-else key="settings" class="page settings-page px-page">
          <div class="px-wrap">
            <header class="px-pghd">
              <span class="px-pghd__kick">SETTINGS · 本机</span>
              <h1>设置</h1>
              <p>模型与供应商配置请前往个人中心。</p>
            </header>

            <div class="px-cols">
              <div class="px-stack">
                <section class="px-sheet">
                  <div class="px-sheet-hd"><span class="lbl">去向</span><span class="n">这一页只有入口</span></div>
                  <div class="px-sheet-bd">
                    <p class="px-note">设置页本身不承载配置项：模型与供应商凭据归在个人中心，本机账户与安全也在那里。</p>
                    <div class="px-frow">
                      <button v-if="hasAccount" class="px-btn is-gold" type="button" @click="navigate({ name: 'account' })">打开个人中心</button>
                      <button class="px-btn" type="button" @click="commandOpen = true">打开命令面板</button>
                    </div>
                    <p v-if="!hasAccount" class="px-rowhint">创建账户后可在个人中心管理配置。</p>
                  </div>
                </section>

                <section class="px-sheet">
                  <div class="px-sheet-hd"><span class="lbl">命令面板</span><span class="n">Ctrl+K 呼出</span></div>
                  <div class="px-sheet-bd">
                    <div class="px-kvrow"><span>打开首页</span><b>/</b></div>
                    <div class="px-kvrow"><span>打开工作台</span><b>/workspace/comic</b></div>
                    <div class="px-kvrow"><span>浏览资产</span><b>/assets</b></div>
                    <div class="px-kvrow"><span>打开设置</span><b>/settings</b></div>
                  </div>
                </section>
              </div>

              <div class="px-stack">
                <section class="px-sheet">
                  <div class="px-sheet-hd"><span class="lbl">本机状态</span><span class="n">只读</span></div>
                  <div class="px-sheet-bd">
                    <div class="px-kvrow"><span>Core</span><b>{{ coreOnline ? '已连接' : '连接中断' }}</b></div>
                    <div class="px-kvrow"><span>账户</span><b>{{ hasAccount ? '已登录 · 本机' : '未创建' }}</b></div>
                    <div class="px-kvrow"><span>默认领域</span><b>漫剧制作</b></div>
                  </div>
                </section>
                <p class="px-pgnote">这一页保持的是产品的真实样子：配置项都在个人中心，设置只做入口与命令面板，不放没有对应接口的开关。</p>
              </div>
            </div>
          </div>
        </main>
      </Transition>
    </section>
    </div>

    <AppConsole :online="coreOnline" :page="worldLabel" />

    <CommandPalette
      :open="commandOpen"
      @close="commandOpen = false"
      @navigate="go"
      @domain="(id: string) => navigate(workspaceEntry(id))"
    />
  </div>
</template>
