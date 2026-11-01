<script setup lang="ts">
import { computed, ref } from 'vue'

/**
 * 登录 / 创建账号 —— 「存档台」。
 *
 * 交互与数据契约与原实现完全一致（AuthGate 传 registered / submitting / error，
 * 这里只 emit credentials），换的是视觉：铁牌存档台 + 奶油纸表单。
 * 文案保持产品语义（登录 / 创建账号），存档隐喻只出现在铭牌与 kicker 上。
 */
const props = withDefaults(defineProps<{
  registered: boolean
  submitting?: boolean
  error?: string
}>(), { submitting: false, error: '' })
const emit = defineEmits<{
  submit: [details: { username: string; password: string }]
  continueLocal: []
}>()
const username = ref('')
const password = ref('')
const confirmation = ref('')
const canSubmit = computed(() => username.value.length >= 3 && password.value.length >= 8
  && (props.registered || password.value === confirmation.value) && !props.submitting)
const mismatch = computed(() => !props.registered && confirmation.value.length > 0
  && password.value !== confirmation.value)

function submit(): void {
  if (canSubmit.value) emit('submit', { username: username.value, password: password.value })
}
</script>

<template>
  <main class="login-page">
    <div class="lg-bar">
      <b>KANTOKU</b>
      <span>WORLD 1-1 · 存档室</span>
      <i class="lg-sep" />
      <span class="lg-live"><i />{{ registered ? '已有存档 · 本机' : '尚未建档 · 本机' }}</span>
      <span class="lg-right">{{ registered ? '1 存档位' : '0 存档位' }}</span>
    </div>

    <div class="lg-wrap">
      <div class="lg-reader">
        <i class="lg-bolt lg-bolt--tl" />
        <i class="lg-bolt lg-bolt--tr" />
        <i class="lg-bolt lg-bolt--bl" />
        <i class="lg-bolt lg-bolt--br" />

        <div class="lg-plate">
          存档台
          <span>KT-01 · 127.0.0.1</span>
        </div>

        <div class="lg-box">
          <div class="lg-box-hd">
            <i />
            <span>{{ registered ? '存档 01 已插入' : '存档 02 空白' }}</span>
            <b>{{ registered ? '只读' : '可写入' }}</b>
          </div>
          <div class="lg-hr" />

          <form class="lg-form" @submit.prevent="submit">
            <p class="lg-kicker">{{ registered ? 'READ SAVE 01' : 'WRITE SAVE 02' }}</p>

            <label class="lg-field" for="login-username">
              <span>用户名</span>
              <input id="login-username" v-model.trim="username" autocomplete="username"
                maxlength="32" placeholder="至少 3 个字符">
            </label>
            <label class="lg-field" for="login-password">
              <span>密码</span>
              <input id="login-password" v-model="password" type="password"
                :autocomplete="registered ? 'current-password' : 'new-password'"
                maxlength="128" placeholder="至少 8 个字符">
            </label>
            <template v-if="!registered">
              <label class="lg-field" for="login-confirmation">
                <span>确认密码</span>
                <input id="login-confirmation" v-model="confirmation" type="password"
                  autocomplete="new-password" maxlength="128" placeholder="再次输入密码">
              </label>
              <p v-if="mismatch" class="lg-error" role="alert">两次输入的密码不一致。</p>
              <p class="lg-hint">目前暂不支持找回密码，请妥善保存。</p>
            </template>

            <p v-if="error" class="lg-error" role="alert">{{ error }}</p>

            <button class="lg-go" type="submit" :disabled="!canSubmit">
              {{ submitting ? '请稍候…' : registered ? '登录' : '创建账号' }}
            </button>
          </form>
        </div>

        <div class="lg-meter">
          <i />
          <span>{{ registered ? '1 / 1' : '0 / 1' }}</span>
          <span class="lg-right">LOCAL · 数据不出这台机器</span>
        </div>
      </div>

      <button v-if="!registered" type="button" class="login-local" @click="emit('continueLocal')">
        暂不创建，继续使用本机工作台 →
      </button>
    </div>

    <div class="lg-foot">
      <span>用户名 ≥ 3 · 密码 ≥ 8</span>
      <i class="lg-sep" />
      <span>凭据只存本机，不联网校验</span>
      <span class="lg-right">Kantoku Core · 本机</span>
    </div>
  </main>
</template>

<style scoped>
.login-page {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  height: 100dvh;
  overflow: hidden;
}

/* ---- 顶 / 底铭牌 ---- */
.lg-bar,
.lg-foot {
  position: relative;
  z-index: 2;
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: var(--space-3);
  padding: 0 var(--space-5);
  background: var(--paper);
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: .16em;
}

.lg-bar {
  height: 44px;
  border-bottom: 4px solid var(--ink);
  box-shadow: 0 4px 0 rgba(20, 20, 20, .12);
}

.lg-bar b { color: var(--brick); font-size: 11px; letter-spacing: .26em; }
.lg-sep { display: block; flex: 0 0 auto; width: 1px; height: 12px; background: rgba(20, 20, 20, .22); }
.lg-live { display: inline-flex; align-items: center; gap: 6px; color: var(--success); }
.lg-live i { display: block; width: 7px; height: 7px; background: currentColor; }
.lg-right { margin-left: auto; }

.lg-foot {
  height: 38px;
  border-top: 4px solid var(--ink);
  box-shadow: 0 -4px 0 rgba(20, 20, 20, .08);
}

/* ---- 存档台主体 ---- */
.lg-wrap {
  position: relative;
  z-index: 2;
  display: grid;
  flex: 1 1 auto;
  align-content: end;
  justify-items: center;
  gap: var(--space-4);
  min-height: 0;
  padding: var(--space-6) var(--space-5) 96px;
  overflow-y: auto;
}

.lg-reader {
  position: relative;
  width: min(560px, 100%);
  padding: 16px 18px 14px;
  border: 4px solid var(--ink);
  background-image:
    radial-gradient(circle at 50% 50%, rgba(255, 206, 140, .11) 2px, transparent 2.5px),
    linear-gradient(180deg, #4a3320, #33220f 56%, #241608);
  background-size: 16px 16px, auto;
  box-shadow:
    inset 0 5px 0 rgba(255, 206, 140, .24),
    inset 0 -8px 0 rgba(0, 0, 0, .6),
    10px 10px 0 rgba(0, 0, 0, .45);
}

.lg-bolt {
  position: absolute;
  width: 8px;
  height: 8px;
  background: #c9a227;
  border: 2px solid #141414;
}
.lg-bolt--tl { top: 6px; left: 6px; }
.lg-bolt--tr { top: 6px; right: 6px; }
.lg-bolt--bl { bottom: 6px; left: 6px; }
.lg-bolt--br { bottom: 6px; right: 6px; }

.lg-plate {
  position: relative;
  display: inline-flex;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: 12px;
  padding: 5px 10px 4px;
  border: 3px solid var(--ink);
  background: linear-gradient(180deg, var(--gold-light), var(--gold) 55%, var(--gold-dark));
  color: #3a2408;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .18em;
  box-shadow: 4px 4px 0 rgba(0, 0, 0, .45);
}

.lg-plate span { font-size: 9px; font-weight: 400; letter-spacing: .12em; opacity: .78; }

.lg-box {
  border: 3px solid var(--ink);
  background: var(--paper);
  box-shadow: inset 0 -4px 0 var(--paper-2);
}

.lg-box-hd {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 6px 10px 5px;
  border-bottom: 3px solid var(--ink);
  background: var(--ink-2);
  color: var(--paper);
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: .16em;
}

.lg-box-hd i { display: block; width: 7px; height: 7px; background: #7fe08a; }
.lg-box-hd b { margin-left: auto; font-weight: 400; color: var(--gold); }

.lg-hr { height: 3px; background: repeating-linear-gradient(90deg, var(--paper-3) 0 6px, transparent 6px 12px); }

.lg-form { display: grid; gap: 10px; padding: 12px 12px 14px; }

.lg-kicker {
  margin: 0;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: .22em;
}

.lg-field { display: grid; gap: 4px; }
.lg-field > span {
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: .12em;
}

.lg-field input {
  width: 100%;
  min-width: 0;
  height: 36px;
  padding: 0 10px;
  border: 3px solid var(--ink);
  border-radius: 0;
  background: var(--paper);
  color: var(--text-primary);
  font-family: var(--font-ui);
  font-size: 13px;
  font-weight: 600;
  outline: none;
  box-shadow: inset 0 -3px 0 var(--paper-2);
}

.lg-field input:focus { box-shadow: inset 0 -3px 0 var(--gold); }
.lg-field input::placeholder { color: var(--text-muted); font-weight: 400; }

.lg-go {
  height: 40px;
  margin-top: 4px;
  border: 3px solid var(--ink);
  border-radius: 0;
  background: linear-gradient(180deg, var(--gold-light), var(--gold) 55%, var(--gold-dark));
  color: #241608;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .18em;
  box-shadow: 0 5px 0 var(--gold-dark), 5px 5px 0 rgba(0, 0, 0, .4);
  transition: transform var(--duration-fast) var(--ease-pixel), box-shadow var(--duration-fast) var(--ease-pixel);
}

.lg-go:hover:not(:disabled) { background: linear-gradient(180deg, #fff0a8, var(--gold-light) 55%, var(--gold)); }
.lg-go:active:not(:disabled) { transform: translateY(3px); box-shadow: 0 2px 0 var(--gold-dark), 2px 2px 0 rgba(0, 0, 0, .4); }
.lg-go:disabled { opacity: .55; cursor: not-allowed; background: var(--paper-3); box-shadow: 0 5px 0 #b9ad94; color: #6b5f49; }

.lg-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  padding: 6px 9px;
  border: 3px solid var(--danger);
  background: var(--danger-soft);
  color: var(--danger);
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: .06em;
}

.lg-hint {
  margin: 0;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: .08em;
}

.lg-meter {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 3px solid rgba(255, 206, 140, .22);
  color: #c9a97c;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: .14em;
}

.lg-meter i {
  display: block;
  width: 10px;
  height: 10px;
  background: var(--gold);
  border: 2px solid #141414;
}

.login-local {
  padding: 8px 14px;
  border: 3px solid var(--ink);
  border-radius: 0;
  background: rgba(255, 253, 245, .94);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: .1em;
  box-shadow: 4px 4px 0 rgba(20, 20, 20, .35);
  transition: transform var(--duration-fast) var(--ease-pixel), box-shadow var(--duration-fast) var(--ease-pixel);
}

.login-local:hover { background: var(--paper); }
.login-local:active { transform: translate(2px, 2px); box-shadow: 2px 2px 0 rgba(20, 20, 20, .35); }

@media (max-width: 640px) {
  .lg-bar span:nth-of-type(2),
  .lg-live { display: none; }
  .lg-wrap { padding: var(--space-4) var(--space-3) 72px; }
}
</style>
