/** 为答辩 PPT / 报告书生成新版界面截图 */
import { chromium } from 'playwright'
import { resolve } from 'node:path'

const BASE = 'http://127.0.0.1:8000'
const OUT = resolve('答辩PPT/assets')
const b = await chromium.launch()

// 1) 登录页（未登录）
const guest = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
await guest.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await guest.waitForTimeout(800)
await guest.screenshot({ path: `${OUT}/12-login-wut.png` })
await guest.close()

// 2) 预约界面（余量角标）
const p = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
await p.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await p.fill("input[type='text']", 'demo')
await p.fill("input[type='password']", 'demo12345')
await p.keyboard.press('Enter')
await p.waitForURL('**/chat', { timeout: 20000 })

await p.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await p.getByRole('tab', { name: /图书馆预约/ }).click()
await p.waitForTimeout(1500)
// 选第一个位置，触发时段余量
const loc = p.locator('.location-card').first()
if (await loc.count()) {
  await loc.click()
  await p.waitForTimeout(1500)
}
await p.screenshot({ path: `${OUT}/12-reserve-slots.png` })
const slots = await p.locator('.slot-left').allInnerTexts()
console.log('预约页余量角标:', slots.slice(0, 6).join(' | '))

// 3) 历史页（搜索 + 分页）
await p.goto(`${BASE}/history`, { waitUntil: 'networkidle' })
await p.waitForTimeout(1200)
await p.locator('.history-search input').fill('宿舍')
await p.waitForTimeout(1200)
await p.screenshot({ path: `${OUT}/12-history-search.png` })
console.log('历史页结果条数:', await p.locator('.conversation-item').count())
await p.close()

// 4) 失物驳回弹窗（管理员）
const a = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
await a.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await a.fill("input[type='text']", 'admin')
await a.fill("input[type='password']", 'admin123')
await a.keyboard.press('Enter')
await a.waitForURL('**/chat', { timeout: 20000 })
await a.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await a.getByRole('tab', { name: /失物招领/ }).click()
await a.waitForTimeout(900)
await a.locator('.el-radio-button__inner', { hasText: '待审核' }).first().click()
await a.waitForTimeout(900)

const rejects = a.getByRole('button', { name: '驳回' })
console.log('待审核条目:', await rejects.count())
if (await rejects.count()) {
  await rejects.first().click()
  await a.waitForTimeout(600)
  await a.locator('.el-dialog:has-text("驳回失物招领") .el-select').click()
  await a.waitForTimeout(500)
  await a.screenshot({ path: `${OUT}/12-lf-reject.png` })
}
await a.close()
await b.close()
console.log('done')
