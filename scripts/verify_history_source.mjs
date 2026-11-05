/** 回归：历史页点赞不串台、来源引用可展开 */
import { chromium } from 'playwright'
import { resolve } from 'node:path'

const BASE = 'http://127.0.0.1:8000'
const OUT = resolve('screenshots')
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1440, height: 900 } })

await p.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await p.fill("input[type='text']", 'demo')
await p.fill("input[type='password']", 'demo12345')
await p.keyboard.press('Enter')
await p.waitForURL('**/chat', { timeout: 20000 })

// 1) 历史页点赞状态
await p.goto(`${BASE}/history`, { waitUntil: 'networkidle' })
await p.waitForTimeout(1500)
const buttons = p.locator('.feedback-row .el-button')
const total = await buttons.count()
console.log('历史页反馈按钮数:', total)
if (total) {
  await buttons.first().click()
  await p.waitForTimeout(900)
  const before = await p.locator('.feedback-row .el-button--success').count()
  const items = p.locator('.conversation-item')
  const n = await items.count()
  console.log('会话数:', n, '| 当前赞态数:', before)
  if (n > 1) {
    await items.nth(1).click()
    await p.waitForTimeout(1200)
    const after = await p.locator('.feedback-row .el-button--success').count()
    console.log('切换会话后赞态数:', after, '| 未串台:', after === 0)
    await p.screenshot({ path: `${OUT}/ui-12-history-switch.png` })
  }
}

// 2) 来源展开
await p.goto(`${BASE}/chat`, { waitUntil: 'networkidle' })
await p.waitForTimeout(1000)
let toggles = await p.locator('.snippet-toggle').count()
if (!toggles) {
  await p.locator('textarea').fill('宿舍晚上几点熄灯？')
  await p.keyboard.press('Enter')
  for (let i = 0; i < 40; i++) {
    if (!(await p.locator('.typing').count())) break
    await p.waitForTimeout(1500)
  }
  await p.waitForTimeout(800)
  toggles = await p.locator('.snippet-toggle').count()
}
console.log('来源展开按钮数:', toggles)
if (toggles) {
  const snippet = p.locator('.source-snippet').first()
  const beforeH = await snippet.evaluate((el) => el.clientHeight)
  await p.locator('.snippet-toggle').first().click()
  await p.waitForTimeout(400)
  const expanded = await p.locator('.source-snippet.is-expanded').count()
  const afterH = await snippet.evaluate((el) => el.clientHeight)
  console.log('展开生效:', expanded > 0, `(高度 ${beforeH} → ${afterH})`)
  await p.locator('.source-list').first().scrollIntoViewIfNeeded()
  await p.screenshot({ path: `${OUT}/ui-13-source-expand.png` })
}

await b.close()
