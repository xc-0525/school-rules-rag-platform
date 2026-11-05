/** 验证：停在公告页时点横幅 × → “已隐藏 N 条”入口立刻出现 → 恢复后入口消失、横幅回来 */
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

// 关键场景：先进公告页，再在公告页上关闭横幅（不刷新、不跳转）
await p.goto(`${BASE}/notices`, { waitUntil: 'networkidle' })
await p.waitForTimeout(1000)

let banner = 0
try {
  await p.waitForSelector('.notice-banner', { timeout: 8000 })
  banner = 1
} catch {
  banner = 0
}
console.log('公告页横幅存在:', banner > 0)
if (!banner) {
  await b.close()
  process.exit(0)
}

console.log('关闭前入口:', await p.locator('.restore-dismissed').count())
await p.screenshot({ path: `${OUT}/72-inpage-before-close.png` })

await p.click('.notice-banner-close')
await p.waitForTimeout(600)

const hint = await p.locator('.restore-dismissed').count()
console.log('关闭后横幅还在:', (await p.locator('.notice-banner').count()) > 0)
console.log('关闭后入口出现:', hint > 0, '| 文案:', hint ? (await p.locator('.restore-dismissed').innerText()).replace(/\s+/g, ' ') : '')
await p.screenshot({ path: `${OUT}/73-inpage-after-close.png` })

if (hint) {
  await p.locator('.restore-dismissed button').click()
  await p.waitForTimeout(800)
  console.log('恢复后入口消失:', (await p.locator('.restore-dismissed').count()) === 0)
  console.log('恢复后横幅回来:', (await p.locator('.notice-banner').count()) > 0)
  await p.screenshot({ path: `${OUT}/74-inpage-after-restore.png` })
}

await b.close()
