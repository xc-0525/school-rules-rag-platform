/** 复现：复合取消+预约请求，检查 tool_drafts 是否生成 */
const BASE = 'http://127.0.0.1:8000'

const login = await fetch(`${BASE}/api/v1/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'demo', password: 'demo12345' }),
})
const { token } = await login.json()
const H = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

const chat = await fetch(`${BASE}/api/v1/chat`, {
  method: 'POST',
  headers: H,
  body: JSON.stringify({
    query: '取消我今天下午的研讨间预约',
    stream: false,
  }),
})
const body = await chat.json()
console.log('status:', chat.status)
console.log('answer:', (body.answer || '').replace(/\s+/g, ' ').slice(0, 300))
console.log('tool_drafts:', JSON.stringify(body.tool_drafts, null, 2)?.slice(0, 1200))
console.log('runtime:', JSON.stringify(body.runtime || {}).slice(0, 900))
