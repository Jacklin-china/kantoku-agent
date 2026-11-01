<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Check, CircleCheck, Eye, EyeOff, Lock, RefreshCw, ShieldCheck, TriangleAlert, UserRound,
} from 'lucide-vue-next'
import {
  changeAccountPassword, getAccountProfile, getCreativeBalance, getTextBalance,
  saveAccountProfile, getProviderCredentials, saveProviderCredentials, configureVideoCredentials,
  type ProviderCredential, type TextBalance,
} from '../services/core'

/* ── 身份与资料 ───────────────────────────────────────────── */
const username = ref('')
const nickname = ref('')
const avatar = ref<string | null>(null)
const draftNickname = ref('')
const sessionStartedAt = ref<number | null>(null)
const profileLoading = ref(true)
const profileSaving = ref(false)
const avatarSaving = ref(false)
const profileError = ref('')
const profileNotice = ref('')
const avatarInput = ref<HTMLInputElement | null>(null)

/* 头像只有一个已保存状态：选图即提交，不留「选了但没存」的中间态。 */
const avatarSource = computed(() => avatar.value)
const avatarLocked = computed(() => avatarSaving.value || profileLoading.value)
const profileDirty = computed(() => draftNickname.value.trim() !== nickname.value)
const canSaveProfile = computed(() => !profileSaving.value && !avatarSaving.value && profileDirty.value
  && draftNickname.value.trim().length > 0 && draftNickname.value.trim().length <= 32)
const canRemoveAvatar = computed(() => avatar.value !== null && !avatarLocked.value)

/* ── 服务额度 ─────────────────────────────────────────────── */
interface QuotaState {
  loading: boolean
  balances: { currency: string; amount: string }[]
  code: string | null
  message: string | null
  updatedAt: string | null
}
function emptyQuota(): QuotaState {
  return { loading: true, balances: [], code: null, message: null, updatedAt: null }
}
const textQuota = reactive<QuotaState>(emptyQuota())
const creativeQuota = reactive<QuotaState>(emptyQuota())
const anyQuotaLoading = computed(() => textQuota.loading || creativeQuota.loading)

/* 已保存的凭据只查询配置状态，不回显服务端密钥。 */
const providers = ref<ProviderCredential[]>([])
const providerLoading = ref(true)
const providerError = ref('')
const textApiKey = ref('')
const textCredentialSaving = ref(false)
const textCredentialNotice = ref('')
const videoAccessKey = ref('')
const videoSecretKey = ref('')
const videoCredentialSaving = ref(false)
const videoCredentialNotice = ref('')
async function loadProviders(): Promise<void> {
  providerLoading.value = true
  try {
    const result = await getProviderCredentials()
    if (!result) throw new Error('服务暂不可用')
    providers.value = result.providers
    providerError.value = ''
  } catch {
    providerError.value = '无法读取模型与供应商配置，请稍后重试。'
  } finally {
    providerLoading.value = false
  }
}
async function saveVideoCredentials(): Promise<void> {
  if (videoCredentialSaving.value || !videoAccessKey.value || !videoSecretKey.value) return
  const accessKey = videoAccessKey.value
  const secretKey = videoSecretKey.value
  videoAccessKey.value = ''
  videoSecretKey.value = ''
  videoCredentialSaving.value = true
  providerError.value = ''
  videoCredentialNotice.value = ''
  try {
    await configureVideoCredentials(accessKey, secretKey)
    videoCredentialNotice.value = '视频凭据已用于当前服务进程，重启后失效。'
    await loadProviders()
    await loadCreativeQuota()
  } catch {
    providerError.value = '视频凭据设置失败，请检查输入；若视频服务已在运行，请重启后重试。'
  } finally {
    videoCredentialSaving.value = false
  }
}
async function saveTextCredentials(): Promise<void> {
  if (textCredentialSaving.value || !textApiKey.value.trim()) return
  const key = textApiKey.value.trim()
  textApiKey.value = ''
  textCredentialSaving.value = true
  providerError.value = ''
  textCredentialNotice.value = ''
  try {
    const result = await saveProviderCredentials('text', { api_key: key })
    providers.value = result.providers
    textCredentialNotice.value = result.message || '文字模型凭据已保存。'
    await loadTextQuota()
  } catch (error) {
    providerError.value = error instanceof Error ? error.message : '凭据保存失败，请重试。'
  } finally {
    textCredentialSaving.value = false
  }
}

const CURRENCY_SIGN: Record<string, string> = { CNY: '¥', USD: '$' }
function money(item: { currency: string; amount: string }): string {
  const sign = CURRENCY_SIGN[item.currency] ?? `${item.currency} `
  const value = Number(item.amount)
  const shown = Number.isFinite(value)
    ? value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : item.amount
  return `${sign} ${shown}`
}
function clockNow(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
}
function needsCredential(state: QuotaState): boolean {
  return state.code === 'credential_missing' || state.code === 'credential_invalid'
    || state.code === 'service_mismatch'
}
function quotaStatus(state: QuotaState): string {
  if (state.code === 'credential_missing') return '未配置'
  if (state.code === 'credential_invalid') return '密钥无效或无权限'
  if (state.code === 'service_mismatch') return '无法核实账户'
  return '查询失败'
}
function retryable(state: QuotaState): boolean {
  return state.code === 'upstream_error' || state.code === null
}

/* 余额查询失败时保留上一次成功时间：不能把「查不到」显示成「余额 0」。 */
async function loadQuota(state: QuotaState, read: () => Promise<TextBalance | null>): Promise<void> {
  state.loading = true
  try {
    const data = await read()
    if (!data) throw new Error('服务暂不可用')
    if (data.unavailable) {
      state.balances = []
      state.code = data.unavailable.code
      state.message = data.unavailable.message
    } else {
      state.balances = data.balances
      state.code = null
      state.message = null
      state.updatedAt = clockNow()
    }
  } catch (error) {
    state.balances = []
    state.code = 'upstream_error'
    state.message = error instanceof Error ? error.message : '余额暂不可用'
  } finally {
    state.loading = false
  }
}
const loadTextQuota = (): Promise<void> => loadQuota(textQuota, getTextBalance)
const loadCreativeQuota = (): Promise<void> => loadQuota(creativeQuota, getCreativeBalance)
function refreshAllQuotas(): void { void Promise.all([loadTextQuota(), loadCreativeQuota()]) }
let quotaTimer: number | undefined
function openProviderSettings(): void {
  document.getElementById('account-providers')?.scrollIntoView({ behavior: 'smooth' })
}

/* ── 账户安全 ─────────────────────────────────────────────── */
const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const showOld = ref(false)
const showNew = ref(false)
const showConfirm = ref(false)
const passwordSaving = ref(false)
const passwordError = ref('')
const passwordNotice = ref('')

const passwordStrength = computed(() => {
  const value = newPassword.value
  if (!value) return { segments: 0, label: '', tone: 'weak' }
  let score = 0
  if (value.length >= 8) score += 1
  if (value.length >= 12) score += 1
  if (/[a-z]/.test(value) && /[A-Z]/.test(value)) score += 1
  if (/\d/.test(value) && /[^A-Za-z0-9]/.test(value)) score += 1
  const segments = Math.min(3, Math.max(1, score))
  const tone = segments >= 3 ? 'strong' : segments === 2 ? 'fair' : 'weak'
  return { segments, label: { strong: '强', fair: '良好', weak: '弱' }[tone], tone }
})
const passwordMismatch = computed(() => confirmPassword.value.length > 0
  && confirmPassword.value !== newPassword.value)
const passwordHint = computed(() => {
  if (!oldPassword.value) return '请输入原密码'
  if (newPassword.value.length < 8) return '新密码至少 8 个字符'
  if (newPassword.value.length > 128) return '新密码不能超过 128 个字符'
  if (newPassword.value !== confirmPassword.value) return '两次输入的新密码不一致'
  return ''
})

/* ── 提示计时器 ───────────────────────────────────────────── */
let profileTimer: number | undefined
let passwordTimer: number | undefined
function flash(which: 'profile' | 'password', message: string): void {
  if (which === 'profile') {
    profileNotice.value = message
    window.clearTimeout(profileTimer)
    profileTimer = window.setTimeout(() => { profileNotice.value = '' }, 6000)
    return
  }
  passwordNotice.value = message
  window.clearTimeout(passwordTimer)
  passwordTimer = window.setTimeout(() => { passwordNotice.value = '' }, 8000)
}

/* ── 动作 ─────────────────────────────────────────────────── */
async function loadProfile(): Promise<void> {
  profileLoading.value = true
  try {
    const data = await getAccountProfile()
    if (!data) return
    username.value = data.username
    nickname.value = data.nickname
    draftNickname.value = data.nickname
    avatar.value = data.avatar
    sessionStartedAt.value = data.session_started_at ?? null
  } catch (error) {
    profileError.value = error instanceof Error ? error.message : '无法读取个人资料'
  } finally {
    profileLoading.value = false
  }
}

function pickAvatar(): void {
  if (avatarLocked.value) return
  avatarInput.value?.click()
}

/**
 * 头像不做草稿态：换图/移除立即落到账户库。
 * 提交时带上服务端已保存的昵称，避免覆盖用户正在编辑的昵称草稿。
 */
async function persistAvatar(next: string | null, remove = false): Promise<void> {
  if (avatarSaving.value) return
  const base = (nickname.value || username.value).trim()
  if (!base) {
    profileError.value = '账户资料尚未载入，请刷新页面后重试。'
    return
  }
  const previous = avatar.value
  avatarSaving.value = true
  profileError.value = ''
  profileNotice.value = ''
  avatar.value = next
  try {
    const result = await saveAccountProfile(base, next, remove)
    nickname.value = result.nickname
    draftNickname.value = draftNickname.value || result.nickname
    avatar.value = result.avatar
    flash('profile', remove ? '头像已移除' : '头像已更新')
  } catch (error) {
    avatar.value = previous
    profileError.value = error instanceof Error ? error.message : '头像保存失败，请重试。'
  } finally {
    avatarSaving.value = false
  }
}

function selectAvatar(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!['image/png', 'image/jpeg'].includes(file.type)) {
    profileError.value = '头像只支持 PNG 或 JPEG 图片。'
    return
  }
  if (file.size > 500000) {
    profileError.value = '头像大小不能超过 500 KB。'
    return
  }
  const reader = new FileReader()
  reader.onload = () => { void persistAvatar(String(reader.result)) }
  reader.onerror = () => { profileError.value = '头像读取失败，请重新选择。' }
  reader.readAsDataURL(file)
}

function removeAvatar(): void {
  if (!canRemoveAvatar.value) return
  void persistAvatar(null, true)
}

function resetProfileDraft(): void {
  draftNickname.value = nickname.value
  profileError.value = ''
}

async function saveProfile(): Promise<void> {
  if (!canSaveProfile.value) return
  profileSaving.value = true
  profileError.value = ''
  profileNotice.value = ''
  try {
    const result = await saveAccountProfile(draftNickname.value.trim(), null, false)
    nickname.value = result.nickname
    draftNickname.value = result.nickname
    avatar.value = result.avatar
    flash('profile', '昵称已保存')
  } catch (error) {
    profileError.value = error instanceof Error ? error.message : '保存失败，请重试。'
  } finally {
    profileSaving.value = false
  }
}

async function submitPassword(): Promise<void> {
  if (passwordSaving.value) return
  passwordError.value = ''
  passwordNotice.value = ''
  if (passwordHint.value) {
    passwordError.value = passwordHint.value
    return
  }
  passwordSaving.value = true
  try {
    await changeAccountPassword(oldPassword.value, newPassword.value)
    oldPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
    showOld.value = false
    showNew.value = false
    showConfirm.value = false
    flash('password', '密码已修改，其他设备上的登录状态已失效')
  } catch (error) {
    passwordError.value = error instanceof Error ? error.message : '修改密码失败。'
  } finally {
    passwordSaving.value = false
  }
}

const sessionLabel = computed(() => {
  if (!sessionStartedAt.value) return ''
  return new Date(sessionStartedAt.value * 1000).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
})

/* 头像占位用真名的前两个字符；没有头像时不能再塞一个 <img> 冒充已上传。 */
const avatarInitials = computed(() => (nickname.value || username.value || '?').trim().slice(0, 2).toUpperCase())

/* 「已配置」只看真实字段状态：没有任何字段时不猜，凭据态由服务端字段决定。 */
function providerConfigured(provider: ProviderCredential): boolean {
  if (provider.fields.length) return provider.fields.every(field => field.configured)
  return provider.session_credentials_configured === true
}

/* 改密表单是真表单（三行输入 + 强度 + 两次一致性），塞进 306px 右栏会挤坏，用弹窗承载。 */
const passwordDialog = ref<HTMLDialogElement | null>(null)
function openPasswordDialog(): void {
  passwordError.value = ''
  passwordNotice.value = ''
  passwordDialog.value?.showModal()
}
function closePasswordDialog(): void {
  passwordDialog.value?.close()
}
/* 与侧栏「退出登录」同一机制：交给 AuthGate 统一处理，不在这里偷偷跳路由。 */
function requestLogout(): void {
  window.dispatchEvent(new Event('kantoku-auth-logout'))
}

onMounted(() => {
  void loadProfile()
  void loadProviders()
  void loadTextQuota()
  void loadCreativeQuota()
  quotaTimer = window.setInterval(() => {
    if (!anyQuotaLoading.value) refreshAllQuotas()
  }, 60_000)
})
onBeforeUnmount(() => {
  window.clearTimeout(profileTimer)
  window.clearTimeout(passwordTimer)
  window.clearInterval(quotaTimer)
})
</script>

<template>
  <main class="page account-page px-page">
    <div class="px-wrap">
      <header class="px-pghd">
        <span class="px-pghd__kick">ACCOUNT · 本机单账户</span>
        <h1>个人中心</h1>
        <p>管理个人资料、服务额度与账户安全。</p>
      </header>

      <div class="px-cols">
        <div class="px-stack">
          <section class="px-sheet">
            <div class="px-sheet-hd"><span class="lbl">账户</span><span class="n">auth_owner</span></div>
            <div class="px-sheet-bd">
              <div class="px-ident">
                <div class="px-avabig">
                  <img v-if="avatarSource" :src="avatarSource" alt="当前头像">
                  <span v-else>{{ avatarInitials }}</span>
                </div>
                <div class="px-idline">
                  <strong>{{ nickname || username || '—' }}</strong>
                  <span class="handle">{{ username ? `@${username}` : '—' }}</span>
                  <span class="px-idchip"><i />已登录 · 本机</span>
                </div>
              </div>
              <div class="px-kvlist">
                <div class="px-kvrow"><span>账户类型</span><b>本机独立账户</b></div>
                <div class="px-kvrow"><span>登录方式</span><b>用户名 + 密码</b></div>
                <div class="px-kvrow"><span>本次登录</span><b>{{ sessionLabel || (profileLoading ? '正在读取账户…' : '会话已建立') }}</b></div>
              </div>
            </div>
          </section>

          <section class="px-sheet">
            <div class="px-sheet-hd"><span class="lbl">个人资料</span><span class="n">auth_profile</span></div>
            <div class="px-sheet-bd">
              <p class="px-note">头像选择后立即保存；昵称修改后点击「保存昵称」生效。</p>

              <div class="px-field">
                <span class="px-rowlab">头像</span>
                <div class="px-frow">
                  <button class="px-btn" type="button" :disabled="avatarLocked" @click="pickAvatar">
                    {{ avatarSaving ? '保存中…' : (avatar ? '更换头像' : '选择头像') }}
                  </button>
                  <button v-if="avatar" class="px-btn is-danger" type="button" :disabled="avatarLocked" @click="removeAvatar">移除</button>
                  <input ref="avatarInput" class="visually-hidden" type="file" accept="image/png,image/jpeg" @change="selectAvatar">
                  <span class="px-rowhint is-inline">PNG / JPEG · ≤ 500 KB · 选择后立即保存</span>
                </div>
              </div>

              <div class="px-field">
                <span class="px-rowlab">用户名</span>
                <input class="px-inp" :value="username || '—'" readonly aria-label="用户名（不可修改）">
                <span class="px-rowhint">登录凭据，不可修改</span>
              </div>

              <div class="px-field">
                <label class="px-rowlab" for="acct-nickname">昵称</label>
                <input
                  id="acct-nickname"
                  v-model="draftNickname"
                  class="px-inp"
                  maxlength="32"
                  autocomplete="nickname"
                  placeholder="请输入昵称"
                  :disabled="profileSaving"
                >
                <span class="px-rowhint">{{ draftNickname.length }} / 32 · 昵称保存后立即生效</span>
              </div>

              <p v-if="profileError" class="px-status is-err" role="alert">{{ profileError }}</p>
              <p v-else-if="profileNotice" class="px-status is-ok" role="status">{{ profileNotice }}</p>

              <div class="px-frow px-frow--end">
                <button v-if="profileDirty" class="px-btn" type="button" :disabled="profileSaving || avatarSaving" @click="resetProfileDraft">放弃修改</button>
                <button class="px-btn is-gold" type="button" :disabled="!canSaveProfile" @click="saveProfile">{{ profileSaving ? '保存中…' : '保存昵称' }}</button>
              </div>
            </div>
          </section>

          <section id="account-providers" class="px-sheet">
            <div class="px-sheet-hd"><span class="lbl">模型与供应商</span><span class="n">凭据只存本机</span></div>
            <div class="px-sheet-bd">
              <p class="px-note">已有凭据仅显示配置状态，不回显密钥；暂不支持页面修改的凭据请在本机配置文件中管理。</p>
              <p v-if="providerLoading" class="px-rowhint">正在读取配置状态…</p>
              <p v-if="providerError" class="px-status is-err" role="alert">{{ providerError }}</p>
              <p v-if="videoCredentialNotice" class="px-status is-ok" role="status">{{ videoCredentialNotice }}</p>
              <p v-if="textCredentialNotice" class="px-status is-ok" role="status">{{ textCredentialNotice }}</p>

              <div v-for="provider in providers" :key="provider.id" class="px-provbox">
                <h3>
                  {{ provider.label }}
                  <span class="px-tag" :class="providerConfigured(provider) ? 'is-ok' : 'is-warn'">{{ providerConfigured(provider) ? '已配置' : '未配置' }}</span>
                </h3>
                <div v-for="field in provider.fields" :key="field.id" class="px-kvrow">
                  <span>{{ field.label }}</span>
                  <b>{{ field.configured ? '已配置' : '未配置' }}</b>
                </div>
                <form v-if="provider.id === 'text' && provider.editable" class="px-provform" @submit.prevent="saveTextCredentials">
                  <label>API Key<input v-model="textApiKey" class="px-inp" type="password" autocomplete="off" spellcheck="false" :placeholder="providerConfigured(provider) ? '已配置，输入新密钥可修改' : '请输入密钥'" required :disabled="textCredentialSaving"></label>
                  <div class="px-frow px-frow--end"><button class="px-btn is-gold" type="submit" :disabled="textCredentialSaving || !textApiKey.trim()">{{ textCredentialSaving ? '保存中…' : '保存文字凭据' }}</button></div>
                </form>
                <p v-if="provider.id === 'video' && provider.session_credentials_configured" class="px-rowhint">当前服务进程已配置视频凭据，重启后失效。</p>
                <form v-if="provider.id === 'video' && provider.credentials_configurable && !provider.session_credentials_configured" class="px-provform" @submit.prevent="saveVideoCredentials">
                  <p class="px-rowhint">小云雀视频服务凭据仅在当前服务进程有效，重启后需重新填写；提交后不显示密钥。</p>
                  <label>Access Key ID<input v-model="videoAccessKey" type="text" autocomplete="off" spellcheck="false" required :disabled="videoCredentialSaving"></label>
                  <label>Secret Access Key<input v-model="videoSecretKey" type="password" autocomplete="off" spellcheck="false" required :disabled="videoCredentialSaving"></label>
                  <div class="px-frow px-frow--end">
                    <button class="px-btn is-gold" type="submit" :disabled="videoCredentialSaving || !videoAccessKey || !videoSecretKey">{{ videoCredentialSaving ? '设置中…' : '设置本次视频凭据' }}</button>
                  </div>
                </form>
              </div>
            </div>
          </section>
        </div>

        <div class="px-stack">
          <section class="px-sheet">
            <div class="px-sheet-hd"><span class="lbl">数据范围</span><span class="n">本页能改什么</span></div>
            <div class="px-sheet-bd">
              <p class="px-note">管理账户资料、供应商凭据和服务额度。</p>
              <div class="px-kvrow"><span>账户资料</span><b><ShieldCheck :size="11" :stroke-width="2.2" /> 昵称与头像 · 写入 auth_profile</b></div>
              <div class="px-kvrow"><span>登录凭据</span><b><Lock :size="11" :stroke-width="2.2" /> 用户名与密码 · 改密后其他会话失效</b></div>
              <div class="px-kvrow"><span>创作数据</span><b><UserRound :size="11" :stroke-width="2.2" /> 只读 · 不受本页操作影响</b></div>
            </div>
          </section>

          <section class="px-sheet">
            <div class="px-sheet-hd">
              <span class="lbl">服务额度</span>
              <button class="px-btn px-btn--mini" type="button" :disabled="anyQuotaLoading" @click="refreshAllQuotas">
                <RefreshCw :size="10" :stroke-width="2.4" :class="{ pxspin: anyQuotaLoading }" />刷新
              </button>
            </div>
            <div class="px-sheet-bd">
              <div class="px-quota">
                <div class="px-quota-hd">
                  <span>文字额度</span>
                  <button class="px-iconbtn" type="button" aria-label="刷新文字额度" :disabled="textQuota.loading" @click="loadTextQuota">
                    <RefreshCw :size="11" :stroke-width="2.4" :class="{ pxspin: textQuota.loading }" />
                  </button>
                </div>
                <p class="px-quota-src">DeepSeek · 账户可用余额</p>
                <p v-if="textQuota.loading" class="px-quota-state">正在查询…</p>
                <template v-else-if="textQuota.message">
                  <p class="px-quota-state is-warn"><TriangleAlert :size="11" :stroke-width="2.4" />{{ quotaStatus(textQuota) }}</p>
                  <p class="px-quota-detail">{{ textQuota.message }}</p>
                  <button v-if="needsCredential(textQuota)" class="px-link" type="button" @click="openProviderSettings">查看配置状态</button>
                  <button v-else-if="retryable(textQuota)" class="px-link" type="button" :disabled="textQuota.loading" @click="loadTextQuota">重试</button>
                </template>
                <p v-else class="px-quota-amt">
                  <b v-for="item in textQuota.balances" :key="item.currency">{{ money(item) }}</b>
                </p>
                <p class="px-quota-time">{{ textQuota.updatedAt ? `${textQuota.message ? '上次成功' : '更新于'} ${textQuota.updatedAt}` : ' ' }}</p>
              </div>

              <div class="px-quota">
                <div class="px-quota-hd">
                  <span>视频额度</span>
                  <button class="px-iconbtn" type="button" aria-label="刷新视频额度" :disabled="creativeQuota.loading" @click="loadCreativeQuota">
                    <RefreshCw :size="11" :stroke-width="2.4" :class="{ pxspin: creativeQuota.loading }" />
                  </button>
                </div>
                <p class="px-quota-src">火山引擎 · 视频服务凭据</p>
                <p v-if="creativeQuota.loading" class="px-quota-state">正在查询…</p>
                <template v-else-if="creativeQuota.message">
                  <p class="px-quota-state is-warn"><TriangleAlert :size="11" :stroke-width="2.4" />{{ quotaStatus(creativeQuota) }}</p>
                  <p class="px-quota-detail">{{ creativeQuota.message }}</p>
                  <button v-if="needsCredential(creativeQuota)" class="px-link" type="button" @click="openProviderSettings">查看配置状态</button>
                  <button v-else-if="retryable(creativeQuota)" class="px-link" type="button" :disabled="creativeQuota.loading" @click="loadCreativeQuota">重试</button>
                </template>
                <p v-else class="px-quota-amt">
                  <b v-for="item in creativeQuota.balances" :key="item.currency">{{ money(item) }}</b>
                </p>
                <p class="px-quota-time">{{ creativeQuota.updatedAt ? `${creativeQuota.message ? '上次成功' : '更新于'} ${creativeQuota.updatedAt}` : ' ' }}</p>
              </div>

              <div class="px-quota">
                <div class="px-quota-hd"><span>图片额度</span></div>
                <p class="px-quota-src">图片模型 · 账户余额</p>
                <p class="px-quota-state is-warn"><TriangleAlert :size="11" :stroke-width="2.4" />暂不可查询</p>
                <p class="px-quota-detail">当前图片服务未接入可使用该凭据查询余额的接口。</p>
              </div>
            </div>
          </section>

          <section class="px-sheet">
            <div class="px-sheet-hd"><span class="lbl">账户安全</span><span class="n">本机校验</span></div>
            <div class="px-sheet-bd">
              <p class="px-note">密码为 8–128 个字符，服务端只保存加盐哈希。改密需先验证旧密码，修改后其他设备上的登录状态会失效。</p>
              <button class="px-btn is-paper px-btn--block" type="button" @click="openPasswordDialog">修改密码</button>
              <button class="px-btn is-danger px-btn--block" type="button" @click="requestLogout">退出登录</button>
            </div>
          </section>
        </div>
      </div>

      <p class="px-pgnote">本页账户资料修改不会影响创作数据与资产文件；服务额度查询失败不代表余额为 0。</p>
    </div>

    <dialog ref="passwordDialog" class="px-modal" aria-label="修改密码">
      <div class="px-modal-hd">
        <span class="lbl">修改密码</span>
        <button class="px-modal-x" type="button" aria-label="关闭修改密码" @click="closePasswordDialog">
          <CircleCheck :size="12" :stroke-width="0" style="display:none" />✕
        </button>
      </div>
      <div class="px-modal-bd">
        <div class="px-field">
          <label class="px-rowlab" for="acct-old">原密码</label>
          <div class="px-inp-grp">
            <input
              id="acct-old"
              v-model="oldPassword"
              class="px-inp"
              :type="showOld ? 'text' : 'password'"
              autocomplete="current-password"
              :disabled="passwordSaving"
            >
            <button class="px-inp-toggle" type="button" :aria-label="showOld ? '隐藏原密码' : '显示原密码'" @click="showOld = !showOld">
              <EyeOff v-if="showOld" :size="13" /><Eye v-else :size="13" />
            </button>
          </div>
        </div>

        <div class="px-field">
          <label class="px-rowlab" for="acct-new">新密码</label>
          <div class="px-inp-grp">
            <input
              id="acct-new"
              v-model="newPassword"
              class="px-inp"
              :type="showNew ? 'text' : 'password'"
              autocomplete="new-password"
              placeholder="至少 8 个字符"
              :disabled="passwordSaving"
            >
            <button class="px-inp-toggle" type="button" :aria-label="showNew ? '隐藏新密码' : '显示新密码'" @click="showNew = !showNew">
              <EyeOff v-if="showNew" :size="13" /><Eye v-else :size="13" />
            </button>
          </div>
          <div v-if="newPassword" class="px-strength" :class="passwordStrength.tone">
            <span v-for="index in 3" :key="index" class="px-strength-seg" :class="{ on: index <= passwordStrength.segments }"></span>
            <small>强度 {{ passwordStrength.label }} · {{ newPassword.length }} 个字符</small>
          </div>
        </div>

        <div class="px-field">
          <label class="px-rowlab" for="acct-confirm">确认新密码</label>
          <div class="px-inp-grp">
            <input
              id="acct-confirm"
              v-model="confirmPassword"
              class="px-inp"
              :type="showConfirm ? 'text' : 'password'"
              autocomplete="new-password"
              :disabled="passwordSaving"
            >
            <button class="px-inp-toggle" type="button" :aria-label="showConfirm ? '隐藏确认密码' : '显示确认密码'" @click="showConfirm = !showConfirm">
              <EyeOff v-if="showConfirm" :size="13" /><Eye v-else :size="13" />
            </button>
          </div>
          <p v-if="passwordMismatch" class="px-status is-err">两次输入的新密码不一致</p>
          <p v-else-if="confirmPassword" class="px-status is-ok">两次输入一致</p>
        </div>
      </div>
      <div class="px-modal-ft">
        <p v-if="passwordError" class="px-status is-err" role="alert">{{ passwordError }}</p>
        <p v-else-if="passwordNotice" class="px-status is-ok" role="status">
          <Check :size="11" :stroke-width="2.6" />{{ passwordNotice }}
        </p>
        <p v-else class="px-rowhint">修改后，其他设备上的登录状态将失效</p>
        <div class="px-frow px-frow--end">
          <button class="px-btn" type="button" :disabled="passwordSaving" @click="closePasswordDialog">取消</button>
          <button class="px-btn is-gold" type="button" :disabled="passwordSaving" @click="submitPassword">{{ passwordSaving ? '提交中…' : '修改密码' }}</button>
        </div>
      </div>
    </dialog>
  </main>
</template>
