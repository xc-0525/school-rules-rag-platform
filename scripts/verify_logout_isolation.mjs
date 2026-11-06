/** 回归：退出换号后聊天流不串号；语义缓存按用户隔离 */
const { chromium } = await import('playwright')
import { resolve } from 'node:path'

const BASE = 'http://127.0.0.1:8000'
const OUT = resolve('screenshots')

async function apiLogin(username, password) {
  const res = await fetch(`${BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  return (await res.json()).token
}

// 1) 缓存隔离：admin 先问，demo 再问同一问题，不应命中 admin 的缓存
const tAdmin = await apiLogin('admin', 'admin123')
const tDemo = await apiLogin('demo', 'demo12345')
const ask = async (token, query) => {
  const res = await fetch(`${BASE}/api/v1/chat`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, stream: false }),
  })
  const body = await res.json()
  return { cached: !!body.cached, answer: (body.answer || '').slice(0, 60) }
}
const q = '国家奖学金需要什么条件？'
const a1 = await ask(tAdmin, q)
const a2 = await ask(tDemo, q)
console.log('admin 问 → cached:', a1.cached)
console.log('demo 问同题 → cached:', a2.cached, '(应为 false，不得复用 admin 缓存)')

// 2) UI：admin 聊天 → 退出 → demo 登录，聊天页应为空（欢迎页）
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1440, height: 900 } })
await p.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await p.fill("input[type='text']", 'admin')
await p.fill("input[type='password']", 'admin123')
await p.keyboard.press('Enter')
await p.waitForURL('**/chat', { timeout: 25000 })
await p.waitForTimeout(600)
await p.locator('textarea').fill(q)
await p.keyboard.press('Enter')
for (let i = 0; i < 50; i++) {
  if (!(await p.locator('.typing').count())) break
  await p.waitForTimeout(1500)
}
console.log('admin 聊天流消息数:', await p.locator('.message-row').count())

await p.getByRole('button', { name: '退出', exact: true }).click()
await p.waitForURL('**/login', { timeout: 15000 })
await p.fill("input[type='text']", 'demo')
await p.fill("input[type='password']", 'demo12345')
await p.keyboard.press('Enter')
await p.waitForURL('**/chat', { timeout: 25000 })
await p.waitForTimeout(800)
const residual = await p.locator('.message-row').count()
const welcome = await p.locator('.welcome').count()
console.log('demo 登录后残留消息:', residual, '(应为 0) | 欢迎页可见:', welcome > 0)
await p.screenshot({ path: `${OUT}/ui-22-fresh-login.png` })
await b.close()
