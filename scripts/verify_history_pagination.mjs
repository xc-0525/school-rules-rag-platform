/** 历史页搜索 + 分页交互验收：首屏、加载更多、整页边界、无结果、快速改词 */
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

await p.goto(`${BASE}/history`, { waitUntil: 'networkidle' })
await p.waitForTimeout(1500)

const count = async () => p.locator('.conversation-item').count()
const countText = async () => {
  const n = await p.locator('.history-count').count()
  return n ? (await p.locator('.history-count').innerText()).trim() : '(无)'
}
const moreText = async () => {
  const n = await p.locator('.history-more').count()
  return n ? (await p.locator('.history-more').innerText()).replace(/\s+/g, ' ').trim() : '(无)'
}

console.log('首屏条数:', await count(), '|', await countText())
console.log('底部控件:', await moreText())
await p.screenshot({ path: `${OUT}/ui-14-history-page1.png` })

// 连续点击加载更多直到到底
let guard = 0
while ((await p.locator('.history-more button').count()) && guard < 10) {
  const before = await count()
  await p.locator('.history-more button').click()
  await p.waitForTimeout(900)
  const after = await count()
  console.log(`加载更多: ${before} → ${after}`)
  if (after === before) break
  guard++
}
console.log('到底后底部:', await moreText(), '| 总条数:', await count())
await p.screenshot({ path: `${OUT}/ui-15-history-end.png` })

// 搜索命中
await p.locator('.history-search input').fill('熄灯')
await p.waitForTimeout(1200)
console.log('搜索“熄灯”条数:', await count(), '|', await countText(), '|', await moreText())
await p.screenshot({ path: `${OUT}/ui-16-history-search.png` })

// 搜索无结果
await p.locator('.history-search input').fill('绝不可能出现的关键词')
await p.waitForTimeout(1200)
console.log('无结果空状态:', (await p.locator('.history-empty').count()) > 0, '| 列表条数:', await count())
console.log('空状态文案:', (await p.locator('.history-empty').innerText()).replace(/\s+/g, ' '))
await p.screenshot({ path: `${OUT}/ui-17-history-empty.png` })

// 清除搜索
await p.locator('.history-empty button').click()
await p.waitForTimeout(1200)
console.log('清除后恢复条数:', await count(), '|', await countText())

// 快速连续改词（防抖 + 竞态）
const box = p.locator('.history-search input')
for (const term of ['图', '图书', '图书馆', '宿舍']) {
  await box.fill(term)
  await p.waitForTimeout(80)
}
await p.waitForTimeout(1500)
const finalText = await box.inputValue()
const titles = await p.locator('.conversation-title').allInnerTexts()
console.log('快速改词后输入框:', finalText, '| 结果条数:', titles.length)
console.log('结果是否都含“宿舍”:', titles.length > 0 && titles.every((t) => t.includes('宿舍')))

await b.close()
