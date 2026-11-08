// 生成答辩演示用 PDF（对齐入库解析：首行即标题，条前不出现非章节标题）
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const OUT = 'G:/毕业设计/校纪校规问答系统/非代码资料/演示上传PDF'
mkdirSync(OUT, { recursive: true })

const CSS = `
  @page { size: A4; margin: 24mm 22mm; }
  body { font-family: "Microsoft YaHei", "SimSun", serif; color: #1a1a1a; font-size: 12pt; line-height: 2.0; }
  h1 { text-align: center; font-size: 19pt; font-weight: 700; margin: 0 0 3mm; line-height: 1.6; }
  .rule { width: 100%; height: 1px; background: #8a1c1c; margin: 0 0 9mm; }
  h2 { font-size: 13pt; font-weight: 700; margin: 7mm 0 2mm; }
  p { margin: 0 0 3.2mm; text-indent: 2em; text-align: justify; }
  p.art { text-indent: 0; }
`

function html(doc) {
  const body = doc.sections
    .map(
      (sec) => `
      ${sec.title ? `<h2>${sec.title}</h2>` : ''}
      ${sec.articles.map((a) => `<p class="art">${a}</p>`).join('')}
    `,
    )
    .join('')
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>${CSS}</style></head>
  <body><h1>${doc.title}</h1><div class="rule"></div>${body}</body></html>`
}

const docs = [
  {
    file: '01-学生校园网络与信息安全管理办法（试行）.pdf',
    title: '学生校园网络与信息安全管理办法（试行）',
    sections: [
      {
        title: '第一章　总则',
        articles: [
          '第一条　为规范校园网络使用行为，保障网络与信息安全，维护学校和师生的合法权益，根据国家有关法律法规，结合学校实际，制定本办法。',
          '第二条　本办法适用于本校全体在校学生使用校园网、校园信息系统及公共计算设备的各类行为。',
        ],
      },
      {
        title: '第二章　账号与使用规范',
        articles: [
          '第三条　校园网账号实行实名制管理，一人一号，不得转借、出租、出售或与他人共用。因转借账号造成的后果由账号所有人承担。',
          '第四条　不得利用校园网从事下列行为：攻击或扫描校园信息系统；传播计算机病毒；搭建代理或翻墙服务；大量下载与学习无关的资源占用带宽。',
          '第五条　宿舍网络出现故障，应通过“校园服务—寝室报修”提交，网络信息中心在接到报修后 24 小时内响应，节假日顺延。',
        ],
      },
      {
        title: '第三章　处理与申诉',
        articles: [
          '第六条　违反本办法的，视情节给予批评教育、暂停校园网使用权限 7 至 30 日等处理；造成设备损坏或数据损失的，应予以赔偿。',
          '第七条　学生对处理结果有异议的，可自收到处理通知之日起 5 个工作日内向学生工作处提出书面申诉。',
          '第八条　本办法自 2026 年 12 月 1 日起施行，由网络信息中心负责解释。',
        ],
      },
    ],
  },
  {
    file: '02-图书馆自习座位预约补充规定.pdf',
    title: '图书馆自习座位预约补充规定',
    sections: [
      {
        title: '',
        articles: [
          '第一条　预约成功后，应在预约时段开始后 15 分钟内到馆签到；逾期未签到的，系统自动释放该座位，并记为一次未签到。',
          '第二条　连续 3 次预约未签到的，暂停预约权限 7 天；一学期内累计 5 次的，本学期不再受理其座位预约申请。',
          '第三条　因课程、实习、就医等正当事由无法按时到馆的，应至少提前 1 小时在系统中取消预约；未取消且未签到的，按未签到处理。',
          '第四条　本规定自发布之日起执行，由图书馆负责解释；此前发布的预约要求与本规定不一致的，以本规定为准。',
        ],
      },
    ],
  },
  {
    file: '03-校园电动车停放与充电管理规定.pdf',
    title: '校园电动车停放与充电管理规定',
    sections: [
      {
        title: '',
        articles: [
          '第一条　电动自行车应停放在划定的非机动车停放区域，不得停放在宿舍楼门厅、楼道、楼梯间、消防通道及机动车位。',
          '第二条　禁止在宿舍、实验室、楼道、地下室为电动自行车或蓄电池充电，禁止从室内拉接电线充电（飞线充电）。充电应使用校内集中充电设施。',
          '第三条　电动自行车进入校园应进行登记并悬挂校内通行标识，未登记的车辆不得进入校园。',
          '第四条　违反本规定停放或充电的，保卫处可暂扣车辆并进行通报；造成火灾、人员伤亡等后果的，依法追究法律责任。',
          '第五条　本规定自 2026 年 12 月 1 日起施行，由保卫处负责解释。',
        ],
      },
    ],
  },
]

const browser = await chromium.launch()
const page = await browser.newPage()
for (const doc of docs) {
  await page.setContent(html(doc), { waitUntil: 'load' })
  const path = resolve(OUT, doc.file)
  await page.pdf({
    path,
    format: 'A4',
    printBackground: true,
    margin: { top: '22mm', bottom: '20mm', left: '20mm', right: '20mm' },
  })
  console.log('PDF:', path)
}
await browser.close()
