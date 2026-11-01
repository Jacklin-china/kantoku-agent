import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, h, nextTick } from 'vue'

// 只替换 HTTP 边界与浏览器宿主；AccountView 本体按真实源码编译执行。
const moduleUrl = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`

const coreUrl = moduleUrl(`
  export const state = {
    profile: { username: 'user1', nickname: '林九日', avatar: null, session_started_at: 1793260000 },
    profileCalls: 0, saveCalls: [], failSave: null, failProfile: null,
    providers: [], textBalanceCalls: 0, creativeBalanceCalls: 0, videoCredentials: [], textCredentials: [], failTextCredential: false,
  }
  export async function getAccountProfile() {
    state.profileCalls += 1
    if (state.failProfile) throw new Error(state.failProfile)
    return { ...state.profile }
  }
  export async function saveAccountProfile(nickname, avatar, removeAvatar = false) {
    state.saveCalls.push({ nickname, avatar, removeAvatar })
    if (state.failSave) throw new Error(state.failSave)
    state.profile = {
      ...state.profile, nickname,
      avatar: removeAvatar ? null : (avatar ?? state.profile.avatar),
    }
    return { ...state.profile }
  }
  export async function getTextBalance() {
    state.textBalanceCalls += 1
    return { platform: 'DeepSeek', balances: [], unavailable: { code: 'credential_missing', message: '未配置密钥' } }
  }
  export async function getCreativeBalance() {
    state.creativeBalanceCalls += 1
    return { platform: '火山引擎', balances: [{ currency: 'CNY', amount: '0.87' }] }
  }
  export async function getProviderCredentials() { return { providers: state.providers } }
  export async function saveProviderCredentials(provider, fields) {
    state.textCredentials.push({ provider, fields })
    if (state.failTextCredential) throw new Error('保存失败，请重试')
    return { providers: state.providers, message: '凭据已保存' }
  }
  export async function configureVideoCredentials(accessKey, secretKey) {
    state.videoCredentials.push({ accessKey, secretKey })
    const video = state.providers.find(p => p.id === 'video')
    if (video) { video.credentials_configurable = false; video.session_credentials_configured = true }
    return { configured: true }
  }
  export async function changeAccountPassword() { return { updated: true } }
`)

// v-model 走运行时的 vModelText，它在 Node 里依赖真实 document；
// 这里换成一个只在测试里成立的最小实现，仍然通过 onUpdate:modelValue 回写。
const vueUrl = moduleUrl(`
  export * from ${JSON.stringify(import.meta.resolve('vue'))}
  export const vModelText = {
    created(el, { value }) { el.value = value ?? '' },
    mounted(el, { value, modifiers }, vnode) {
      el.value = value ?? ''
      el.addEventListener('input', (event) => {
        const raw = event.target.value
        const next = modifiers?.trim ? raw.trim() : raw
        vnode.props?.['onUpdate:modelValue']?.(next)
      })
    },
    beforeUpdate(el, { value }) { el.value = value ?? '' },
  }
`)

const iconUrl = moduleUrl(`
  const icon = () => null
  export const Check = icon, CircleCheck = icon, Eye = icon, EyeOff = icon, Lock = icon,
    RefreshCw = icon, ShieldCheck = icon, TriangleAlert = icon, UserRound = icon
`)
const routerUrl = moduleUrl('export const navigate = () => {}')

const modules = new Map()
function loadModule(url) {
  if (url.pathname.endsWith('/services/core.ts')) return coreUrl
  if (modules.has(url.href)) return modules.get(url.href)
  const source = readFileSync(url, 'utf8')
  const script = url.pathname.endsWith('.vue')
    ? compileScript(parse(source).descriptor, { id: url.href, inlineTemplate: true }).content : source
  let code = ts.transpileModule(script, { compilerOptions: { module: ts.ModuleKind.ES2022 } }).outputText
  code = code.replace(/from (["'])([^"']+)\1/g, (_match, _quote, path) => {
    const target = path === 'lucide-vue-next' ? iconUrl
      : path === 'vue' ? vueUrl
      : path === '../router' ? routerUrl
      : path.startsWith('.')
        ? loadModule(new URL(/\.(vue|ts)$/.test(path) ? path : path + '.ts', url))
        : import.meta.resolve(path)
    return `from ${JSON.stringify(target)}`
  })
  const result = moduleUrl(code)
  modules.set(url.href, result)
  return result
}

// flash() 走 window 定时器；测试不依赖自动清空，用惰性句柄避免遗留计时器干扰后续用例。
let intervalCallback
let intervalCleared = false
globalThis.window = {
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: (fn, delay) => { assert.equal(delay, 60_000); intervalCallback = fn; intervalCleared = false; return 42 },
  clearInterval: (id) => { if (id === 42) intervalCleared = true },
}
globalThis.FileReader = class {
  readAsDataURL(file) {
    this.result = file.dataUrl
    queueMicrotask(() => this.onload?.())
  }
}

const { default: AccountView } = await import(loadModule(new URL('../src/views/AccountView.vue', import.meta.url)))
const { state } = await import(coreUrl)

const node = tag => ({
  tag, tagName: tag.toUpperCase(), props: {}, children: [], parent: null, listeners: {}, value: '',
  addEventListener(name, handler) { this.listeners[name] = handler },
  removeEventListener(name) { delete this.listeners[name] },
  getRootNode() { return { activeElement: null } },
  focus() {},
})
const remove = child => {
  if (!child.parent) return
  const index = child.parent.children.indexOf(child)
  if (index >= 0) child.parent.children.splice(index, 1)
}
const renderer = createRenderer({
  createElement: node,
  createText: text => Object.assign(node('#text'), { text }),
  createComment: text => Object.assign(node('#comment'), { text }),
  setText(child, text) { child.text = text },
  setElementText(child, text) { child.text = text; child.children = [] },
  patchProp(child, key, _old, value) { child.props[key] = value; if (key === 'value') child.value = value },
  insert(child, parent, anchor) {
    remove(child)
    const index = anchor ? parent.children.indexOf(anchor) : -1
    parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
    child.parent = parent
  },
  remove,
  parentNode: child => child.parent,
  nextSibling: child => child.parent?.children[child.parent.children.indexOf(child) + 1] ?? null,
})
const all = child => [child, ...child.children.flatMap(all)]
const find = (root, pred) => all(root).find(pred)
const textOf = child => all(child).map(item => item.text ?? '').join('')

const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=='
const settle = async () => { for (let i = 0; i < 6; i += 1) { await nextTick(); await new Promise(r => setTimeout(r, 0)) } }

async function mount() {
  const root = node('root')
  const app = renderer.createApp({ render: () => h(AccountView) })
  app.mount(root)
  await settle()
  return { root, app }
}

function fileInput(root) {
  return find(root, n => n.tag === 'input' && n.props.type === 'file')
}

async function chooseAvatar(root, dataUrl, size = 4096) {
  const input = fileInput(root)
  const file = { type: 'image/png', size, dataUrl }
  input.files = [file]
  input.props.onChange({ target: input })
  await settle()
}

test('选择头像后立即提交保存，不需要再点按钮', async () => {
  state.profile = { username: 'user1', nickname: '林九日', avatar: null, session_started_at: 1793260000 }
  state.saveCalls.length = 0
  state.failSave = null
  const { root, app } = await mount()
  assert.equal(find(root, n => n.tag === 'img'), undefined)

  await chooseAvatar(root, PNG)

  assert.deepEqual(state.saveCalls, [{ nickname: '林九日', avatar: PNG, removeAvatar: false }])
  assert.equal(find(root, n => n.tag === 'img')?.props.src, PNG)
  app.unmount()
})

test('切换页面重新挂载后沿用已保存的头像', async () => {
  state.profile = { ...state.profile, avatar: null }
  state.saveCalls.length = 0
  const first = await mount()
  await chooseAvatar(first.root, PNG)
  assert.equal(state.profile.avatar, PNG)

  // 未点任何按钮，换一个全新实例重新挂载即等于「切走再回来」。
  const second = await mount()
  assert.equal(find(second.root, n => n.tag === 'img')?.props.src, PNG)
  assert.equal(state.saveCalls.length, 1)
})

test('移除头像立即提交 remove_avatar 并回到占位图', async () => {
  state.profile = { ...state.profile, avatar: PNG }
  state.saveCalls.length = 0
  const { root } = await mount()
  assert.equal(find(root, n => n.tag === 'img')?.props.src, PNG)

  const removeButton = all(root).find(n => n.tag === 'button' && textOf(n).trim() === '移除')
  assert.ok(removeButton, '有头像时应出现移除入口')
  removeButton.props.onClick()
  await settle()

  assert.deepEqual(state.saveCalls, [{ nickname: '林九日', avatar: null, removeAvatar: true }])
  assert.equal(find(root, n => n.tag === 'img'), undefined)
})

test('保存失败时预览回滚', async () => {
  state.profile = { ...state.profile, avatar: null }
  state.saveCalls.length = 0
  state.failSave = '头像文件过大'
  const { root, app } = await mount()

  await chooseAvatar(root, PNG)

  assert.equal(find(root, n => n.tag === 'img'), undefined, '失败后不能留下未保存的预览')
  assert.match(textOf(root), /头像文件过大/)
  state.failSave = null
  app.unmount()
})

test('昵称单独保存时不动头像', async () => {
  state.profile = { ...state.profile, nickname: '林九日', avatar: PNG }
  state.saveCalls.length = 0
  const { root, app } = await mount()

  const saveButton = all(root).find(n => n.tag === 'button' && textOf(n).trim() === '保存昵称')
  assert.ok(saveButton, '应有保存昵称按钮')
  assert.equal(saveButton.props.disabled, true)

  const nicknameInput = find(root, n => n.props.id === 'acct-nickname')
  assert.ok(nicknameInput, '应有昵称输入框')
  nicknameInput.value = '新昵称'
  nicknameInput.listeners.input({ target: nicknameInput })
  await settle()
  assert.equal(saveButton.props.disabled, false)

  saveButton.props.onClick()
  await settle()

  assert.deepEqual(state.saveCalls, [{ nickname: '新昵称', avatar: null, removeAvatar: false }])
  assert.equal(find(root, n => n.tag === 'img')?.props.src, PNG)
  app.unmount()
})

test('凭据只展示配置状态，不显示密钥或可误用的保存按钮', async () => {
  state.providers = [{ id: 'video', label: '视频服务', fields: [
    { id: 'access_key', label: 'API ID', configured: true },
    { id: 'secret_key', label: 'API Key', configured: true },
  ] }]
  const { root, app } = await mount()
  assert.match(textOf(root), /视频服务/)
  assert.match(textOf(root), /不回显密钥/)
  assert.equal(find(root, n => n.props.id === 'provider-video-secret_key'), undefined)
  assert.equal(all(root).find(n => n.tag === 'button' && textOf(n).trim() === '保存配置'), undefined)
  app.unmount()
  state.providers = []
})

test('仅在服务支持时设置进程内视频凭据，并于提交后清空输入', async () => {
  state.providers = [{ id: 'video', label: '视频模型', fields: [], credentials_configurable: true, session_credentials_configured: false }]
  state.videoCredentials.length = 0
  const { root, app } = await mount()
  const inputs = all(root).filter(n => n.tag === 'input')
  const secret = inputs.find(n => n.props.type === 'password' && n.props.autocomplete === 'off')
  const id = inputs.find(n => n.props.type === 'text' && n.props.autocomplete === 'off')
  assert.ok(id && secret)
  id.value = 'test-id-12345'; id.listeners.input({ target: id })
  secret.value = 'test-key-12345'; secret.listeners.input({ target: secret })
  await settle()
  const form = find(root, n => n.tag === 'form' && textOf(n).includes('设置本次视频凭据'))
  assert.ok(form)
  form.props.onSubmit({ preventDefault() {}, stopPropagation() {} })
  await settle()
  assert.deepEqual(state.videoCredentials, [{ accessKey: 'test-id-12345', secretKey: 'test-key-12345' }])
  assert.equal(find(root, n => n.tag === 'form' && textOf(n).includes('设置本次视频凭据')), undefined)
  assert.match(textOf(root), /重启后失效/)
  assert.match(textOf(root), /图片额度/)
  assert.match(textOf(root), /暂不可查询/)
  app.unmount()
  state.providers = []
})

test('文字密钥仅在后端支持时可编辑，提交后清空并刷新余额', async () => {
  state.providers = [{ id: 'text', label: '文字模型', editable: true, fields: [{ id: 'api_key', label: 'API Key', configured: true }] }]
  state.textCredentials.length = 0
  state.textBalanceCalls = 0
  const { root, app } = await mount()
  const form = find(root, n => n.tag === 'form' && textOf(n).includes('保存文字凭据'))
  const input = find(form, n => n.tag === 'input')
  assert.equal(input.props.type, 'password')
  assert.match(input.props.placeholder, /已配置/)
  input.value = 'test-new-key-12345'; input.listeners.input({ target: input })
  await settle()
  form.props.onSubmit({ preventDefault() {}, stopPropagation() {} })
  await settle()
  assert.deepEqual(state.textCredentials, [{ provider: 'text', fields: { api_key: 'test-new-key-12345' } }])
  assert.equal(input.value, '')
  assert.equal(state.textBalanceCalls, 2)
  app.unmount(); state.providers = []
})

test('文字密钥保存失败后不保留输入，也不误刷新余额', async () => {
  state.providers = [{ id: 'text', label: '文字模型', editable: true, fields: [{ id: 'api_key', label: 'API Key', configured: true }] }]
  state.textBalanceCalls = 0; state.failTextCredential = true
  const { root, app } = await mount()
  const form = find(root, n => n.tag === 'form' && textOf(n).includes('保存文字凭据'))
  const input = find(form, n => n.tag === 'input')
  input.value = 'test-failed-key'; input.listeners.input({ target: input })
  await settle()
  form.props.onSubmit({ preventDefault() {}, stopPropagation() {} })
  await settle()
  assert.equal(input.value, '')
  assert.equal(state.textBalanceCalls, 1)
  assert.match(textOf(root), /保存失败/)
  app.unmount(); state.providers = []; state.failTextCredential = false
})

test('额度每分钟刷新并在离开账户页时停止', async () => {
  state.textBalanceCalls = 0
  state.creativeBalanceCalls = 0
  const { app } = await mount()
  assert.equal(state.textBalanceCalls, 1)
  assert.equal(state.creativeBalanceCalls, 1)
  intervalCallback()
  await settle()
  assert.equal(state.textBalanceCalls, 2)
  assert.equal(state.creativeBalanceCalls, 2)
  app.unmount()
  assert.equal(intervalCleared, true)
})
