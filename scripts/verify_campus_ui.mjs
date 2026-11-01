/** 校园服务模块 UI 截图（宽松模式：不定时断言，产物人工核验） */
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
await page.waitForURL('**/chat', { timeout: 15000 })

// 报修 Tab
await page.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1800)
await page.screenshot({ path: `${OUT}/10-services-repairs.png` })
console.log('[1] repairs:', await page.locator('.service-card').count(), 'cards')

// 预约 Tab
await page.click(".el-tabs__item:has-text('图书馆预约')")
await page.waitForTimeout(1800)
await page.screenshot({ path: `${OUT}/11-services-reserve.png` })
console.log('[2] reserve:', await page.locator('.location-card').count(), 'locations')

// 失物招领 Tab
await page.click(".el-tabs__item:has-text('失物招领')")
await page.waitForTimeout(1800)
await page.screenshot({ path: `${OUT}/12-services-lostfound.png` })
console.log('[3] lostfound:', await page.locator('.service-card').count(), 'cards')

// 文档预览：点击文件名应弹出新窗口
await page.goto(`${BASE}/documents`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)
const popupPromise = page.waitForEvent('popup', { timeout: 8000 }).catch(() => null)
await page.click('.el-link')
const popup = await popupPromise
console.log('[4] preview popup:', popup ? popup.url().slice(0, 36) : '未打开（需人工检查）')
if (popup) await popup.close()
await page.screenshot({ path: `${OUT}/13-documents-preview.png` })

await browser.close()
console.log('截图完成')
