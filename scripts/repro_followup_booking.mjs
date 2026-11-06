/** 复现用户场景：多轮办理中的短回复（“我想约202”）应继续走预约工具 */
const BASE = 'http://127.0.0.1:8000'

const login = await fetch(`${BASE}/api/v1/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'student', password: 'student123' }),
})
const loginBody = await login.json()
const token = loginBody.token
console.log('登录:', login.status, loginBody.user?.username ?? '')
const H = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

async function chat(query, conversationId) {
  const res = await fetch(`${BASE}/api/v1/chat`, {
    method: 'POST',
    headers: H,
    body: JSON.stringify({ query, stream: false, conversation_id: conversationId }),
  })
  const body = await res.json()
  return body
}

// 轮 1：用户原话（取消可能失败——那两条已被取消——但会引出余量列表）
const r1 = await chat('取消一下今天晚上和明天下午的图书馆预约 我想预约后天晚上的研讨室')
console.log('轮1 answer:', (r1.answer || '').replace(/\s+/g, ' ').slice(0, 160))
console.log('轮1 tool_drafts:', (r1.tool_drafts || []).length)

// 轮 2：短回复“我想约202”
const r2 = await chat('我想约202', r1.conversation_id)
console.log('轮2 answer:', (r2.answer || '').replace(/\s+/g, ' ').slice(0, 200))
console.log(
  '轮2 tool_drafts:',
  JSON.stringify((r2.tool_drafts || []).map((d) => ({ action: d.action, title: d.title, description: d.description }))),
)
