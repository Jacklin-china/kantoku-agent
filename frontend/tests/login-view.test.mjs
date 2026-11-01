import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, h, nextTick } from 'vue'

const source = readFileSync(new URL('../src/views/LoginView.vue', import.meta.url), 'utf8')
const script = compileScript(parse(source).descriptor, { id: 'login-view', inlineTemplate: true }).content
const code = ts.transpileModule(script, { compilerOptions: { module: ts.ModuleKind.ES2022 } }).outputText
  .replace(/from (["'])vue\1/g, `from "${import.meta.resolve('vue')}"`)
const { default: LoginView } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)

const node = tag => ({
  tag, tagName: tag.toUpperCase(), props: {}, children: [], parent: null, listeners: {}, value: '',
  addEventListener(name, handler) { this.listeners[name] = handler },
  removeEventListener(name) { delete this.listeners[name] },
})
const remove = child => {
  if (!child.parent) return
  const index = child.parent.children.indexOf(child)
  if (index >= 0) child.parent.children.splice(index, 1)
}
const insertNode = (child, parent, anchor) => {
  remove(child)
  const index = anchor ? parent.children.indexOf(anchor) : -1
  parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
  child.parent = parent
}
const renderer = createRenderer({
  createElement: node,
  createText: text => Object.assign(node('#text'), { text }),
  createComment: text => Object.assign(node('#comment'), { text }),
  setText(child, text) { child.text = text },
  setElementText(child, text) { child.text = text; child.children = [] },
  patchProp(child, key, _old, value) { child.props[key] = value; if (key === 'value') child.value = value },
  insert: insertNode,
  remove,
  parentNode: child => child.parent,
  nextSibling: child => child.parent?.children[child.parent.children.indexOf(child) + 1] ?? null,
  /* Vue 会把「整棵子树都是静态」的节点序列化成静态字符串，并交给 hostInsertStaticContent 插入。
     浏览器渲染器实现了它；自定义渲染器必须补上，否则模板一变大就会抛 TypeError。
     这里只需把它挂成一棵占位节点——本测试只断言输入框、按钮与表单事件。 */
  insertStaticContent(content, parent, anchor) {
    const staticNode = Object.assign(node('#static'), { text: content })
    insertNode(staticNode, parent, anchor)
    return [staticNode, staticNode]
  },
  createElementNS: (_namespace, tag) => node(tag),
})
const all = child => [child, ...child.children.flatMap(all)]
const find = (root, pred) => all(root).find(pred)

test('registration requires matching credentials and can preserve legacy mode', async () => {
  const root = node('root')
  const calls = []
  const app = renderer.createApp({ render: () => h(LoginView, {
    registered: false, onSubmit: value => calls.push(value),
    onContinueLocal: () => calls.push('local'),
  }) })
  app.mount(root)
  const submit = find(root, n => n.tag === 'button' && n.text === '创建账号')
  assert.equal(submit.props.disabled, true)
  for (const [id, value] of [
    ['login-username', 'user1'], ['login-password', 'password123'],
    ['login-confirmation', 'wrong-password'],
  ]) {
    const input = find(root, n => n.tag === 'input' && n.props.id === id)
    input.value = value
    input.listeners.input({ target: input })
  }
  await nextTick()
  assert.equal(submit.props.disabled, true)
  const confirmation = find(root, n => n.props.id === 'login-confirmation')
  confirmation.value = 'password123'
  confirmation.listeners.input({ target: confirmation })
  await nextTick()
  assert.equal(submit.props.disabled, false)
  find(root, n => n.tag === 'form').props.onSubmit({ preventDefault() {} })
  assert.deepEqual(calls, [{ username: 'user1', password: 'password123' }])
  find(root, n => n.tag === 'button' && n.props.class === 'login-local').props.onClick()
  assert.equal(calls[1], 'local')
  app.unmount()
})

test('login emits credentials without a confirmation field', async () => {
  const root = node('root')
  const calls = []
  const app = renderer.createApp({ render: () => h(LoginView, {
    registered: true, onSubmit: value => calls.push(value),
  }) })
  app.mount(root)
  for (const [id, value] of [['login-username', 'user1'], ['login-password', 'password123']]) {
    const input = find(root, n => n.tag === 'input' && n.props.id === id)
    input.value = value
    input.listeners.input({ target: input })
  }
  await nextTick()
  find(root, n => n.tag === 'form').props.onSubmit({ preventDefault() {} })
  assert.deepEqual(calls, [{ username: 'user1', password: 'password123' }])
  assert.equal(find(root, n => n.props.id === 'login-confirmation'), undefined)
  app.unmount()
})
