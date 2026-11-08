/** 基线冒烟：登录 + 各业务只读接口 + 问答链路 + 逐页截图（不写业务数据） */
import { chromium } from 'playwright'

const BASE = 'http://127.0.0.1:8000'
const OUT = 'screenshots'
const results = []
function check(name, ok, extra = '') {
  results.push({ name, ok, extra })
  console.log(`${ok ? 'OK ' : '!! '} ${name}${extra ? ' | ' + extra : ''}`)
}

async function login(username, password) {
  const res = await fetch(`${BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const body = await res.json()
  return { status: res.status, token: body.token, user: body.user }
}

const admin = await login('admin', 'admin123')
check('管理员登录', admin.status === 200 && !!admin.token, admin.user?.role)
const demo = await login('demo', 'demo12345')
check('学生登录', demo.status === 200 && !!demo.token)
check('错误密码被拒', (await login('demo', 'wrong-pass')).status === 401)

const H = { Authorization: `Bearer ${admin.token}`, 'Content-Type': 'application/json' }
const HS = { Authorization: `Bearer ${demo.token}`, 'Content-Type': 'application/json' }

async function get(path, headers = H) {
  const res = await fetch(BASE + path, { headers })
  return { status: res.status, body: await res.json().catch(() => null) }
}

// 只读接口
const health = await get('/health', {})
check('健康检查', health.status === 200 && health.body?.status === 'ok', health.body?.modes?.vector)

const conv = await get('/api/v1/conversations?limit=3&offset=0')
check('会话列表分页', conv.status === 200 && Array.isArray(conv.body?.items), `total=${conv.body?.total}`)

const docs = await get('/api/v1/documents')
check('文档列表', docs.status === 200 && Array.isArray(docs.body), `${docs.body?.length} 篇`)

const notices = await get('/api/v1/notices?limit=5')
check('公告列表', notices.status === 200 && Array.isArray(notices.body))

const locs = await get('/api/v1/reservations/locations')
check('可预约位置', locs.status === 200 && Array.isArray(locs.body), `${locs.body?.length} 个`)

const resv = await get('/api/v1/reservations?limit=5', HS)
check('我的预约', resv.status === 200)

const repairs = await get('/api/v1/repairs?limit=5', HS)
check('报修列表', repairs.status === 200)

const lost = await get('/api/v1/lost-found?kind=found&limit=5', HS)
check('失物列表', lost.status === 200)

const users = await get('/api/v1/admin/users?limit=5')
check('用户管理列表', users.status === 200 && Array.isArray(users.body?.items), `total=${users.body?.total}`)
check('学生访问用户管理被拒', (await get('/api/v1/admin/users?limit=5', HS)).status === 403)

// 问答链路（非流式，走完整 Agent）
const t0 = Date.now()
const chat = await fetch(`${BASE}/api/v1/chat`, {
  method: 'POST',
  headers: HS,
  body: JSON.stringify({ query: '宿舍晚上几点熄灯？', stream: false }),
})
const chatBody = await chat.json()
const visited = chatBody.runtime?.visited || []
check(
  '问答链路',
  chat.status === 200 && (chatBody.answer || '').length > 20,
  `${Date.now() - t0}ms 节点=${visited.length} 来源=${(chatBody.sources || []).length}`,
)
check('未命中拒答节点', !visited.includes('reject_handoff') || (chatBody.answer || '').includes('没有能直接回答'))

// 逐页截图（确认无白屏）
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await page.fill("input[type='text']", 'admin')
await page.fill("input[type='password']", 'admin123')
await page.keyboard.press('Enter')
await page.waitForURL('**/chat', { timeout: 25000 })

const pages = [
  ['/chat', 'baseline-01-chat'],
  ['/history', 'baseline-02-history'],
  ['/notices', 'baseline-03-notices'],
  ['/services', 'baseline-04-services'],
  ['/documents', 'baseline-05-documents'],
  ['/admin/users', 'baseline-06-users'],
  ['/about', 'baseline-07-about'],
]
for (const [path, name] of pages) {
  await page.goto(BASE + path, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  const textLen = (await page.locator('body').innerText()).replace(/\s+/g, '').length
  check(`页面 ${path}`, textLen > 120, `${textLen} 字`)
  await page.screenshot({ path: `${OUT}/${name}.png` })
}
await browser.close()

const failed = results.filter((r) => !r.ok)
console.log(`\n通过 ${results.length - failed.length}/${results.length}`)
if (failed.length) {
  console.log('失败项:', failed.map((f) => f.name).join('、'))
  process.exitCode = 1
}
