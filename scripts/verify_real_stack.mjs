// 真实栈（5173 → 8000，qdrant/neo4j/mysql）学生视角冒烟
import { chromium } from 'playwright'

const TOKEN = process.argv[2]
if (!TOKEN) {
  console.error('缺少 token 参数')
  process.exit(2)
}
const BASE = 'http://localhost:5173'
const USER = {
  id: 788,
  username: '__probe_user',
  nickname: '探针',
  role: 'student',
  education_level: 'unset',
}

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

async function dismissProfile(page) {
  const later = page.getByRole('button', { name: '稍后再说' })
  if (await later.count()) {
    await later.click().catch(() => {})
    await page.waitForTimeout(300)
  }
}

async function check(path, expects, action) {
  const page = await context.newPage()
  const errors = []
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  page.on('pageerror', (e) => errors.push(String(e)))
  await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle', timeout: 60000 })
  await dismissProfile(page)
  if (action) await action(page)
  const body = await page.locator('body').innerText()
  const missing = expects.filter((t) => !body.includes(t))
  const ok = !missing.length && !errors.length
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'} ${path}`)
  if (missing.length) console.log(`  缺少: ${missing.join(' / ')}`)
  if (errors.length) console.log(`  报错: ${errors.slice(0, 2).join(' | ')}`)
  await page.close()
}

// N-20：未设置学历的学生首次进入会弹出补全框（先验弹窗，再验其他页）
const page = await context.newPage()
await page.goto(`${BASE}/chat`, { waitUntil: 'networkidle' })
const dialog = await page.locator('text=补全身份信息').count()
console.log(`${dialog > 0 ? 'PASS' : 'FAIL'} 首登身份补全弹窗`)
if (!dialog) failed += 1
await page.close()

await check('/chat', ['智能问答'])
await check('/services', ['寝室报修'], async (page) => {
  await page.getByRole('tab', { name: '我的工单' }).click()
  await page.waitForTimeout(500)
})
await check('/notices', ['校园公告'])

await browser.close()
console.log(failed ? `失败 ${failed} 项` : '真实栈冒烟全部通过')
process.exit(failed ? 1 : 0)
