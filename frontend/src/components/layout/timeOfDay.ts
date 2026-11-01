import { ref } from 'vue'

/**
 * 时段（天空配色）控制器。
 *
 * 设计意图：背景跟着本机时间走，用户不必设置；仍允许手动锁定某一段用于检查视觉。
 * 状态放在模块里而不是 Vuex/Pinia：与 router.ts 用模块级 ref 保存路由的现有做法一致，
 * 不引入任何新依赖。
 */
export type TimeOfDay = 'dawn' | 'day' | 'dusk' | 'night'
export type TimeOfDayMode = 'auto' | TimeOfDay

export const TOD_LABELS: Record<TimeOfDay, string> = {
  dawn: '清晨',
  day: '白天',
  dusk: '傍晚',
  night: '夜晚',
}

export const TOD_OPTIONS: { id: TimeOfDayMode; label: string }[] = [
  { id: 'auto', label: '自动' },
  { id: 'dawn', label: '清晨' },
  { id: 'day', label: '白天' },
  { id: 'dusk', label: '傍晚' },
  { id: 'night', label: '夜晚' },
]

const STORAGE_KEY = 'kantoku-time-of-day'

/** 本机时间 → 时段。边界与原型一致：05–07:30 清晨，07:30–16:30 白天，16:30–19:00 傍晚。 */
export function todOfNow(now = new Date()): TimeOfDay {
  const hour = now.getHours() + now.getMinutes() / 60
  if (hour >= 5 && hour < 7.5) return 'dawn'
  if (hour >= 7.5 && hour < 16.5) return 'day'
  if (hour >= 16.5 && hour < 19) return 'dusk'
  return 'night'
}

function readStoredMode(): TimeOfDayMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'auto' || saved === 'dawn' || saved === 'day' || saved === 'dusk' || saved === 'night') return saved
  } catch { /* 隐私模式或非浏览器环境：退回自动 */ }
  return 'auto'
}

export const todMode = ref<TimeOfDayMode>(readStoredMode())
export const todNow = ref<TimeOfDay>(todOfNow())

/** 生效中的时段：手动模式就用手动的，自动模式按当前时间实时算。 */
export function effectiveTod(): TimeOfDay {
  return todMode.value === 'auto' ? todNow.value : todMode.value
}

function paint(): void {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.tod = effectiveTod()
}

/** 手动切换（`auto` 表示跟随时间）。立即生效并写入 localStorage。 */
export function setTodMode(mode: TimeOfDayMode): void {
  todMode.value = mode
  todNow.value = todOfNow()
  try { localStorage.setItem(STORAGE_KEY, mode) } catch { /* 写入失败不影响本次渲染 */ }
  paint()
}

let started = false
let timer: number | undefined

/** 启动：立刻上色，并每分钟校准一次（自动模式下跨过时段边界会自然切换）。 */
export function startTimeOfDay(): void {
  if (typeof window === 'undefined') return
  todNow.value = todOfNow()
  paint()
  if (started) return
  started = true
  timer = window.setInterval(() => {
    const next = todOfNow()
    if (next === todNow.value) return
    todNow.value = next
    if (todMode.value === 'auto') paint()
  }, 60_000)
}

export function stopTimeOfDay(): void {
  if (timer !== undefined) window.clearInterval(timer)
  timer = undefined
  started = false
}
