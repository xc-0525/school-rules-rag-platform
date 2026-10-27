<template>
  <el-row :gutter="16" class="page-grid">
    <el-col :span="12" style="height: 100%">
      <div class="panel panel-pad scroll-panel">
        <div class="panel-title">运行状态</div>
        <div v-if="!health" class="muted">后端未连接，请先启动后端。</div>
        <template v-else>
          <div class="about-list">
            <div>
              服务状态：
              <el-tag :type="health.status === 'ok' ? 'success' : 'danger'" size="small" effect="plain">
                {{ health.status }}
              </el-tag>
            </div>
            <div>版本：{{ health.version }}</div>
            <div>
              运行模式：
              <el-tag size="small" effect="plain">向量 {{ health.modes.vector }}</el-tag>
              <el-tag size="small" effect="plain" style="margin-left: 6px">图谱 {{ health.modes.graph }}</el-tag>
              <el-tag size="small" effect="plain" style="margin-left: 6px">存储 {{ health.modes.persistence }}</el-tag>
            </div>
            <div>
              向量预热：
              <el-tag size="small" :type="health.vector_ready ? 'success' : 'warning'" effect="plain">
                {{ health.vector_ready ? '就绪' : '未就绪' }}
              </el-tag>
            </div>
            <div>DeepSeek Key：{{ health.llm_configured ? '已配置' : '未配置' }}</div>
            <div>
              Agent 图：{{ health.graph.node_count }} 个节点 / {{ health.graph.agent_node_count }} 个 Agent 节点
            </div>
            <div>
              控制结构：
              <el-tag
                v-for="item in health.graph.control_structures"
                :key="item"
                size="small"
                effect="plain"
                style="margin-right: 6px"
              >
                {{ item }}
              </el-tag>
            </div>
          </div>
        </template>

        <el-divider />
        <div class="panel-title">接口一览</div>
        <div class="about-list muted">
          <div>POST /api/v1/chat —— 多智能体问答（SSE）</div>
          <div>GET /api/v1/graph —— Agent 图定义</div>
          <div>GET /api/v1/conversations —— 会话历史</div>
          <div>POST /api/v1/documents/upload —— 文档入库</div>
          <div>GET /api/v1/documents —— 文档列表</div>
          <div>GET /health —— 健康检查</div>
        </div>
      </div>
    </el-col>

    <el-col :span="12" style="height: 100%">
      <div class="panel panel-pad scroll-panel">
        <div class="panel-title">系统说明</div>
        <el-alert type="success" :closable="false" show-icon style="margin-bottom: 14px">
          <template #title>课程答辩演示要点</template>
          <div class="upload-hint">
            1. Agent 图共 13 节点，含 <b>并行（双路召回）</b>、<b>选择（充分性判定/缓存）</b>、
            <b>循环（查询扩展、引用校验重生成）</b>；<br />
            2. 真实链路：Qdrant 本地嵌入式 + 本地 BGE + Neo4j 图谱 + MySQL 落库 + DeepSeek；<br />
            3. 防幻觉：无证据拒答、引用校验、降级提示；<br />
            4. 现场建议先问"宿舍晚上几点熄灯？"，展示 Agent 轨迹与两类来源引用。
          </div>
        </el-alert>

        <div class="panel-title">启动顺序（本机）</div>
        <pre class="muted" style="background: #f8fafc; padding: 12px; border-radius: 10px; line-height: 1.8">
1. scripts\start_neo4j.ps1        # 启动 Neo4j
2. scripts\start_backend.ps1      # 启动后端（首次预热 BGE 约 2~3 分钟）
3. cd frontend; npm run dev       # 启动前端开发服务器</pre>

        <div class="panel-title" style="margin-top: 16px">已知限制</div>
        <div class="about-list muted">
          <div>· 尚无登录鉴权（演示固定用户 id=1）</div>
          <div>· 扫描件 PDF 需安装 OCR 引擎</div>
          <div>· 语义缓存实际为问题归一化精确匹配</div>
          <div>· 本地 MySQL 为 5.5.11，建表脚本已兼容</div>
        </div>
      </div>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchHealth } from '../api/client'
import type { HealthInfo } from '../types'

const health = ref<HealthInfo | null>(null)

onMounted(async () => {
  try {
    health.value = await fetchHealth()
  } catch {
    health.value = null
  }
})
</script>
