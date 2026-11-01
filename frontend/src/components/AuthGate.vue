<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import App from '../App.vue'
import PixelSky from './layout/PixelSky.vue'
import { startTimeOfDay, stopTimeOfDay } from './layout/timeOfDay'
import { logout } from '../services/core'
import LoginView from '../views/LoginView.vue'

const devHost = typeof window !== 'undefined' && window.location?.hostname === 'localhost'
  ? 'localhost' : '127.0.0.1'
const base = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '')
  ?? (import.meta.env.DEV ? `http://${devHost}:8000` : '')
const state = ref<'checking' | 'login' | 'register' | 'ready' | 'offline'>('checking')
const submitting = ref(false)
const error = ref('')

async function check(): Promise<void> {
  state.value = 'checking'
  error.value = ''
  try {
    const response = await fetch(`${base}/api/auth/status`, { credentials: 'include' })
    if (!response.ok) throw new Error('无法检查登录状态')
    const status = await response.json() as { registered: boolean; authenticated: boolean }
    document.documentElement.dataset.kantokuRegistered = String(status.registered)
    state.value = status.registered ? status.authenticated ? 'ready' : 'login' : 'register'
  } catch {
    state.value = 'offline'
  }
}

async function submit(details: { username: string; password: string }): Promise<void> {
  if (submitting.value) return
  submitting.value = true
  error.value = ''
  try {
    const action = state.value === 'register' ? 'register' : 'login'
    const response = await fetch(`${base}/api/auth/${action}`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-Kantoku-Login': '1' },
      body: JSON.stringify(details),
    })
    const body = await response.json() as { error?: string }
    if (!response.ok) {
      error.value = body.error ?? '操作没有完成，请重试。'
      return
    }
    await check()
  } catch {
    error.value = '无法连接后端，请确认后端正在运行。'
  } finally {
    submitting.value = false
  }
}

function requireLogin(): void { void check() }
async function signOut(): Promise<void> {
  try {
    await logout()
    await check()
  } catch {
    window.alert('退出登录失败，请检查后端连接。')
  }
}
onMounted(() => {
  startTimeOfDay()
  window.addEventListener('kantoku-auth-required', requireLogin)
  window.addEventListener('kantoku-auth-logout', signOut)
  void check()
})
onBeforeUnmount(() => {
  stopTimeOfDay()
  window.removeEventListener('kantoku-auth-required', requireLogin)
  window.removeEventListener('kantoku-auth-logout', signOut)
})
</script>

<template>
  <PixelSky v-if="state !== 'ready'" />
  <App v-if="state === 'ready'" />
  <LoginView v-else-if="state === 'login' || state === 'register'"
    :registered="state === 'login'" :submitting="submitting" :error="error"
    @submit="submit" @continue-local="state = 'ready'" />
  <main v-else class="auth-status" role="status">
    <p v-if="state === 'checking'">正在检查登录状态…</p>
    <template v-else>
      <p>无法连接 Kantoku 后端，请确认后端已启动。</p>
      <button type="button" @click="check">重新连接</button>
    </template>
  </main>
</template>

<style scoped>
.auth-status { min-height: 100vh; display: grid; place-content: center; gap: 12px; text-align: center; color: var(--text-secondary); font-family: var(--font-ui); }
.auth-status button { padding: 8px 20px; border: 1px solid var(--border-strong); border-radius: var(--radius-control); background: var(--surface); color: var(--text-primary); cursor: pointer; }
</style>
