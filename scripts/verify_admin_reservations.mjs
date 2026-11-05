/** 验证：我的预约折叠/过期标签、管理员预约列表与代取消、失物图片单删 */
import { chromium } from 'playwright'
import { resolve } from 'node:path'
const BASE = 'http://127.0.0.1:8000'
const OUT = resolve('screenshots')
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

async function login(u, p) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await page.fill("input[type='text']", u); await page.fill("input[type='password']", p)
  await page.keyboard.press('Enter'); await page.waitForURL('**/chat', { timeout: 20000 })
}

// 学生视角
await login('demo', 'demo12345')
await page.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await page.click(".el-tabs__item:has-text('图书馆预约')"); await page.waitForTimeout(1200)
await page.screenshot({ path: `${OUT}/50-my-reservations.png` })
console.log('[学生] 表头:', (await page.locator('.el-table__header').innerText()).replace(/\s+/g, ' '))
console.log('[学生] 行:', (await page.locator('.el-table__body').innerText()).replace(/\s+/g, ' ').slice(0, 120))
const sw = await page.locator('.el-switch').count()
if (sw) { await page.locator('.el-switch').first().click(); await page.waitForTimeout(500)
  console.log('[学生] 展开后行:', (await page.locator('.el-table__body').innerText()).replace(/\s+/g, ' ').slice(0, 160))
  await page.screenshot({ path: `${OUT}/51-my-reservations-expanded.png` }) }

// 失物图片删除
await page.click(".el-tabs__item:has-text('失物招领')"); await page.waitForTimeout(800)
await page.click("button:has-text('发布')"); await page.waitForTimeout(600)
const fileInput = page.locator("input[type='file']").last()
await fileInput.setInputFiles(resolve('screenshots/01-banner-visible.png'))
await page.waitForTimeout(2500)
const thumbs = await page.locator('.lf-thumb-wrap').count()
console.log('[失物] 缩略图数量:', thumbs, '| 删除按钮:', await page.locator('.lf-thumb-remove').count())
await page.screenshot({ path: `${OUT}/52-lostfound-image-remove.png` })
await page.keyboard.press('Escape'); await page.waitForTimeout(400)

// 管理员视角
await page.evaluate(() => localStorage.clear())
await login('admin', 'admin123')
await page.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await page.click(".el-tabs__item:has-text('图书馆预约')"); await page.waitForTimeout(1200)
await page.screenshot({ path: `${OUT}/53-admin-reservations.png` })
console.log('[管理员] 表头:', (await page.locator('.el-table__header').innerText()).replace(/\s+/g, ' '))
const btn = page.locator("button:has-text('代取消')").first()
if (await btn.count()) {
  await btn.click(); await page.waitForSelector('.el-dialog:has-text("代学生取消预约")', { timeout: 6000 })
  await page.waitForTimeout(400)
  await page.click(".el-dialog:has-text('代学生取消预约') .el-select")
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${OUT}/54-admin-cancel-dialog.png` })
  const opts = await page.locator('.el-select-dropdown__item').allInnerTexts()
  console.log('[管理员] 取消原因:', opts.join(' | '))
}
await browser.close()
console.log('完成')
