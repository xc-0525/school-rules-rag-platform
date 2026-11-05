/** 造一条待审核失物 → 管理员用下拉原因驳回，全程截图 */
import { chromium } from 'playwright'
import { resolve } from 'node:path'

const BASE = 'http://127.0.0.1:8000'
const OUT = resolve('screenshots')
const b = await chromium.launch()

async function login(page, user, pwd) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await page.evaluate(() => localStorage.clear())
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await page.fill("input[type='text']", user)
  await page.fill("input[type='password']", pwd)
  await page.keyboard.press('Enter')
  await page.waitForURL('**/chat', { timeout: 20000 })
}

// 1) 学生发一条，制造待审核
const s = await b.newPage({ viewport: { width: 1440, height: 900 } })
await login(s, 'demo', 'demo12345')
await s.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await s.getByRole('tab', { name: /失物招领/ }).click()
await s.waitForTimeout(600)
await s.getByRole('button', { name: '发布申请' }).click()
await s.waitForTimeout(400)
await s.getByPlaceholder('如：捡到一张校园卡').fill('【UI验证】桂园三楼捡到一把宿舍钥匙')
await s.getByPlaceholder('丢失 / 拾获地点').fill('桂园三楼走廊')
await s.getByPlaceholder('QQ / 微信 / 手机号').fill('QQ 12345678')
await s
  .getByPlaceholder(/至少 10 个字/)
  .fill('在教学楼走廊窗台捡到一把宿舍钥匙，挂蓝色钥匙扣，请失主联系我认领。')
await s.getByRole('button', { name: '提交审核' }).click()
await s.waitForTimeout(1200)
console.log('学生提交成功提示:', await s.locator('.el-message--success').count())
await s.close()

// 2) 管理员驳回
const p = await b.newPage({ viewport: { width: 1440, height: 900 } })
await login(p, 'admin', 'admin123')
await p.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await p.getByRole('tab', { name: /失物招领/ }).click()
await p.waitForTimeout(700)
await p.locator('.el-radio-button__inner', { hasText: '待审核' }).first().click()
await p.waitForTimeout(900)

const rejects = p.getByRole('button', { name: '驳回' })
console.log('待审核条目数:', await rejects.count())
if (!(await rejects.count())) {
  await b.close()
  process.exit(0)
}

await rejects.first().click()
await p.waitForTimeout(600)
await p.screenshot({ path: `${OUT}/ui-07-lf-reject-dialog.png` })

await p.locator('.el-dialog:has-text("驳回失物招领") .el-select').click()
await p.waitForTimeout(400)
const options = (await p.locator('.el-select-dropdown__item:visible').allInnerTexts()).filter(Boolean)
const presets = options.filter((t) => t.includes('信息不完整') || t.includes('描述过于笼统') || t.includes('联系方式无效') || t.includes('疑似重复发布') || t.includes('隐私信息') || t.includes('不属于失物招领'))
console.log('预设原因条数:', presets.length)
console.log('示例:', presets.slice(0, 2).join(' | '))
await p.screenshot({ path: `${OUT}/ui-08-lf-reject-options.png` })

await p.locator('.el-select-dropdown__item:visible', { hasText: '信息不完整' }).first().click()
await p.waitForTimeout(300)
await p.getByRole('button', { name: '确认驳回' }).click()
await p.waitForTimeout(1300)
console.log('弹窗已关闭:', (await p.locator('.el-dialog:visible:has-text("驳回失物招领")').count()) === 0)
await p.screenshot({ path: `${OUT}/ui-09-lf-rejected.png` })

// 3) 学生端确认驳回原因可见
const s2 = await b.newPage({ viewport: { width: 1440, height: 900 } })
await login(s2, 'demo', 'demo12345')
await s2.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await s2.getByRole('tab', { name: /失物招领/ }).click()
await s2.waitForTimeout(600)
await s2.locator('.el-radio-button__inner', { hasText: '我的发布' }).first().click()
await s2.waitForTimeout(900)
const remark = s2.locator('.notice-foot:has-text("审核意见")')
console.log('学生端可见审核意见条数:', await remark.count())
if (await remark.count()) console.log('文案:', (await remark.first().innerText()).replace(/\s+/g, ' ').slice(0, 80))
await s2.screenshot({ path: `${OUT}/ui-10-lf-reject-student.png` })

await b.close()
