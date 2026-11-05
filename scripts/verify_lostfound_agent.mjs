/**
 * 验证：对话发布失物招领 → 草稿卡 → 点“确认发布” → 已提交审核
 * 一次性脚本（不参与构建）
 */
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

await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await page.fill("input[type='text']", 'demo')
await page.fill("input[type='password']", 'demo12345')
await page.keyboard.press('Enter')
await page.waitForURL('**/chat', { timeout: 20000 })

const waitIdle = () =>
  page.waitForFunction(() => {
    const el = document.querySelector('textarea')
    return el && !el.disabled
  }, { timeout: 150000 })

// 第一轮：不带联系方式，模型应调用工具后追问
await page.fill('textarea', '我昨天在桂园三楼丢了一个学生证 帮我发布一下失物招领')
await page.keyboard.press('Enter')
await waitIdle()
console.log('[1] 第一轮回答：', (await page.locator('.message-row.assistant').last().innerText()).replace(/\s+/g, ' ').slice(0, 100))

// 第二轮：补联系方式 → 应出现草稿卡
await page.fill('textarea', '手机号 13800001112')
await page.keyboard.press('Enter')
await page.waitForSelector('.ticket-draft', { timeout: 150000 })
await page.waitForTimeout(800)
await page.screenshot({ path: `${OUT}/30-lostfound-draft.png` })
console.log('[2] 草稿卡：', (await page.locator('.ticket-draft').last().innerText()).replace(/\s+/g, ' ').slice(0, 130))

const btn = page.locator('.ticket-draft').last().locator('button')
console.log('[3] 按钮文案：', (await btn.innerText()).trim())
await btn.click()
await page.waitForTimeout(4500)
await page.screenshot({ path: `${OUT}/31-lostfound-confirmed.png` })
console.log('[4] 确认后：', (await page.locator('.ticket-draft').last().innerText()).replace(/\s+/g, ' ').slice(0, 120))

await browser.close()
console.log('完成')
