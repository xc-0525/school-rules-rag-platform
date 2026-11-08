// 工单提交冒烟：登录 → 校园服务 → 我的工单 → 提工单 → 校验列表
import { chromium } from 'playwright'

const BASE = process.env.SMOKE_BASE || 'http://localhost:5173'
const title = `冒烟工单 ${Date.now()}`

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))

await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await page.fill("input[type='text']", 'demo')
await page.fill("input[type='password']", 'demo12345')
await page.keyboard.press('Enter')
await page.waitForURL('**/chat', { timeout: 20000 })

await page.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
const later = page.getByRole('button', { name: '稍后再说' })
if (await later.count()) {
  await later.click()
  await page.waitForTimeout(400)
}
await page.getByRole('tab', { name: '我的工单' }).click()
await page.waitForTimeout(500)
await page.getByRole('button', { name: '提工单' }).click()
await page.waitForTimeout(400)

const dialog = page.locator('.el-dialog:visible')
await dialog.locator('input[type="text"]').first().fill(title)
await dialog.locator('textarea').fill('答辩前冒烟：宿舍走廊熄灯后仍有噪音，希望宿管加强巡查。')
await dialog.getByRole('button', { name: '提交' }).click()

const ok = await page.locator('.el-message--success').first().innerText().catch(() => '')
await page.waitForTimeout(800)
const body = await page.locator('body').innerText()

console.log('toast:', ok.replace(/\n/g, ' '))
console.log('列表含新工单:', body.includes(title))
console.log('控制台错误:', errors.length ? errors.slice(0, 2).join(' | ') : '无')

await browser.close()
process.exit(ok && body.includes(title) && !errors.length ? 0 : 1)
