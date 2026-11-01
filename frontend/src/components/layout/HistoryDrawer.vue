<script setup lang="ts">
/**
 * 历史面板 · 右侧抽屉
 *
 * 为什么是抽屉而不是常驻栏：常驻右栏会永久占掉 258–340px 主内容宽度，
 * 在 1080 宽窗口下左侧对话被挤成一条缝，而它大部分时间是空的。
 * 改为按需滑出：默认不占宽度，打开时覆盖在内容之上，宽度还给主内容。
 *
 * 交互：点遮罩 / 按 Esc / 点右上角 × 关闭；打开时焦点落在抽屉内，关闭后还给触发按钮。
 */
import { onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{ open: boolean; title: string; subtitle?: string; count?: number }>()
const emit = defineEmits<{ close: [] }>()
const panel = ref<HTMLElement | null>(null)

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && props.open) emit('close')
}
watch(() => props.open, (open) => {
  if (typeof document === 'undefined') return
  if (open) {
    document.addEventListener('keydown', onKeydown)
    requestAnimationFrame(() => panel.value?.querySelector<HTMLElement>('input, button')?.focus())
  } else {
    document.removeEventListener('keydown', onKeydown)
  }
})
onBeforeUnmount(() => { if (typeof document !== 'undefined') document.removeEventListener('keydown', onKeydown) })
</script>

<template>
  <!-- 传送到 #app 根下：抽屉在首页与工作区的宿主层级不同，
       留在原地会被各自的 stacking context 影响，同一份样式时而盖住 HUD、时而被 HUD 盖住。 -->
  <Teleport to="#app">
    <div class="mk-drawer-layer" :class="{ 'is-open': open }" :aria-hidden="!open">
      <div class="mk-drawer-scrim" aria-hidden="true" @click="emit('close')" />
      <aside ref="panel" class="mk-drawer" role="dialog" :aria-label="title" :aria-modal="open || undefined">
        <header class="mk-drawer-hd">
          <span class="mk-drawer-kick">{{ subtitle || 'HISTORY' }}</span>
          <strong>{{ title }}</strong>
          <b v-if="count !== undefined" class="mk-drawer-count">{{ count }}</b>
          <button type="button" class="mk-drawer-x" aria-label="收起面板" title="收起（Esc）" @click="emit('close')">×</button>
        </header>
        <div class="mk-drawer-bd"><slot /></div>
      </aside>
    </div>
  </Teleport>
</template>
