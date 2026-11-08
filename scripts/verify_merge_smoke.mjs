// 合并后冒烟：新功能页 + 对方改动的页面（问答/校园服务/公告）不报错
import { chromium } from 'playwright'

const BASE = process.env.SMOKE_BASE || 'http://localhost:5173'
const TOKEN = process.argv[2]

const USER = {
  id: 1,
  username: 'admin',
  nickname: '管理员',
  role: 'admin',
  education_level: 'unset',
  education_label: '未设置',
}

const CASES = [
  { path: '/chat', expect: ['智能问答'] },
  {
    path: '/services',
    expect: ['寝室报修'],
    // placeholder 不在 innerText 里，按选择器判定
    selectors: ['input[placeholder="搜索标题或描述"]', 'input[placeholder="楼栋，如 12栋"]'],
  },
  { path: '/notices', expect: ['校园公告'] },
  { path: '/admin/tickets', expect: ['工单处理台'] },
  { path: '/admin/feedback', expect: ['反馈与未命中工作台'] },
  { path: '/admin/dashboard', expect: ['运营看板', '提问量'] },
  { path: '/admin/users', expect: ['用户账号管理', '学历'] },
]

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
  await page.goto(`${BASE}${item.path}`, { waitUntil: 'networkidle', timeout: 45000 })
  const body = await page.locator('body').innerText()
  const missing = item.expect.filter((text) => !body.includes(text))
  for (const selector of item.selectors || []) {
    if ((await page.locator(selector).count()) === 0) missing.push(selector)
  }
  const ok = missing.length === 0 && errors.length === 0
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'} ${item.path}`)
  if (missing.length) console.log(`  缺少: ${missing.join(' / ')}`)
  if (errors.length) console.log(`  报错: ${errors.slice(0, 2).join(' | ')}`)
  await page.close()
}

await browser.close()
console.log(failed ? `失败 ${failed} 项` : '合并版冒烟全部通过')
process.exit(failed ? 1 : 0)
