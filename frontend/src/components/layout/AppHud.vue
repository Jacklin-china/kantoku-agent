<script setup lang="ts">
/**
 * 顶部 HUD 横牌 · 对应原型 `.hud`
 *
 * 纪律：这里每一项都必须对应真实状态。
 *   world  = 当前路由（App 传入）
 *   online = Kantoku Core 轮询结果（App 传入）
 *   clock / 时段 = 本机时间与手动锁定
 * 原型里的「功能对照」「存档位」「保存状态」在本项目没有对应后端状态，一律不搬。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { TOD_LABELS, TOD_OPTIONS, effectiveTod, setTodMode, todMode } from './timeOfDay'

const props = defineProps<{ world: string; online: boolean; reason?: string }>()
const emit = defineEmits<{ openCommand: []; home: [] }>()

const clock = ref('')
let timer: number | undefined

function tick(): void {
  const now = new Date()
  clock.value = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
}

onMounted(() => {
  tick()
  timer = window.setInterval(tick, 15_000)
})

onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer)
})

const onlineTitle = computed(() =>
  props.online ? 'Kantoku Core · 本机已连接' : `Kantoku Core 连接中断：${props.reason || '正在重试'}`,
)
</script>

<template>
  <header class="px-hud" role="banner">
    <button class="px-hud__brand" type="button" title="回到首页" @click="emit('home')">
      <i class="px-hud__mark" />
      <span class="px-hud__name">KANTOKU</span>
    </button>
    <span class="px-hud__world">{{ props.world }}</span>
    <span class="px-hud__grow" />

    <button class="px-hud__ghost" type="button" @click="emit('openCommand')">命令面板 <kbd>⌘K</kbd></button>

    <span class="px-hud__todgrp" role="group" aria-label="背景时段">
      <button
        v-for="option in TOD_OPTIONS"
        :key="option.id"
        type="button"
        :aria-pressed="todMode === option.id"
        :title="option.id === 'auto' ? '跟随本机时间' : `固定为${option.label}`"
        @click="setTodMode(option.id)"
      >{{ option.label }}</button>
    </span>

    <span class="px-hud__stat" :title="`本机时间 · ${TOD_LABELS[effectiveTod()]}`">
      <b>{{ clock }}</b>
    </span>
    <span class="px-hud__stat" :title="onlineTitle">
      <i :class="{ 'is-down': !props.online }" />
      <b :class="{ 'is-ok': props.online }">{{ props.online ? '本机已连接' : '连接中断' }}</b>
    </span>
  </header>
</template>
