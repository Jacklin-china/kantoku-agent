<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

/**
 * 底部状态行（原型 .console）。
 *
 * 只展示真实状态：本机地址、当前页面（来自真实路由）、Core 在线与否、本机时钟。
 * 不承载任何业务入口或设置开关——它就是一条状态条，不是画布浮层。
 *
 * 时钟放在这个独立组件里：App.vue 的 Core 轮询测试桩只认 4000ms 那一个 interval，
 * 把 1s 时钟挪到这里，App.vue 的脚本就回到「只有轮询 interval」，测试契约不动。
 */
const props = defineProps<{ online: boolean; page: string }>()

const clock = ref(formatClock())
let timer: number | undefined

function formatClock(): string {
  try {
    return new Date().toLocaleTimeString('zh-CN', {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    })
  } catch { return '' }
}

onMounted(() => {
  clock.value = formatClock()
  timer = window.setInterval(() => { clock.value = formatClock() }, 1000)
})
onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<template>
  <footer class="px-console" aria-hidden="true">
    <span class="px-console__user">kantoku@localhost</span>
    <i class="px-console__caret" />
    <span class="px-console__page">{{ page }}</span>
    <span class="px-console__r">
      <span class="px-console__dim">LOCAL</span>
      <span class="px-console__online" :class="{ 'is-down': !online }"><i />{{ online ? 'CORE·ON' : 'CORE·OFF' }}</span>
      <span class="px-console__clock">{{ clock }}</span>
    </span>
  </footer>
</template>
