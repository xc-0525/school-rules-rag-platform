/** 不同投影分辨率下的版式检查：1366x768 与 1920x1080 */
import { chromium } from 'playwright'
import { resolve } from 'node:path'

const BASE = 'http://127.0.0.1:8000'
const OUT = resolve('screenshots')
const b = await chromium.launch()

for (const [w, h, tag] of [
  [1366, 768, '1366'],
  [1920, 1080, '1920'],
]) {
  const p = await b.newPage({ viewport: { width: w, height: h } })
  await p.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await p.waitForTimeout(400)
  await p.screenshot({ path: `${OUT}/ui-res-${tag}-login.png` })

  await p.fill("input[type='text']", 'demo')
  await p.fill("input[type='password']", 'demo12345')
  await p.keyboard.press('Enter')
  await p.waitForURL('**/chat', { timeout: 20000 })
  await p.waitForTimeout(900)
  await p.screenshot({ path: `${OUT}/ui-res-${tag}-chat.png` })
  await p.close()
}

console.log('done')
await b.close()
