// 扩展功能前端冒烟：管理员三页 + 校园服务“我的工单”页签
// 运行：node scripts/verify_extension_p0.mjs <token>
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const TOKEN = process.argv[2]
const BASE = process.env.SMOKE_BASE || 'http://127.0.0.1:5174'
if (!TOKEN) {
  console.error('缺少 token 参数')
  process.exit(2)
}

const USER = {
  id: 1,
  username: 'admin',
  nickname: '管理员',
  role: 'admin',
  education_level: 'unset',
  education_label: '未设置',
}

const CASES = [
  { path: '/admin/tickets', expect: ['工单处理台', '工单号'] },
  { path: '/admin/feedback', expect: ['反馈与未命中工作台', '未命中问题'] },
  { path: '/admin/dashboard', expect: ['运营看板', '提问量'] },
  { path: '/services', expect: ['校园服务', '我的工单'] },
]

mkdirSync('screenshots', { recursive: true })

const browser = await chromium.launch()
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
await context.addInitScript(
  ([token, user]) => {
    localStorage.setItem('school_rules_token', token)
    localStorage.setItem('school_rules_user', JSON.stringify(user))
  },
  [TOKEN, USER],
)

let failed = 0
for (const item of CASES) {
  const page = await context.newPage()
  const errors = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text())
  })
  page.on('pageerror', (err) => errors.push(String(err)))
  const url = `${BASE}${item.path}`
  await page.goto(url, { waitUntil: 'networkidle', timeout: 45000 })
  const body = await page.locator('body').innerText()
  const missing = item.expect.filter((text) => !body.includes(text))
  const name = item.path.replace(/\//g, '_').replace(/^_/, '')
  await page.screenshot({ path: `screenshots/ext-${name}.png`, fullPage: true })
  const ok = missing.length === 0 && errors.length === 0
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'} ${item.path}`)
  if (missing.length) console.log(`  缺少文案: ${missing.join(' / ')}`)
  if (errors.length) console.log(`  控制台错误: ${errors.slice(0, 3).join(' | ')}`)
  await page.close()
}

// 管理员在“我的工单”页签能看到提交入口提示
const page = await context.newPage()
await page.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await page.getByRole('tab', { name: '我的工单' }).click()
await page.waitForTimeout(600)
const tabText = await page.locator('body').innerText()
const tabOk = tabText.includes('转人工 / 投诉工单与寝室报修分开管理')
if (!tabOk) failed += 1
console.log(`${tabOk ? 'PASS' : 'FAIL'} /services 我的工单页签内容`)
await page.screenshot({ path: 'screenshots/ext-services-tickets-tab.png', fullPage: true })

await browser.close()
console.log(failed ? `失败 ${failed} 项` : '全部通过')
process.exit(failed ? 1 : 0)
