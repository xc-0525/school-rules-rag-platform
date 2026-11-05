/** 验证：PDF 上传去重弹窗 + 对话预约确认按钮（一次性脚本） */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const BASE = 'http://127.0.0.1:8000'
const OUT = resolve(__dirname, '../screenshots')
mkdirSync(OUT, { recursive: true })
const PDF = resolve(__dirname, '../.buildtmp/report/校园电动车管理办法_演示.pdf')

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

// 1. 管理员登录并上传 PDF
await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await page.fill("input[type='text']", 'admin')
await page.fill("input[type='password']", 'admin123')
await page.keyboard.press('Enter')
await page.waitForURL('**/chat', { timeout: 15000 })
await page.goto(`${BASE}/documents`, { waitUntil: 'networkidle' })
await page.setInputFiles("input[type='file']", PDF)
await page.waitForSelector('.el-message-box', { timeout: 120000 })
await page.waitForTimeout(600)
await page.screenshot({ path: `${OUT}/20-upload-dedupe-dialog.png` })
const dialogText = (await page.locator('.el-message-box').innerText()).replace(/\s+/g, ' ')
console.log('[1] 去重弹窗:', dialogText.slice(0, 120))
await page.click(".el-message-box__btns button")

// 2. 学生登录，对话预约
await page.evaluate(() => localStorage.clear())
await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await page.fill("input[type='text']", 'demo')
await page.fill("input[type='password']", 'demo12345')
await page.keyboard.press('Enter')
await page.waitForURL('**/chat', { timeout: 15000 })
await page.fill('textarea', '帮我预约明天下午的研讨间')
await page.keyboard.press('Enter')
await page.waitForSelector('.ticket-draft', { timeout: 120000 })
await page.waitForTimeout(500)
await page.screenshot({ path: `${OUT}/21-reservation-draft.png` })
console.log('[2] 预约草稿:', (await page.locator('.ticket-draft').innerText()).replace(/\s+/g, ' ').slice(0, 110))

await page.click(".ticket-draft-actions button")
await page.waitForTimeout(2500)
await page.screenshot({ path: `${OUT}/22-reservation-confirmed.png` })
const confirmed = await page.locator('.ticket-draft-actions').innerText()
console.log('[3] 确认后:', confirmed.replace(/\s+/g, ' '))

await browser.close()
console.log('完成')
