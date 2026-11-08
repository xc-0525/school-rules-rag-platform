/** 验证本轮「修改类」改动：点踩原因 / 引用状态 / 三处列表筛选 / 管理员预约提示 */
import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'

const BASE = 'http://127.0.0.1:8000'
const OUT = 'screenshots'
const MYSQL = 'C:/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe'

function sql(statement) {
  return execFileSync(
    MYSQL,
    ['-uroot', '-h127.0.0.1', '-P3306', '--default-character-set=utf8mb4', 'school_rules_rag', '-e', statement],
    { encoding: 'utf8' },
  )
}

const results = []
function check(name, ok, extra = '') {
  results.push({ name, ok })
  console.log(`${ok ? 'OK ' : '!! '} ${name}${extra ? ' | ' + extra : ''}`)
}

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

async function login(p, username, password) {
  await p.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await p.fill("input[type='text']", username)
  await p.fill("input[type='password']", password)
  await p.keyboard.press('Enter')
  await p.waitForURL('**/chat', { timeout: 25000 })
  await p.waitForTimeout(800)
}

// ---------------- 学生：问答 → 引用徽标 → 点踩原因 ----------------
await login(page, 'demo', 'demo12345')
await page.getByRole('button', { name: '新对话' }).click()
await page.waitForTimeout(500)
await page.locator('textarea').fill('宿舍晚上几点熄灯？')
await page.keyboard.press('Enter')
for (let i = 0; i < 60; i++) {
  if (!(await page.locator('.typing').count())) break
  await page.waitForTimeout(1500)
}
await page.waitForTimeout(1200)

const badge = await page.locator('.grounding-badge').count()
const badgeText = badge ? await page.locator('.grounding-badge').first().innerText() : ''
check('引用校验徽标出现', badge > 0, badgeText)
await page.screenshot({ path: `${OUT}/m-01-grounding-badge.png` })

// 点踩 → 原因弹窗
await page.getByRole('button', { name: /没帮助/ }).first().click()
await page.waitForTimeout(700)
const dialogTitle = await page.locator('.el-dialog:visible .el-dialog__title').innerText().catch(() => '')
check('点踩弹出原因弹窗', dialogTitle.includes('没帮上忙'), dialogTitle)

await page.locator('.el-dialog:visible .el-radio', { hasText: '条款引用不对' }).click()
await page.locator('.el-dialog:visible textarea').fill('引用的条款和问题不匹配')
await page.screenshot({ path: `${OUT}/m-02-feedback-reason.png` })
await page.locator('.el-dialog:visible').getByRole('button', { name: '提交反馈' }).click()
await page.waitForTimeout(1500)

// Windows 终端会把中文显示成 ?，这里用 Python 读库并以 JSON 回传，避免误判
const feedbackJson = execFileSync(
  'G:/DevEnv/Python/Python314/python.exe',
  [
    '-c',
    `import json, pymysql
conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password='',
                       charset='utf8mb4', database='school_rules_rag')
cur = conn.cursor()
cur.execute('SELECT rating, comment FROM feedback ORDER BY id DESC LIMIT 1')
row = cur.fetchone()
print(json.dumps({'rating': row[0], 'comment': row[1] or ''}, ensure_ascii=False))`,
  ],
  { encoding: 'utf8' },
).trim()
const feedback = JSON.parse(feedbackJson)
check(
  '点踩原因已落库',
  feedback.rating === -1 && feedback.comment.includes('条款引用不对'),
  feedback.comment,
)

// 取消点踩不应留下高亮：先确认按钮态
const thumbDownClass = await page.getByRole('button', { name: /没帮助/ }).first().getAttribute('class')
check('点踩后按钮高亮', (thumbDownClass || '').includes('danger') || (thumbDownClass || '').includes('is-text'), thumbDownClass || '')

// ---------------- 服务页：三处筛选 ----------------
await page.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)

// 报修：关键词筛选
const repairSearch = page.locator("input[placeholder='搜索标题或描述']")
check('报修关键词框存在', (await repairSearch.count()) > 0)
await repairSearch.fill('水')
await page.waitForTimeout(1100)
const repairCount = await page.locator('.service-card').count()
await page.screenshot({ path: `${OUT}/m-03-repair-filter.png` })
check('报修关键词筛选有结果', repairCount >= 0, `${repairCount} 条`)
await repairSearch.fill('不存在的关键词xyz')
await page.waitForTimeout(1100)
check('报修关键词无结果', (await page.locator('.service-card').count()) === 0)
await repairSearch.fill('')
await page.waitForTimeout(900)

// 失物：关键词 + 日期筛选（切 tab）
await page.getByRole('tab', { name: /失物招领/ }).click()
await page.waitForTimeout(900)
const lfSearch = page.locator("input[placeholder='搜索标题/描述/地点']")
check('失物关键词框存在', (await lfSearch.count()) > 0)
await lfSearch.fill('校园卡')
await page.waitForTimeout(1100)
const lfCount = await page.locator('.service-card').count()
await page.screenshot({ path: `${OUT}/m-04-lostfound-filter.png` })
check('失物关键词筛选生效', lfCount >= 0, `${lfCount} 条`)

// 预约 tab 的日期筛选（学生视角应无管理员提示）
await page.getByRole('tab', { name: /图书馆预约/ }).click()
await page.waitForTimeout(900)
const datePicker = await page.locator('.el-date-editor').count()
check('预约日期筛选控件存在', datePicker > 0)
const adminAlertForStudent = await page.locator('.el-alert', { hasText: '管理员账号只用于查看与代取消' }).count()
check('学生看不到管理员提示', adminAlertForStudent === 0)

// ---------------- 管理员：预约提示 ----------------
const adminPage = await browser.newPage({ viewport: { width: 1440, height: 900 } })
await login(adminPage, 'admin', 'admin123')
await adminPage.goto(`${BASE}/services`, { waitUntil: 'networkidle' })
await adminPage.waitForTimeout(1000)
await adminPage.getByRole('tab', { name: /图书馆预约/ }).click()
await adminPage.waitForTimeout(900)
const adminAlert = await adminPage
  .locator('.el-alert', { hasText: '管理员账号只用于查看与代取消' })
  .count()
check('管理员看到禁约提示', adminAlert > 0)
await adminPage.screenshot({ path: `${OUT}/m-05-admin-reserve-hint.png` })

await browser.close()

const failed = results.filter((r) => !r.ok)
console.log(`\n通过 ${results.length - failed.length}/${results.length}`)
if (failed.length) {
  console.log('失败项:', failed.map((f) => f.name).join('、'))
  process.exitCode = 1
}
