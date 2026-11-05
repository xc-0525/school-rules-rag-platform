/** 验证：管理员禁约 + 预约人筛选/搜索 */
import { chromium } from 'playwright'
import { resolve } from 'node:path'
const BASE = 'http://127.0.0.1:8000'
const OUT = resolve('screenshots')
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1440, height: 900 } })

await p.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await p.fill("input[type='text']", 'admin'); await p.fill("input[type='password']", 'admin123')
await p.keyboard.press('Enter'); await p.waitForURL('**/chat', { timeout: 20000 })
await p.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await p.click(".el-tabs__item:has-text('图书馆预约')"); await p.waitForTimeout(1500)

const disabled = await p.locator('.slot-row button[disabled]').count()
const total = await p.locator('.slot-row button').count()
console.log('[管理员] 时段按钮 disabled/总数:', disabled, '/', total)
console.log('[管理员] 提示:', (await p.locator('.reserve-layout .muted').first().innerText()).replace(/\s+/g, ' '))
console.log('[管理员] 筛选栏:', (await p.locator('.reserve-filter').innerText()).replace(/\s+/g, ' ').slice(0, 80))
await p.screenshot({ path: `${OUT}/60-admin-filter-bar.png` })

// 按预约人筛选
await p.click('.reserve-filter .el-select')
await p.waitForTimeout(500)
const opts = await p.locator('.el-select-dropdown__item').allInnerTexts()
console.log('[管理员] 预约人选项:', opts.slice(0, 5).join(' | '))
await p.locator('.el-select-dropdown__item').first().click()
await p.waitForTimeout(700)
console.log('[筛选后] 行数:', await p.locator('.el-table__body tr').count())
await p.screenshot({ path: `${OUT}/61-admin-filtered.png` })

// 关键词搜索
await p.fill('.reserve-filter input[placeholder*="搜索"]', '研讨间')
await p.waitForTimeout(600)
console.log('[搜索 研讨间] 行数:', await p.locator('.el-table__body tr').count())
await b.close()
