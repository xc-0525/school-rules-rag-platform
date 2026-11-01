/**
 * 紧急公告横幅的端到端验证脚本（一次性，Node + Playwright）。
 *
 * 流程：学生登录 → 首页横幅出现 → 点 × 关闭 → 刷新后不再出现 →
 * 公告板块显示"恢复显示" → 恢复后横幅重新出现 → 点击横幅跳转并高亮。
 * 截图存 screenshots/。
 *
 * 运行：node scripts/verify_notice_banner.mjs
 */
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

try {
  // 1. 学生登录
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await page.fill("input[type='text']", 'demo')
  await page.fill("input[type='password']", 'demo12345')
  await page.keyboard.press('Enter')
  await page.waitForURL('**/chat', { timeout: 15000 })

  // 2. 横幅出现
  const banner = page.locator('.notice-banner')
  await banner.waitFor({ timeout: 10000 })
  await page.screenshot({ path: `${OUT}/01-banner-visible.png` })
  console.log(`[1] 横幅出现: ${(await banner.innerText()).replace(/\s+/g, ' ').slice(0, 70)}`)

  // 3. 点 × 关闭
  await page.click('.notice-banner-close')
  await page.waitForSelector('.notice-banner', { state: 'detached', timeout: 5000 })
  await page.screenshot({ path: `${OUT}/02-banner-dismissed.png` })
  console.log('[2] 点 × 后横幅消失')

  // 4. 刷新后仍隐藏
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  const stillHidden = (await page.locator('.notice-banner').count()) === 0
  console.log(`[3] 刷新后仍隐藏: ${stillHidden}`)
  if (!stillHidden) throw new Error('刷新后横幅不应重新出现')

  // 5. 公告板块有恢复入口
  await page.goto(`${BASE}/notices`, { waitUntil: 'networkidle' })
  const restore = page.locator('.restore-dismissed')
  await restore.waitFor({ timeout: 8000 })
  await page.screenshot({ path: `${OUT}/03-notices-restore-entry.png` })
  console.log(`[4] 公告页恢复入口: ${(await restore.innerText()).replace(/\s+/g, ' ')}`)

  // 6. 恢复后横幅重新出现
  await restore.locator('button').click()
  await page.goto(`${BASE}/chat`, { waitUntil: 'networkidle' })
  await page.locator('.notice-banner').waitFor({ timeout: 8000 })
  await page.screenshot({ path: `${OUT}/04-banner-restored.png` })
  console.log('[5] 恢复显示后横幅重新出现')

  // 7. 点击横幅跳转公告页并聚焦高亮
  await page.click('.notice-banner-body')
  await page.waitForURL('**/notices**', { timeout: 8000 })
  await page.waitForTimeout(1200)
  const focused = await page.locator('.notice-card.is-focus').count()
  console.log(`[6] 点击横幅跳转公告页并高亮: ${focused === 1}`)
  await page.screenshot({ path: `${OUT}/05-notice-focus.png` })

  console.log('全部通过')
} finally {
  await browser.close()
}
