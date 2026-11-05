/** 验证：关闭紧急横幅 → 公告页“恢复显示” → 计数归零、按钮消失、横幅回来 */
import { chromium } from 'playwright'
import { resolve } from 'node:path'
const BASE = 'http://127.0.0.1:8000'
const OUT = resolve('screenshots')
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1440, height: 900 } })
await p.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await p.fill("input[type='text']", 'demo'); await p.fill("input[type='password']", 'demo12345')
await p.keyboard.press('Enter'); await p.waitForURL('**/chat', { timeout: 20000 })

let banner = 0
try {
  await p.waitForSelector('.notice-banner', { timeout: 10000 })
  banner = 1
} catch { banner = 0 }
console.log('横幅存在:', banner > 0)
if (banner) { await p.click('.notice-banner-close'); await p.waitForTimeout(800) }
await p.goto(`${BASE}/notices`, { waitUntil: 'networkidle' })
await p.waitForTimeout(1200)
const hintBefore = await p.locator('.restore-dismissed').count()
console.log('恢复入口可见:', hintBefore > 0, '| 文案:', hintBefore ? (await p.locator('.restore-dismissed').innerText()).replace(/\s+/g, ' ') : '')
await p.screenshot({ path: `${OUT}/70-restore-before.png` })
if (hintBefore) {
  await p.locator('.restore-dismissed button').click()
  await p.waitForTimeout(1200)
  console.log('点击后入口还在:', await p.locator('.restore-dismissed').count() > 0)
  console.log('横幅回来了:', await p.locator('.notice-banner').count() > 0)
  await p.screenshot({ path: `${OUT}/71-restore-after.png` })
}
await b.close()
