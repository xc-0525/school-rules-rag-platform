// 开发辅助：不依赖 esbuild，用 Vue 官方编译器校验所有 .vue（沙箱内也能跑）
const fs = require('fs')
const path = require('path')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const root = path.join(__dirname, '..', 'src')

function walk(dir) {
  const result = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) result.push(...walk(full))
    else result.push(full)
  }
  return result
}

let failed = 0
const files = walk(root).filter((file) => file.endsWith('.vue'))
for (const file of files) {
  const source = fs.readFileSync(file, 'utf8')
  const { descriptor, errors } = parse(source, { filename: file })
  if (errors.length) {
    failed += 1
    console.log('PARSE_FAIL', path.relative(root, file), errors.map(String).join('; '))
    continue
  }
  try {
    const id = path.basename(file)
    if (descriptor.script || descriptor.scriptSetup) {
      compileScript(descriptor, { id, inlineTemplate: false })
    }
    if (descriptor.template) {
      const result = compileTemplate({
        source: descriptor.template.content,
        filename: file,
        id,
        compilerOptions: {},
      })
      if (result.errors.length) {
        failed += 1
        console.log('TEMPLATE_FAIL', path.relative(root, file), result.errors.map(String).join('; '))
        continue
      }
    }
    console.log('OK', path.relative(root, file))
  } catch (error) {
    failed += 1
    console.log('COMPILE_FAIL', path.relative(root, file), error.message)
  }
}

console.log(`\nSFC files: ${files.length}, failed: ${failed}`)
process.exit(failed ? 1 : 0)
