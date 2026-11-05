/** 交互回归：IME 回车不误发、滚动不被拽回、停止按钮生效 */
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
await p.waitForTimeout(1200)

// 1) 输入法组合态回车：不应发送
await p.locator('textarea').fill('测试输入法回车')
const before = await p.locator('.message-row').count()
await p.evaluate(() => {
  const ta = document.querySelector('textarea')
  const ev = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'isComposing', { value: true })
  ta.dispatchEvent(ev)
})
await p.waitForTimeout(600)
const after = await p.locator('.message-row').count()
console.log('组合态回车未发送:', after === before, `(${before} → ${after})`)

// 2) 正常回车发送
await p.locator('textarea').fill('宿舍晚上几点熄灯？')
await p.keyboard.press('Enter')
await p.waitForTimeout(1000)
console.log('正常回车已发送:', (await p.locator('.message-row').count()) >= before + 2)

// 等回答落地（最多 90 秒）
for (let i = 0; i < 45; i++) {
  if (!(await p.locator('.typing').count())) break
  await p.waitForTimeout(2000)
}
await p.waitForTimeout(800)

// 3) 上翻后不被拽回，出现“回到底部”
const box = await p.locator('.chat-messages').boundingBox()
await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
await p.mouse.wheel(0, -2000)
await p.waitForTimeout(1200)
const scrollState = await p.evaluate(() => {
  const el = document.querySelector('.chat-messages')
  return {
    top: Math.round(el.scrollTop),
    gap: Math.round(el.scrollHeight - el.scrollTop - el.clientHeight),
    hasButton: !!document.querySelector('.scroll-bottom'),
  }
})
console.log('上翻后状态:', JSON.stringify(scrollState))
await p.screenshot({ path: `${OUT}/ui-10-scroll-back.png` })

if (scrollState.hasButton) {
  await p.locator('.scroll-bottom').click()
  await p.waitForTimeout(1200)
  const back = await p.evaluate(() => {
    const el = document.querySelector('.chat-messages')
    return el.scrollHeight - el.scrollTop - el.clientHeight
  })
  console.log('点击后回到底部:', back < 120, `(距底 ${Math.round(back)}px)`)
}

// 4) 停止按钮
await p.getByRole('button', { name: '新对话' }).click()
await p.waitForTimeout(600)
await p.locator('textarea').fill('国家奖学金需要什么条件？')
await p.keyboard.press('Enter')
await p.waitForTimeout(1200)
const stopped = await p.evaluate(() => {
  const btn = [...document.querySelectorAll('button')].find((b) => b.textContent.includes('停止'))
  if (btn) {
    btn.click()
    return true
  }
  return false
})
await p.waitForTimeout(1500)
const cancelled = await p.locator('.assistant-text:has-text("已取消生成")').count()
console.log('停止按钮存在并点击:', stopped, '| 出现取消文案:', cancelled > 0)
await p.screenshot({ path: `${OUT}/ui-11-stop.png` })

await b.close()
