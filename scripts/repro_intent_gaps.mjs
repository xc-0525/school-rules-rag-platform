/** 复现两个问题：①考场带手机拒答；②“两个都取消然后预约A区”被拒答 */
const BASE = 'http://127.0.0.1:8000'

const login = await fetch(`${BASE}/api/v1/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'student', password: 'student123' }),
})
const { token } = await login.json()
const H = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

async function chat(query, conversationId) {
  const res = await fetch(`${BASE}/api/v1/chat`, {
    method: 'POST',
    headers: H,
    body: JSON.stringify({ query, stream: false, conversation_id: conversationId }),
  })
  return res.json()
}

function trace(body, tag) {
  console.log(`\n===== ${tag} =====`)
  console.log('query:', body.query, '| status:', body.status || '')
  console.log('answer:', (body.answer || '').replace(/\s+/g, ' ').slice(0, 180))
  console.log('检索串:', body.retrieval_query || '(未返回)')
  console.log('生成串:', body.generation_query || '(未返回)')
  const nodes = body.runtime?.nodes || []
  for (const n of nodes) {
    const d = (n.detail || '').replace(/\s+/g, ' ').slice(0, 110)
    console.log(`  [${n.node}] ${d}`)
  }
  const calls = body.runtime?.tool_calls
  console.log('  工具调用数:', typeof calls === 'number' ? calls : Array.isArray(calls) ? calls.length : 0)
  console.log('草稿:', (body.tool_drafts || []).map((d) => `${d.action}:${d.title}`).join(' | ') || '无')
  console.log('来源数:', (body.sources || []).length)
}

// ---------- 问题1：单问考场带手机 ----------
const q1 = await chat('考试时带手机进考场会怎么处理？')
trace(q1, '问题1-轮1 考场带手机')

const q1b = await chat('那你告诉我关于学生重评方面的事', q1.conversation_id)
trace(q1b, '问题1-轮2 学生重评')

// ---------- 问题2：两轮办理 ----------
const q2 = await chat('帮我取消明天的图书馆预约我想预约后天的研讨室')
trace(q2, '问题2-轮1 取消+预约研讨室')

const q2b = await chat('那你帮我将两个都取消了然后预约后天下午的图书馆a区座位', q2.conversation_id)
trace(q2b, '问题2-轮2 都取消+约A区')
