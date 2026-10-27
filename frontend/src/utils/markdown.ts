import MarkdownIt from 'markdown-it'

// 关闭 raw HTML，避免大模型输出被当作 HTML 执行
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: false,
})

export function renderMarkdown(text: string): string {
  return md.render(text || '')
}
