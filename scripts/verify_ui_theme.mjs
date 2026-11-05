/** 视觉验收：登录页 + 学生视角五个页面截图 */
import { chromium } from 'playwright'
import { resolve } from 'node:path'

const BASE = 'http://127.0.0.1:8000'
const OUT = resolve('screenshots')
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })

// 登出态：登录页
await p.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await p.waitForTimeout(500)
await p.screenshot({ path: `${OUT}/ui-00-login.png` })

await p.fill("input[type='text']", 'demo')
await p.fill("input[type='password']", 'demo12345')
await p.keyboard.press('Enter')
await p.waitForURL('**/chat', { timeout: 20000 })
await p.waitForTimeout(1200)
await p.screenshot({ path: `${OUT}/ui-01-chat.png` })

for (const [path, name] of [
  ['/notices', 'ui-02-notices'],
  ['/services', 'ui-03-services'],
  ['/documents', 'ui-04-documents'],
  ['/history', 'ui-05-history'],
  ['/about', 'ui-06-about'],
]) {
  await p.goto(`${BASE}${path}`, { waitUntil: 'networkidle' })
  await p.waitForTimeout(900)
  await p.screenshot({ path: `${OUT}/${name}.png` })
}

console.log('done')
await b.close()
