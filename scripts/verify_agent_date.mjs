/** 端到端：Agent 对“今天/明天”的日期锚定 */
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
await p.waitForURL('**/chat', { timeout: 25000 })
await p.waitForTimeout(1000)

async function ask(q) {
  await p.locator('textarea').fill(q)
  await p.keyboard.press('Enter')
  for (let i = 0; i < 60; i++) {
    if (!(await p.locator('.typing').count())) break
    await p.waitForTimeout(1500)
  }
  await p.waitForTimeout(600)
  return p.locator('.assistant-text').last().innerText()
}

await p.getByRole('button', { name: '新对话' }).click()
await p.waitForTimeout(600)
const a1 = await ask('今天是几月几号星期几？')
console.log('【日期问句】', a1.replace(/\s+/g, ' ').slice(0, 120))

await p.getByRole('button', { name: '新对话' }).click()
await p.waitForTimeout(600)
const a2 = await ask('帮我取消我明天的预约')
console.log('【取消明天预约】', a2.replace(/\s+/g, ' ').slice(0, 260))
await p.locator('.chat-messages').first().scrollIntoViewIfNeeded()
await p.screenshot({ path: `${OUT}/ui-20-agent-date.png` })

await b.close()
