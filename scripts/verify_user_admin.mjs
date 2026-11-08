/** 验收用户管理页：管理员可见可改、学生不可见且手输 URL 被拦 */
import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'

const BASE = 'http://127.0.0.1:8000'
const OUT = 'screenshots'
const TEMP_USER = 'uicheck_stu'
const TEMP_PASS = 'uicheck-pass-1'
const NEW_PASS = 'uicheck-pass-2'
const MYSQL = 'C:/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe'
const PYTHON = 'G:/DevEnv/Python/Python314/python.exe'

function sql(statement) {
  return execFileSync(
    MYSQL,
    ['-uroot', '-h127.0.0.1', '-P3306', '--default-character-set=utf8mb4', 'school_rules_rag', '-e', statement],
    { encoding: 'utf8' },
  )
}

function createTempUser() {
  const code = `
import sys
sys.path.insert(0, '.')
from app.core.security import hash_password
import pymysql

conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password='',
                       charset='utf8mb4', database='school_rules_rag', autocommit=True)
cur = conn.cursor()
cur.execute("DELETE FROM users WHERE username=%s", ('${TEMP_USER}',))
cur.execute(
    "INSERT INTO users (username, password, nickname, role, status, updated_at) "
    "VALUES (%s, %s, %s, 'student', 1, NOW())",
    ('${TEMP_USER}', hash_password('${TEMP_PASS}'), 'UI 验证账号'),
)
print(cur.lastrowid)
`
  return execFileSync(PYTHON, ['-c', code], { cwd: 'backend', encoding: 'utf8' }).trim()
}

function dropTempUser() {
  sql(`DELETE FROM users WHERE username='${TEMP_USER}';`)
}

dropTempUser()
console.log('临时账号 id:', createTempUser())

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

/** 在用户管理页定位某一行的操作按钮 */
function rowOf(p, text) {
  return p.locator('.el-table__body tr', { hasText: text }).first()
}

// ---------- 管理员 ----------
await login(page, 'admin', 'admin123')
const adminMenu = await page.locator('.app-menu .el-menu-item').allInnerTexts()
console.log('管理员菜单含用户管理:', adminMenu.some((t) => t.includes('用户管理')))

await page.goto(`${BASE}/admin/users`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)
console.log('页面标题:', await page.locator('.panel-title').first().innerText())
console.log('首屏用户行数:', await page.locator('.el-table__body tr').count())
await page.screenshot({ path: `${OUT}/ui-20-admin-users.png` })

// 搜索临时账号
await page.fill("input[placeholder='搜索用户名或昵称']", TEMP_USER)
await page.waitForTimeout(900)
console.log('搜索命中行数:', await page.locator('.el-table__body tr').count())

// 编辑：改昵称 + 提为管理员
await rowOf(page, TEMP_USER).getByRole('button', { name: '编辑' }).click()
await page.waitForTimeout(600)
await page.locator('.el-dialog:visible input[maxlength="50"]').first().fill('UI 验证改名')
await page.locator('.el-dialog:visible .ua-form-row', { hasText: '角色' }).locator('.el-select').click()
await page.waitForTimeout(400)
await page.locator('.el-select-dropdown__item:visible', { hasText: '管理员' }).first().click()
await page.screenshot({ path: `${OUT}/ui-21-admin-user-edit.png` })
await page.locator('.el-dialog:visible').getByRole('button', { name: '保存' }).click()
await page.waitForTimeout(1500)
console.log('保存后行:', (await rowOf(page, TEMP_USER).innerText()).replace(/\s+/g, ' ').slice(0, 70))
await page.screenshot({ path: `${OUT}/ui-22-admin-user-updated.png` })

// 重置密码
await rowOf(page, TEMP_USER).getByRole('button', { name: '重置密码' }).click()
await page.waitForTimeout(600)
await page.locator('.el-dialog:visible input[type="password"]').nth(0).fill(NEW_PASS)
await page.locator('.el-dialog:visible input[type="password"]').nth(1).fill(NEW_PASS)
await page.screenshot({ path: `${OUT}/ui-23-admin-reset-password.png` })
await page.locator('.el-dialog:visible').getByRole('button', { name: '确认重置' }).click()
await page.waitForTimeout(1500)
console.log('重置结果:', (await page.locator('.el-message').allInnerTexts()).join(' | ').slice(0, 70))

// 改回学生（让学生能正常登录后又可被拦截验证）
await rowOf(page, TEMP_USER).getByRole('button', { name: '编辑' }).click()
await page.waitForTimeout(600)
await page.locator('.el-dialog:visible .ua-form-row', { hasText: '角色' }).locator('.el-select').click()
await page.waitForTimeout(400)
await page.locator('.el-select-dropdown__item:visible', { hasText: '学生' }).first().click()
await page.locator('.el-dialog:visible').getByRole('button', { name: '保存' }).click()
await page.waitForTimeout(1500)
console.log('改回学生:', (await rowOf(page, TEMP_USER).innerText()).replace(/\s+/g, ' ').slice(0, 70))

// ---------- 学生：菜单不可见 + 手输 URL 被拦 ----------
const stuPage = await browser.newPage({ viewport: { width: 1440, height: 900 } })
await login(stuPage, TEMP_USER, NEW_PASS)
const stuMenuItems = await stuPage.locator('.app-menu .el-menu-item').allInnerTexts()
console.log('学生菜单含用户管理:', stuMenuItems.some((t) => t.includes('用户管理')))
await stuPage.goto(`${BASE}/admin/users`, { waitUntil: 'networkidle' })
await stuPage.waitForTimeout(1200)
console.log('学生访问 /admin/users 后落在:', new URL(stuPage.url()).pathname)
await stuPage.screenshot({ path: `${OUT}/ui-24-student-blocked.png` })

// ---------- 回到管理员：状态开关（禁用→启用） ----------
await page.bringToFront()
await page.locator('.el-table__body tr').first().locator('.el-switch').click()
await page.waitForTimeout(1500)
console.log('禁用后开关状态:', await page.locator('.el-table__body tr').first().locator('.el-switch').getAttribute('class'))
await page.screenshot({ path: `${OUT}/ui-25-admin-user-disabled.png` })
await page.locator('.el-table__body tr').first().locator('.el-switch').click()
await page.waitForTimeout(1200)

await browser.close()
dropTempUser()
console.log('临时账号已清理')
