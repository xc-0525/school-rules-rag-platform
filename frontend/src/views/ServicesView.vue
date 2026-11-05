<template>
  <div class="panel panel-pad" style="height: 100%; overflow-y: auto">
    <div class="detail-head" style="margin-bottom: 12px">
      <div>
        <div class="panel-title" style="margin-bottom: 4px">校园服务</div>
        <div class="muted">寝室报修 · 图书馆位置预约 · 失物招领，提交后可在对应页签跟踪进度</div>
      </div>
    </div>

    <el-tabs v-model="tab" @tab-change="onTabChange">
      <!-- ================= 寝室报修 ================= -->
      <el-tab-pane label="寝室报修" name="repairs">
        <div class="service-toolbar">
          <el-button type="primary" @click="repairDialog = true">提交报修申请</el-button>
          <el-checkbox v-if="auth.isAdmin" v-model="repairAll" @change="loadRepairs">
            查看全部学生报修
          </el-checkbox>
        </div>
        <el-empty v-if="!repairs.length" description="暂无报修记录" :image-size="80" />
        <div v-for="item in repairs" :key="item.id" class="service-card">
          <div class="notice-head">
            <el-tag size="small" effect="plain" type="primary">{{ item.category_label }}</el-tag>
            <el-tag size="small" :type="repairStatusType(item.status)">{{ repairStatusLabel(item.status) }}</el-tag>
            <span class="notice-title">{{ item.title }}</span>
            <span class="notice-time">{{ item.building }} {{ item.room }} · {{ item.created_at }}</span>
          </div>
          <div class="notice-content">{{ item.description }}</div>
          <div class="notice-foot">
            <span>联系人：{{ item.contact }}</span>
            <span v-if="item.handler_remark">· 处理意见：{{ item.handler_remark }}</span>
            <span v-if="auth.isAdmin && (item.status === 'pending' || item.status === 'processing')" class="notice-admin">
              <el-button v-if="item.status === 'pending'" link type="primary" size="small" @click="actRepair(item, 'process')">受理</el-button>
              <el-button v-else link type="success" size="small" @click="actRepair(item, 'complete')">完成</el-button>
              <el-button link type="danger" size="small" @click="actRepair(item, 'close')">关闭</el-button>
            </span>
            <span v-if="!auth.isAdmin && item.user_id === auth.user?.id && item.status === 'pending'" class="notice-admin">
              <el-button link type="info" size="small" @click="actRepair(item, 'cancel')">取消申请</el-button>
            </span>
          </div>
        </div>
      </el-tab-pane>

      <!-- ================= 图书馆预约 ================= -->
      <el-tab-pane label="图书馆预约" name="reservations">
        <div class="reserve-layout">
          <div class="reserve-left">
            <div class="muted" style="margin-bottom: 8px">
              <template v-if="auth.isAdmin">
                管理员账号不参与座位预约；下方可查看全部预约并代学生取消
              </template>
              <template v-else>
                可预约 {{ tomorrow }} 起未来 7 天的时段；每个时段剩余名额实时显示，约满后不可再约
              </template>
            </div>
            <div
              v-for="loc in locations"
              :key="loc.id"
              class="location-card"
              :class="{ active: selectedLocation?.id === loc.id }"
              @click="selectLocation(loc)"
            >
              <div>
                <b>{{ loc.name }}</b>
                <span class="muted" style="margin-left: 8px">{{ loc.zone }} · 容量 {{ loc.capacity }}</span>
              </div>
            </div>
          </div>
          <div class="reserve-right">
            <template v-if="selectedLocation">
              <div class="reserve-date">
                <el-date-picker
                  v-model="reserveDate"
                  type="date"
                  :disabled-date="isDateDisabled"
                  value-format="YYYY-MM-DD"
                  @change="loadAvailability"
                />
              </div>
              <div class="slot-row">
                <el-button
                  v-for="s in availability.slots"
                  :key="s.slot"
                  :type="s.booked ? 'info' : 'primary'"
                  :disabled="s.booked || auth.isAdmin"
                  plain
                  @click="book(selectedLocation!, s.slot)"
                >
                  {{ s.label }}
                  <span class="slot-left" :class="{ full: s.booked }">
                    {{ s.booked ? '已约满' : `余 ${s.remaining ?? '-'}/${s.capacity ?? '-'}` }}
                  </span>
                </el-button>
              </div>
              <div v-if="availability.range_note" class="muted">{{ availability.range_note }}</div>
            </template>
            <el-empty v-else description="先在左侧选择位置" :image-size="80" />
          </div>
        </div>

        <div class="panel-subtitle">
          {{ auth.isAdmin ? '全部预约（管理员）' : '我的预约' }}
          <span v-if="expiredCount" class="history-toggle">
            <el-switch v-model="showExpired" size="small" />
            <span>{{ showExpired ? '收起已过期' : `显示已过期（${expiredCount}）` }}</span>
          </span>
        </div>
        <div v-if="auth.isAdmin" class="reserve-filter">
          <el-select
            v-model="resFilterUser"
            clearable
            filterable
            placeholder="全部预约人"
            style="width: 180px"
          >
            <el-option
              v-for="u in resUsers"
              :key="u.name"
              :label="`${u.name}（${u.count}）`"
              :value="u.name"
            />
          </el-select>
          <el-select v-model="resFilterStatus" clearable placeholder="全部状态" style="width: 140px">
            <el-option label="已预约" value="reserved" />
            <el-option label="已过期" value="expired" />
            <el-option label="已取消" value="cancelled" />
          </el-select>
          <el-input
            v-model="resKeyword"
            clearable
            placeholder="搜索预约人 / 位置 / 日期"
            style="width: 240px"
          />
          <span class="muted" style="font-size: 12px">共 {{ visibleReservations.length }} 条</span>
        </div>
        <el-table :data="visibleReservations" size="small" stripe>
          <el-table-column v-if="auth.isAdmin" prop="username" label="预约人" width="110">
            <template #default="{ row }">{{ row.username || `#${row.user_id}` }}</template>
          </el-table-column>
          <el-table-column prop="location_name" label="位置" min-width="150" />
          <el-table-column prop="res_date" label="日期" width="110" />
          <el-table-column label="时段" width="170">
            <template #default="{ row }">
              <span class="slot-cell">
                <el-tag size="small" effect="plain" :type="slotTagType(row.slot)">{{ slotShort(row.slot) }}</el-tag>
                <span class="muted">{{ slotTime(row.slot) }}</span>
              </span>
            </template>
          </el-table-column>
          <el-table-column label="状态" min-width="150">
            <template #default="{ row }">
              <el-tag size="small" :type="resStatusType(row)">{{ resStatusLabel(row) }}</el-tag>
              <span v-if="row.cancel_reason" class="muted" style="margin-left: 6px; font-size: 12px">
                {{ row.cancel_reason }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="110">
            <template #default="{ row }">
              <el-button
                v-if="row.status === 'reserved' && !row.is_expired"
                link
                type="danger"
                size="small"
                @click="cancelReservation(row)"
              >
                {{ auth.isAdmin ? '代取消' : '取消' }}
              </el-button>
            </template>
          </el-table-column>
          <template #empty>还没有预约</template>
        </el-table>
      </el-tab-pane>

      <!-- ================= 失物招领 ================= -->
      <el-tab-pane :label="lfTabLabel" name="lostfound">
        <div class="service-toolbar">
          <el-radio-group v-model="lfScope" size="small" @change="loadLostFound">
            <el-radio-button label="public">公示栏</el-radio-button>
            <el-radio-button label="mine">我的发布</el-radio-button>
            <el-radio-button v-if="auth.isAdmin" label="pending">待审核</el-radio-button>
          </el-radio-group>
          <el-button type="primary" @click="openLfDialog">发布申请</el-button>
        </div>
        <el-alert
          v-if="lfScope === 'mine' && timedOutItems.length"
          type="warning"
          :closable="false"
          show-icon
          style="margin-bottom: 12px"
          :title="`有 ${timedOutItems.length} 条发布超过 48 小时未审核，已提醒管理员尽快处理`"
        />
        <el-empty v-if="!lostFound.length" description="暂无内容" :image-size="80" />
        <div v-for="item in lostFound" :key="item.id" class="service-card">
          <div class="notice-head">
            <el-tag size="small" :type="item.kind === 'lost' ? 'warning' : 'success'">{{ item.kind_label }}</el-tag>
            <el-tag v-if="item.status !== 'published'" size="small" :type="lfStatusType(item.status)">
              {{ lfStatusLabel(item.status) }}
            </el-tag>
            <span class="notice-title">{{ item.title }}</span>
            <span class="notice-time">{{ item.created_at }}</span>
          </div>
          <div class="notice-content">{{ item.description }}</div>
          <div v-if="item.images?.length" class="lf-images">
            <SecureImage v-for="img in item.images" :key="img" :url="img" :size="72" />
          </div>
          <div class="notice-foot">
            <span>地点：{{ item.location || '未填写' }}</span>
            <span>· 联系：{{ item.contact }}</span>
            <span v-if="item.review_remark" style="color: #b45309">· 审核意见：{{ item.review_remark }}</span>
            <span v-if="lfScope === 'pending' || (auth.isAdmin && item.status === 'published')" class="notice-admin">
              <template v-if="item.status === 'pending_review'">
                <el-button link type="success" size="small" @click="reviewLf(item, 'approve')">通过</el-button>
                <el-button link type="danger" size="small" @click="reviewLf(item, 'reject')">驳回</el-button>
              </template>
              <el-button v-if="item.status === 'published'" link type="danger" size="small" @click="reviewLf(item, 'remove')">
                下架
              </el-button>
            </span>
            <span v-else-if="lfScope === 'mine' && item.status === 'published'" class="notice-admin">
              <el-button link type="success" size="small" @click="resolveLf(item)">已找回/已归还</el-button>
            </span>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 报修申请弹窗 -->
    <el-dialog v-model="repairDialog" title="提交报修申请" width="560px" :close-on-click-modal="false">
      <el-form :model="repairForm" label-width="90px">
        <el-form-item label="标题" required>
          <el-input v-model="repairForm.title" maxlength="200" placeholder="如：卫生间水龙头漏水" />
        </el-form-item>
        <el-form-item label="分类">
          <el-select v-model="repairForm.category" style="width: 160px">
            <el-option label="水电" value="water_power" />
            <el-option label="网络" value="network" />
            <el-option label="家具" value="furniture" />
            <el-option label="门窗" value="door_window" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-form-item>
        <el-form-item label="位置" required>
          <el-input v-model="repairForm.building" placeholder="楼栋，如 12 栋" style="width: 130px" />
          <el-input v-model="repairForm.room" placeholder="房间号，如 305" style="width: 130px; margin-left: 8px" />
        </el-form-item>
        <el-form-item label="联系方式" required>
          <el-input v-model="repairForm.contact" placeholder="手机号 / QQ / 微信" />
        </el-form-item>
        <el-form-item label="故障描述" required>
          <el-input v-model="repairForm.description" type="textarea" :rows="3" placeholder="至少 5 个字，写清现象与影响" />
        </el-form-item>
      </el-form>
      <div class="muted" style="font-size: 12px">每天最多提交 3 条；同房间同类型 24 小时内已有申请会提示等待处理</div>
      <template #footer>
        <el-button @click="repairDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitRepair">提交申请</el-button>
      </template>
    </el-dialog>

    <!-- 关闭报修弹窗（管理员）：常见原因下拉，可选可输入 -->
    <el-dialog v-model="closeDialog" title="关闭报修申请" width="500px" :close-on-click-modal="false">
      <el-form label-width="72px">
        <el-form-item label="工单">
          <span>{{ closeTarget?.title }}（{{ closeTarget?.building }} {{ closeTarget?.room }}）</span>
        </el-form-item>
        <el-form-item label="原因" required>
          <el-select
            v-model="closeRemark"
            filterable
            allow-create
            default-first-option
            placeholder="选择常见原因，或直接输入"
            style="width: 100%"
          >
            <el-option v-for="reason in CLOSE_PRESETS" :key="reason" :label="reason" :value="reason" />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="muted" style="font-size: 12px">关闭后学生可在“我的报修”看到该原因，且不能再被受理</div>
      <template #footer>
        <el-button @click="closeDialog = false">取消</el-button>
        <el-button type="danger" :loading="closeLoading" @click="confirmCloseRepair">确认关闭</el-button>
      </template>
    </el-dialog>
    <!-- 管理员代取消预约（必须填原因，学生端可见） -->
    <el-dialog v-model="cancelDialog" title="代学生取消预约" width="500px" :close-on-click-modal="false">
      <el-form label-width="72px">
        <el-form-item label="预约人">
          <span>{{ cancelTarget?.username || '#' + (cancelTarget?.user_id ?? '') }}</span>
        </el-form-item>
        <el-form-item label="预约">
          <span>{{ cancelTarget?.location_name }} · {{ cancelTarget?.res_date }} {{ cancelTarget?.slot_label }}</span>
        </el-form-item>
        <el-form-item label="原因" required>
          <el-select
            v-model="cancelReason"
            filterable
            allow-create
            default-first-option
            placeholder="选择常见原因，或直接输入"
            style="width: 100%"
          >
            <el-option v-for="reason in CANCEL_PRESETS" :key="reason" :label="reason" :value="reason" />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="muted" style="font-size: 12px">
        取消后该时段立即释放，学生能在“我的预约”看到这条原因
      </div>
      <template #footer>
        <el-button @click="cancelDialog = false">返回</el-button>
        <el-button type="danger" :loading="cancelLoading" @click="confirmCancelForUser">确认取消</el-button>
      </template>
    </el-dialog>

    <!-- 失物招领发布弹窗 -->
    <el-dialog v-model="lfDialog" :title="lfForm.kind === 'lost' ? '发布失物（我丢了东西）' : '发布招领（我捡到东西）'" width="560px" :close-on-click-modal="false">
      <el-form :model="lfForm" label-width="90px">
        <el-form-item label="类型">
          <el-radio-group v-model="lfForm.kind">
            <el-radio label="lost">我丢了东西</el-radio>
            <el-radio label="found">我捡到东西</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="标题" required>
          <el-input v-model="lfForm.title" maxlength="200" placeholder="如：捡到一张校园卡" />
        </el-form-item>
        <el-form-item label="地点">
          <el-input v-model="lfForm.location" maxlength="200" placeholder="丢失 / 拾获地点" />
        </el-form-item>
        <el-form-item label="联系方式" required>
          <el-input v-model="lfForm.contact" maxlength="100" placeholder="QQ / 微信 / 手机号" />
        </el-form-item>
        <el-form-item label="描述" required>
          <el-input v-model="lfForm.description" type="textarea" :rows="3" placeholder="至少 10 个字，物品特征、时间等（隐私信息先打码）" />
        </el-form-item>
        <el-form-item label="图片">
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            accept=".jpg,.jpeg,.png,.webp"
            :on-change="onImagePicked"
          >
            <el-button size="small">添加图片（jpg/png/webp，≤5MB，最多 3 张）</el-button>
          </el-upload>
          <div v-if="lfForm.image_urls.length" class="lf-images" style="margin-top: 8px">
            <div v-for="(u, i) in lfForm.image_urls" :key="u" class="lf-thumb-wrap">
              <SecureImage :url="u" :size="72" />
              <button type="button" class="lf-thumb-remove" title="移除这张图片" @click="removeLfImage(i)">
                ×
              </button>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <div class="muted" style="font-size: 12px">提交后进入待审核，管理员通过后才会在公示栏展示</div>
      <template #footer>
        <el-button @click="lfDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitLf">提交审核</el-button>
      </template>
    </el-dialog>

    <!-- 失物招领驳回：常规原因下拉可点选，也允许直接输入 -->
    <el-dialog v-model="lfRejectDialog" title="驳回失物招领" width="500px" :close-on-click-modal="false">
      <el-form label-width="72px">
        <el-form-item label="条目">
          <span>{{ lfRejectTarget?.title }}</span>
        </el-form-item>
        <el-form-item label="原因" required>
          <el-select
            v-model="lfRejectReason"
            filterable
            allow-create
            default-first-option
            placeholder="选择常见原因，或直接输入"
            style="width: 100%"
          >
            <el-option v-for="item in LF_REJECT_PRESETS" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="muted" style="font-size: 12px">
        驳回原因会显示在发布者的条目标签下，请写明需要补充或修改的内容。
      </div>
      <template #footer>
        <el-button @click="lfRejectDialog = false">取消</el-button>
        <el-button type="danger" :loading="lfRejectLoading" @click="confirmRejectLf">确认驳回</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile } from 'element-plus'
import {
  createLostFound,
  createRepair,
  createReservation,
  fetchAvailability,
  fetchLocations,
  fetchLostFound,
  fetchRepairs,
  fetchReservations,
  resolveLostFound,
  reviewLostFound,
  transitionRepair,
  transitionReservation,
  uploadLostFoundImage,
} from '../api/client'
import { useAuthStore } from '../stores/auth'
import SecureImage from '../components/SecureImage.vue'
import type { AvailabilityInfo, LocationItem, LostFoundItem, RepairItem, ReservationItem } from '../types'

const auth = useAuthStore()
const tab = ref('repairs')
const saving = ref(false)

// ---------------- 报修 ----------------
const repairs = ref<RepairItem[]>([])
const repairAll = ref(false)
const repairDialog = ref(false)
const repairForm = ref({ title: '', category: 'other', building: '', room: '', contact: '', description: '' })

/** 管理员关闭报修的快捷原因（可点选，也可直接输入自定义） */
const closeDialog = ref(false)
const closeTarget = ref<RepairItem | null>(null)
const closeRemark = ref('')
const closeLoading = ref(false)
const CLOSE_PRESETS = [
  '设备已恢复正常，无需维修',
  '信息不实或重复提交',
  '学生已自行处理',
  '非本部门职责范围，已转交',
  '多次联系不上报修人',
]

const REPAIR_STATUS: Record<string, { label: string; type: 'warning' | 'primary' | 'success' | 'info' }> = {
  pending: { label: '待受理', type: 'warning' },
  processing: { label: '处理中', type: 'primary' },
  completed: { label: '已完成', type: 'success' },
  closed: { label: '已关闭', type: 'info' },
}
const repairStatusLabel = (s: string) => REPAIR_STATUS[s]?.label ?? s
const repairStatusType = (s: string) => REPAIR_STATUS[s]?.type ?? 'info'

async function loadRepairs() {
  try {
    repairs.value = await fetchRepairs()
  } catch (error: any) {
    ElMessage.error(`加载报修失败：${error?.message || error}`)
  }
}

async function submitRepair() {
  const f = repairForm.value
  if (!f.title.trim() || !f.building.trim() || !f.room.trim() || !f.contact.trim()) {
    ElMessage.warning('标题、楼栋房间、联系方式不能为空')
    return
  }
  if (f.description.trim().length < 5) {
    ElMessage.warning('故障描述至少 5 个字')
    return
  }
  saving.value = true
  try {
    await createRepair({ ...f, title: f.title.trim(), description: f.description.trim() })
    ElMessage.success('已提交，等待后勤受理')
    repairDialog.value = false
    repairForm.value = { title: '', category: 'other', building: '', room: '', contact: '', description: '' }
    await loadRepairs()
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  } finally {
    saving.value = false
  }
}

async function actRepair(item: RepairItem, action: 'process' | 'complete' | 'close' | 'cancel') {
  let remark = ''
  if (action === 'close') {
    // 关闭走专用弹窗：常见原因下拉可点选，也允许直接输入
    closeTarget.value = item
    closeRemark.value = ''
    closeDialog.value = true
    return
  }
  if (action === 'process') {
    try {
      const { value } = await ElMessageBox.prompt('受理备注（可选，如已派维修师傅）', `受理「${item.title}」`, {
        inputPlaceholder: '选填',
      }).catch(() => ({ value: '' }))
      remark = value || ''
    } catch {
      return
    }
  }
  try {
    await transitionRepair(item.id, action, remark)
    ElMessage.success('已更新')
    await loadRepairs()
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  }
}

/** 关闭弹窗确认：原因必填，提交后写入 handler_remark */
async function confirmCloseRepair() {
  const target = closeTarget.value
  const remark = closeRemark.value.trim()
  if (!target) return
  if (!remark) {
    ElMessage.warning('请选择或填写关闭原因')
    return
  }
  closeLoading.value = true
  try {
    await transitionRepair(target.id, 'close', remark)
    ElMessage.success('已关闭该报修申请')
    closeDialog.value = false
    await loadRepairs()
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  } finally {
    closeLoading.value = false
  }
}

// ---------------- 预约 ----------------
const locations = ref<LocationItem[]>([])
const selectedLocation = ref<LocationItem | null>(null)
const reserveDate = ref('')
const availability = ref<AvailabilityInfo>({ location_id: 0, date: '', slots: [], range_note: '' })
/** 管理员代取消预约：原因必填，学生端可见 */
const cancelDialog = ref(false)
const cancelTarget = ref<ReservationItem | null>(null)
const cancelReason = ref('')
const cancelLoading = ref(false)
const CANCEL_PRESETS = [
  '时段调整，请重新预约',
  '场地临时维护关闭',
  '重复预约，保留其中一条',
  '长时间未到馆，座位已释放',
]

const reservations = ref<ReservationItem[]>([])
/** 默认收起过期记录，避免列表越滚越长；有过期时才显示开关 */
const showExpired = ref(false)
const expiredCount = computed(() => reservations.value.filter((r) => r.is_expired).length)

/** 管理员筛选：按预约人 / 状态 / 关键词，三者可组合 */
const resFilterUser = ref('')
const resFilterStatus = ref('')
const resKeyword = ref('')

/** 预约人分类：出现过的用户 + 各自条数（下拉里展示） */
const resUsers = computed(() => {
  const map = new Map<string, number>()
  for (const r of reservations.value) {
    const name = r.username || `#${r.user_id}`
    map.set(name, (map.get(name) ?? 0) + 1)
  }
  return [...map.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => a.name.localeCompare(b.name))
})

const visibleReservations = computed(() => {
  // 明确筛“已过期”时忽略折叠开关，否则会筛出空列表
  const includeExpired = showExpired.value || resFilterStatus.value === 'expired'
  let rows = includeExpired ? reservations.value : reservations.value.filter((r) => !r.is_expired)
  if (!auth.isAdmin) return rows

  const kw = resKeyword.value.trim().toLowerCase()
  return rows.filter((r) => {
    if (resFilterUser.value && (r.username || `#${r.user_id}`) !== resFilterUser.value) return false
    if (resFilterStatus.value) {
      const state = r.is_expired ? 'expired' : r.status
      if (state !== resFilterStatus.value) return false
    }
    if (kw) {
      const hay = `${r.username || ''} ${r.location_name} ${r.res_date} ${r.slot_label}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    return true
  })
})

const tomorrow = new Date(Date.now() + 86400_000).toISOString().slice(0, 10)
const tomorrowTs = (() => {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return d.getTime() + 86400_000
})()
const maxTs = tomorrowTs + 6 * 86400_000

/** 只能选明天起 7 天内（与后端 reservation_days_ahead 一致） */
function isDateDisabled(d: Date) {
  return d.getTime() < tomorrowTs || d.getTime() > maxTs
}

const RES_STATUS: Record<string, { label: string; type: 'primary' | 'success' | 'info' | 'danger' | 'warning' }> = {
  reserved: { label: '已预约', type: 'primary' },
  fulfilled: { label: '已使用', type: 'success' },
  cancelled: { label: '已取消', type: 'info' },
  no_show: { label: '违约', type: 'danger' },
}

/** 过期未履约优先于 reserved 展示（日期已过但状态没流转） */
const resStatusLabel = (row: ReservationItem) =>
  row.is_expired ? '已过期' : (RES_STATUS[row.status]?.label ?? row.status)
const resStatusType = (row: ReservationItem) =>
  row.is_expired ? 'warning' : (RES_STATUS[row.status]?.type ?? 'info')

/** 时段短标签：一列内快速扫读，具体时间跟在后面 */
const SLOT_SHORT: Record<string, string> = { morning: '上午', afternoon: '中午', evening: '晚上' }
const SLOT_TIME: Record<string, string> = {
  morning: '08:00-12:00',
  afternoon: '12:00-17:00',
  evening: '17:00-22:00',
}
const slotShort = (slot: string) => SLOT_SHORT[slot] ?? slot
const slotTime = (slot: string) => SLOT_TIME[slot] ?? ''
const slotTagType = (slot: string) =>
  slot === 'morning' ? 'success' : slot === 'afternoon' ? 'warning' : 'info'

async function loadLocations() {
  try {
    locations.value = await fetchLocations()
    if (!selectedLocation.value && locations.value.length) await selectLocation(locations.value[0])
  } catch (error: any) {
    ElMessage.error(`加载位置失败：${error?.message || error}`)
  }
}

async function selectLocation(loc: LocationItem) {
  selectedLocation.value = loc
  if (!reserveDate.value) reserveDate.value = tomorrow
  await loadAvailability()
}

async function loadAvailability() {
  if (!selectedLocation.value || !reserveDate.value) return
  try {
    availability.value = await fetchAvailability(selectedLocation.value.id, reserveDate.value)
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  }
}

async function book(loc: LocationItem, slot: string) {
  const slotInfo = availability.value.slots.find((s) => s.slot === slot)
  const slotLabel = slotInfo?.label || slot
  try {
    await ElMessageBox.confirm(
      `位置：${loc.name}\n日期：${reserveDate.value}\n时段：${slotLabel}（余 ${slotInfo?.remaining ?? '-'} 个）`,
      '确认预约',
      {
        type: 'info',
        confirmButtonText: '确认预约',
        cancelButtonText: '再想想',
        // 保留换行，避免三行信息挤成一句
        customClass: 'reserve-confirm-box',
      },
    )
  } catch {
    return
  }
  try {
    await createReservation({ location_id: loc.id, res_date: reserveDate.value, slot })
    ElMessage.success(`已预约 ${loc.name} ${reserveDate.value} ${slotLabel}`)
    await Promise.all([loadAvailability(), loadReservations()])
  } catch (error: any) {
    // 409 冲突后端返回 {message, conflict_reservation_id}
    const detail = error?.message || error
    ElMessage.error(typeof detail === 'string' ? detail : '预约失败，请稍后重试')
    await loadAvailability()
  }
}

async function loadReservations() {
  try {
    reservations.value = await fetchReservations()
  } catch (error: any) {
    ElMessage.error(`加载预约失败：${error?.message || error}`)
  }
}

async function cancelReservation(row: ReservationItem) {
  // 管理员代取消：必须填原因，学生会在“我的预约”看到这句话（相当于单人通知）
  if (auth.isAdmin) {
    cancelTarget.value = row
    cancelReason.value = ''
    cancelDialog.value = true
    return
  }
  try {
    await ElMessageBox.confirm(`取消 ${row.res_date} ${row.slot_label} 的预约？`, '取消预约', { type: 'warning' })
  } catch {
    return
  }
  try {
    await transitionReservation(row.id, 'cancel')
    ElMessage.success('已取消')
    await loadReservations()
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  }
}

/** 管理员代取消：提交原因 */
async function confirmCancelForUser() {
  const target = cancelTarget.value
  const reason = cancelReason.value.trim()
  if (!target) return
  if (!reason) {
    ElMessage.warning('请填写取消原因，学生会在“我的预约”看到')
    return
  }
  cancelLoading.value = true
  try {
    await transitionReservation(target.id, 'cancel', reason)
    ElMessage.success(`已取消 ${target.username || '#' + target.user_id} 的预约`)
    cancelDialog.value = false
    await Promise.all([loadReservations(), loadAvailability()])
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  } finally {
    cancelLoading.value = false
  }
}

// ---------------- 失物招领 ----------------
const lostFound = ref<LostFoundItem[]>([])
const lfScope = ref('public')
const lfDialog = ref(false)
const pendingCount = ref(0)
const lfForm = ref({ kind: 'found', title: '', location: '', contact: '', description: '', image_urls: [] as string[] })

/** 管理员驳回：常见原因可点选，也允许自由输入（写 review_remark） */
const lfRejectDialog = ref(false)
const lfRejectTarget = ref<LostFoundItem | null>(null)
const lfRejectReason = ref('')
const lfRejectLoading = ref(false)
const LF_REJECT_PRESETS = [
  '信息不完整，请补充物品特征与丢失/拾获地点',
  '描述过于笼统，无法核实，请补充细节',
  '联系方式无效，无法联系失主或拾获人',
  '疑似重复发布，请合并为一条',
  '含身份证号、银行卡号等隐私信息，请打码后重提',
  '不属于失物招领范围，请通过其他渠道处理',
]

const lfTabLabel = computed(() =>
  auth.isAdmin && pendingCount.value > 0
    ? `失物招领（${pendingCount.value} 条待审）`
    : '失物招领',
)

const LF_STATUS: Record<string, { label: string; type: 'warning' | 'success' | 'danger' | 'info' }> = {
  pending_review: { label: '待审核', type: 'warning' },
  rejected: { label: '已驳回', type: 'danger' },
  resolved: { label: '已解决', type: 'success' },
  removed: { label: '已下架', type: 'info' },
}
const lfStatusLabel = (s: string) => LF_STATUS[s]?.label ?? s
const lfStatusType = (s: string) => LF_STATUS[s]?.type ?? 'info'

/** 超过 48 小时仍未审核的条目（审批超时提示，与后端阈值一致） */
const timedOutItems = computed(() =>
  lostFound.value.filter(
    (i) => i.status === 'pending_review' && i.created_at && Date.now() - new Date(i.created_at.replace(' ', 'T')).getTime() > 48 * 3600_000,
  ),
)

async function loadLostFound() {
  try {
    lostFound.value = await fetchLostFound(lfScope.value)
    if (auth.isAdmin && lfScope.value !== 'pending') {
      const pending = await fetchLostFound('pending')
      pendingCount.value = pending.length
    }
  } catch (error: any) {
    ElMessage.error(`加载失物招领失败：${error?.message || error}`)
  }
}

function openLfDialog() {
  lfForm.value = { kind: 'found', title: '', location: '', contact: '', description: '', image_urls: [] }
  lfDialog.value = true
}

async function onImagePicked(file: UploadFile) {
  const raw = file.raw
  if (!raw) return
  if (raw.size > 5 * 1024 * 1024) {
    ElMessage.warning('图片超过 5MB')
    return
  }
  if (lfForm.value.image_urls.length >= 3) {
    ElMessage.warning('最多 3 张图片')
    return
  }
  try {
    const uploaded = await uploadLostFoundImage(raw)
    lfForm.value.image_urls.push(uploaded.url)
  } catch (error: any) {
    ElMessage.error(`上传失败：${error?.message || error}`)
  }
}

/** 单独移除某张已上传图片（只从表单移除，不影响已提交的条目） */
function removeLfImage(index: number) {
  lfForm.value.image_urls.splice(index, 1)
}

async function submitLf() {
  const f = lfForm.value
  if (f.title.trim().length < 3 || f.description.trim().length < 10 || !f.contact.trim()) {
    ElMessage.warning('标题至少 3 字、描述至少 10 字、联系方式必填')
    return
  }
  saving.value = true
  try {
    await createLostFound({
      kind: f.kind,
      title: f.title.trim(),
      description: f.description.trim(),
      location: f.location.trim(),
      contact: f.contact.trim(),
      image_urls: f.image_urls,
    })
    ElMessage.success('已提交，等待管理员审核')
    lfDialog.value = false
    lfScope.value = 'mine'
    await loadLostFound()
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  } finally {
    saving.value = false
  }
}

async function reviewLf(item: LostFoundItem, action: 'approve' | 'reject' | 'remove') {
  let remark = ''
  if (action === 'reject') {
    // 驳回走专用弹窗：常见原因下拉可点选，也允许直接输入
    lfRejectTarget.value = item
    lfRejectReason.value = ''
    lfRejectDialog.value = true
    return
  }
  if (action === 'remove') {
    try {
      await ElMessageBox.confirm(`下架「${item.title}」？`, '下架', { type: 'warning' })
    } catch {
      return
    }
  }
  try {
    await reviewLostFound(item.id, action, remark)
    ElMessage.success('已处理')
    await loadLostFound()
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  }
}

/** 驳回弹窗确认：原因必填，提交后写入 review_remark，学生端可见 */
async function confirmRejectLf() {
  const target = lfRejectTarget.value
  const remark = lfRejectReason.value.trim()
  if (!target) return
  if (!remark) {
    ElMessage.warning('请选择或填写驳回原因')
    return
  }
  lfRejectLoading.value = true
  try {
    await reviewLostFound(target.id, 'reject', remark)
    ElMessage.success('已驳回该条失物招领')
    lfRejectDialog.value = false
    await loadLostFound()
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  } finally {
    lfRejectLoading.value = false
  }
}

async function resolveLf(item: LostFoundItem) {
  try {
    await resolveLostFound(item.id)
    ElMessage.success('已标记解决')
    await loadLostFound()
  } catch (error: any) {
    ElMessage.error(`${error?.message || error}`)
  }
}

// ---------------- 装载 ----------------
function onTabChange(name: string | number) {
  if (name === 'repairs') loadRepairs()
  else if (name === 'reservations') {
    loadLocations()
    loadReservations()
  } else if (name === 'lostfound') loadLostFound()
}

onMounted(() => {
  loadRepairs()
})
</script>

<style scoped>
.service-toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.service-card {
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--panel);
  padding: 12px 16px;
  margin-bottom: 10px;
}

.panel-subtitle {
  font-weight: 600;
  margin: 18px 0 8px;
}

.reserve-layout {
  display: flex;
  gap: 16px;
  margin-bottom: 6px;
}

.reserve-left {
  flex: 1;
  min-width: 0;
}

.reserve-right {
  flex: 1.2;
  min-width: 0;
}

.location-card {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 14px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.location-card:hover {
  border-color: var(--brand);
}

.location-card.active {
  border-color: var(--brand);
  background: rgba(138, 28, 28, 0.06);
}

.slot-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin: 12px 0;
}

/* 时段按钮上的余量角标：可约=绿，满员=灰 */
.slot-left {
  margin-left: 8px;
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 12px;
  background: rgba(138, 28, 28, 0.1);
  color: var(--brand);
}

.slot-left.full {
  background: rgba(144, 147, 153, 0.16);
  color: #909399;
}

.lf-images {
  display: flex;
  gap: 10px;
  margin-top: 10px;
  /* 独占一行，避免和“添加图片”按钮挤在一起 */
  flex-basis: 100%;
  padding-left: 4px;
}

.reserve-filter {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.history-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 12px;
  font-weight: 400;
  font-size: 12px;
  color: var(--muted, #909399);
}

/* 时段单元格：标签与时间不换行 */
.slot-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

/* 缩略图右上角单独删除按钮 */
.lf-thumb-wrap {
  position: relative;
  display: inline-block;
}

.lf-thumb-remove {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 20px;
  height: 20px;
  line-height: 18px;
  border-radius: 50%;
  border: none;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  padding: 0;
}

.lf-thumb-remove:hover {
  background: var(--el-color-danger);
}

.lf-thumb {
  width: 72px;
  height: 72px;
  border-radius: 8px;
  border: 1px solid var(--border);
}

@media (max-width: 768px) {
  .reserve-layout {
    flex-direction: column;
  }
}
</style>
