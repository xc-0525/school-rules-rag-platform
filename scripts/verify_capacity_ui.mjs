/** 验证：预约时段余量显示 + 管理员关闭报修原因下拉（一次性脚本） */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const BASE = 'http://127.0.0.1:8000'
const OUT = resolve(__dirname, '../screenshots')
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

async function login(user, pwd) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await page.fill("input[type='text']", user)
  await page.fill("input[type='password']", pwd)
  await page.keyboard.press('Enter')
  await page.waitForURL('**/chat', { timeout: 20000 })
}

// 1) 学生：图书馆预约余量
await login('demo', 'demo12345')
await page.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await page.click(".el-tabs__item:has-text('图书馆预约')")
await page.waitForTimeout(800)
await page.click('.location-card')
await page.waitForTimeout(1500)
await page.screenshot({ path: `${OUT}/40-slot-remaining.png` })
const slots = await page.locator('.slot-row').innerText()
console.log('[1] 时段余量：', slots.replace(/\s+/g, ' ').slice(0, 120))

// 2) 管理员：关闭报修原因下拉
await page.evaluate(() => localStorage.clear())
await login('admin', 'admin123')
await page.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await page.waitForTimeout(800)
const closeBtn = page.locator("button:has-text('关闭')").first()
if (await closeBtn.count()) {
  await closeBtn.click()
  await page.waitForSelector('.el-dialog:has-text("关闭报修申请")', { timeout: 8000 })
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${OUT}/41-close-reason.png` })
  await page.click(".el-dialog:has-text('关闭报修申请') .el-select")
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${OUT}/42-close-reason-options.png` })
  const opts = await page.locator('.el-select-dropdown__item').allInnerTexts()
  console.log('[2] 下拉选项：', opts.join(' | '))
} else {
  console.log('[2] 未找到关闭按钮（可能没有待处理报修）')
}

await browser.close()
console.log('完成')
