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
              可预约 {{ tomorrow }} 起未来 7 天的时段；每个时段剩余名额实时显示，约满后不可再约
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
                  :disabled="s.booked"
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

        <div class="panel-subtitle">我的预约</div>
        <el-table :data="reservations" size="small" stripe>
          <el-table-column prop="location_name" label="位置" min-width="140" />
          <el-table-column prop="res_date" label="日期" width="110" />
          <el-table-column prop="slot_label" label="时段" min-width="140" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="resStatusType(row.status)">{{ resStatusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="110">
            <template #default="{ row }">
              <el-button v-if="row.status === 'reserved'" link type="danger" size="small" @click="cancelReservation(row)">
                取消
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
            <SecureImage v-for="u in lfForm.image_urls" :key="u" :url="u" :size="72" />
          </div>
        </el-form-item>
      </el-form>
      <div class="muted" style="font-size: 12px">提交后进入待审核，管理员通过后才会在公示栏展示</div>
      <template #footer>
        <el-button @click="lfDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitLf">提交审核</el-button>
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
const reservations = ref<ReservationItem[]>([])

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

const RES_STATUS: Record<string, { label: string; type: 'primary' | 'success' | 'info' | 'danger' }> = {
  reserved: { label: '已预约', type: 'primary' },
  fulfilled: { label: '已使用', type: 'success' },
  cancelled: { label: '已取消', type: 'info' },
  no_show: { label: '违约', type: 'danger' },
}
const resStatusLabel = (s: string) => RES_STATUS[s]?.label ?? s
const resStatusType = (s: string) => RES_STATUS[s]?.type ?? 'info'

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

// ---------------- 失物招领 ----------------
const lostFound = ref<LostFoundItem[]>([])
const lfScope = ref('public')
const lfDialog = ref(false)
const pendingCount = ref(0)
const lfForm = ref({ kind: 'found', title: '', location: '', contact: '', description: '', image_urls: [] as string[] })

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
    try {
      const { value } = await ElMessageBox.prompt('驳回必须填写理由', `驳回「${item.title}」`, {
        inputPattern: /\S+/,
        inputErrorMessage: '理由不能为空',
      })
      remark = value
    } catch {
      return
    }
  } else if (action === 'remove') {
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
  background: rgba(47, 111, 237, 0.06);
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
  background: rgba(47, 111, 237, 0.12);
  color: var(--brand);
}

.slot-left.full {
  background: rgba(144, 147, 153, 0.16);
  color: #909399;
}

.lf-images {
  display: flex;
  gap: 8px;
  margin-top: 8px;
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
