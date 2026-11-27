<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NEmpty, NInput, NModal, NSelect, NTag, useMessage } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { useAuthStore } from '@/stores/auth'
import { useTabsStore } from '@/stores/tabs'
import { getRdProjects } from '@/api/rd'
import { getAttachmentCountsByEntries } from '@/api/voucherBook'
import {
  VOUCHER_ATTACHMENT_ACCEPT,
  downloadAttachmentBlob,
  fetchAttachmentPreviewBlob,
  isImageAttachment,
  listVoucherAttachments,
  uploadVoucherAttachments,
  voidAttachment,
  voucherAttachmentTypeLabel,
  type VoucherAttachmentItem,
} from '@/api/attachments'
import { saveBlobFile } from '@/utils/download'
import { assignEntryAuxiliary, getAuxiliaryOptions } from '@/api/auxiliary'
import type { AuxiliaryItem } from '@/api/auxiliary'
import VoucherSheet from '@/components/business/VoucherSheet.vue'
import { SoloErrorState } from '@/components/ui'
import { SoloSkeleton } from '@/components/ui'
import { errorSummary } from '@/utils/errorPresenter'
import { useTabGuard } from '@/composables/useTabGuard'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()
const authStore = useAuthStore()
const tabsStore = useTabsStore()

const entry = ref<(typeof entryStore.activeEntries)[0] | null>(null)
const loading = ref(true)
/** 加载失败原因。失败时页面要给出可重试的错误态，不能停在空白页 */
const loadError = ref('')
const rdProjectLabels = ref<Record<string, string>>({})
/** 附单据张数（真实附件聚合，与凭证册/打印页同源） */
const attachmentCount = ref<number>(0)

// ── 原始凭证 / 附件管理 ──
const voucherAttachments = ref<VoucherAttachmentItem[]>([])
const attachmentsLoading = ref(false)
const uploadingAttachments = ref(false)
const attachmentInputRef = ref<HTMLInputElement | null>(null)
const voidingAttachmentId = ref('')
const voidAttachmentReason = ref('')
const previewModalShow = ref(false)
const previewModalTitle = ref('')
const previewModalUrl = ref('')

function revokeAttachmentPreview() {
  if (previewModalUrl.value && typeof URL.revokeObjectURL === 'function') {
    URL.revokeObjectURL(previewModalUrl.value)
  }
  previewModalUrl.value = ''
}

async function loadEntryAttachments(entryId: string) {
  attachmentsLoading.value = true
  try {
    const res = await listVoucherAttachments(entryId, true)
    voucherAttachments.value = res.items || []
    if (typeof res.activeCount === 'number') {
      attachmentCount.value = res.activeCount
    }
  } catch {
    voucherAttachments.value = []
  } finally {
    attachmentsLoading.value = false
  }
}

function triggerAttachmentPicker() {
  attachmentInputRef.value?.click()
}

async function handleAttachmentFileChange(event: Event) {
  const input = event.target as HTMLInputElement | null
  const files = input?.files ? Array.from(input.files) : []
  if (input) input.value = ''
  if (!files.length || !entry.value) return
  uploadingAttachments.value = true
  try {
    const res = await uploadVoucherAttachments(entry.value.id, 'other', files)
    const stored = res.storedCount ?? (res.items || []).filter((item) => item.outcome === 'stored').length
    const dup = res.duplicateCount ?? (res.items || []).filter((item) => item.outcome === 'duplicate').length
    const rejected = (res.items || []).find((item) => item.outcome === 'rejected')
    if (stored > 0) message.success(`已归档 ${stored} 份原始凭证${dup > 0 ? `，跳过重复 ${dup} 份` : ''}`)
    else if (dup > 0) message.info(`跳过 ${dup} 份已归档的重复文件`)
    if (rejected?.error) message.warning(`「${rejected.fileName}」${rejected.error}`)
    await loadEntryAttachments(entry.value.id)
    try {
      const { counts } = await getAttachmentCountsByEntries([entry.value.id])
      attachmentCount.value = counts?.[entry.value.id] ?? voucherAttachments.value.filter((i) => i.status === 'active').length
    } catch {
      // ignore count refresh failure
    }
  } catch (err) {
    message.error(errorSummary(err, '附件上传失败'))
  } finally {
    uploadingAttachments.value = false
  }
}

async function handlePreviewAttachment(item: VoucherAttachmentItem) {
  revokeAttachmentPreview()
  try {
    const blob = await fetchAttachmentPreviewBlob(item.id)
    previewModalUrl.value = URL.createObjectURL(blob)
    previewModalTitle.value = item.fileName
    previewModalShow.value = true
  } catch (err) {
    message.error(errorSummary(err, '预览失败'))
  }
}

async function handleDownloadAttachment(item: VoucherAttachmentItem) {
  try {
    const blob = await downloadAttachmentBlob(item.id)
    saveBlobFile(blob, item.fileName)
  } catch (err) {
    message.error(errorSummary(err, '下载失败'))
  }
}

function startVoidAttachment(item: VoucherAttachmentItem) {
  if (voidingAttachmentId.value === item.id) {
    voidingAttachmentId.value = ''
    voidAttachmentReason.value = ''
    return
  }
  voidingAttachmentId.value = item.id
  voidAttachmentReason.value = ''
}

async function confirmVoidAttachment(item: VoucherAttachmentItem) {
  if (!entry.value) return
  const reason = voidAttachmentReason.value.trim() || '凭证详情页作废'
  try {
    await voidAttachment(item.id, reason)
    message.success('已作废（原件文件保留，审计已留痕）')
    voidingAttachmentId.value = ''
    voidAttachmentReason.value = ''
    await loadEntryAttachments(entry.value.id)
    try {
      const { counts } = await getAttachmentCountsByEntries([entry.value.id])
      attachmentCount.value = counts?.[entry.value.id] ?? voucherAttachments.value.filter((i) => i.status === 'active').length
    } catch {
      // ignore count refresh failure
    }
  } catch (err) {
    message.error(errorSummary(err, '作废失败'))
  }
}

const companyName = computed(
  () => authStore.user?.companies.find((company) => company.company_id === authStore.currentCompanyId)?.name || '',
)

// ── 辅助核算（仅草稿可改：已过账凭证不可变） ──
const isDraft = computed(() => ['draft', 'pending_review'].includes(String(entry.value?.voucherStatus || '')))
const partners = ref<AuxiliaryItem[]>([])
const departments = ref<AuxiliaryItem[]>([])
const projects = ref<AuxiliaryItem[]>([])
const employees = ref<AuxiliaryItem[]>([])
const selectedPartner = ref<string | null>(null)
const selectedDepartment = ref<string | null>(null)
const selectedProject = ref<string | null>(null)
const selectedEmployee = ref<string | null>(null)
const savingAuxiliary = ref(false)
/** 已落库的辅助核算快照：和当前选择不一致 = 有未保存改动，关标签要先拦一下 */
const savedAuxiliary = ref<{
  partner: string | null
  department: string | null
  project: string | null
  employee: string | null
}>({
  partner: null,
  department: null,
  project: null,
  employee: null,
})

function snapshotAuxiliary() {
  savedAuxiliary.value = {
    partner: selectedPartner.value,
    department: selectedDepartment.value,
    project: selectedProject.value,
    employee: selectedEmployee.value,
  }
}

function auxiliaryDirty(): boolean {
  return (
    selectedPartner.value !== savedAuxiliary.value.partner ||
    selectedDepartment.value !== savedAuxiliary.value.department ||
    selectedProject.value !== savedAuxiliary.value.project ||
    selectedEmployee.value !== savedAuxiliary.value.employee
  )
}

const partnerOptions = computed(() => partners.value.map((item) => ({ label: `${item.code} ${item.name}`, value: item.id })))
const departmentOptions = computed(() => departments.value.map((item) => ({ label: item.name, value: item.id })))
const projectOptions = computed(() => projects.value.map((item) => ({ label: `${item.code} ${item.name}`, value: item.id })))
const employeeOptions = computed(() =>
  employees.value.map((item) => ({
    label: item.department ? `${item.name} · ${item.department}` : item.name,
    value: item.id,
  })),
)

async function loadAuxiliaryOptions() {
  if (!isDraft.value) return
  try {
    const data = await getAuxiliaryOptions('', 100)
    partners.value = data.partners
    departments.value = data.departments
    projects.value = data.projects
    employees.value = data.employees || []
    // 回显：取分录里第一个挂了辅助的行
    const lines = (entry.value as any)?.voucherLines || {}
    const allLines = [...(lines.debit || []), ...(lines.credit || [])]
    const first = allLines.find(
      (line: any) =>
        line?.auxiliary?.partner_id ||
        line?.auxiliary?.department_id ||
        line?.auxiliary?.project_id ||
        line?.auxiliary?.employee_id,
    )
    if (first) {
      selectedPartner.value = first.auxiliary.partner_id || null
      selectedDepartment.value = first.auxiliary.department_id || null
      selectedProject.value = first.auxiliary.project_id || null
      selectedEmployee.value = first.auxiliary.employee_id || null
    }
    snapshotAuxiliary()
  } catch {
    // 选项加载失败不阻塞凭证查看
  }
}

async function saveAuxiliary() {
  if (!entry.value) return
  if (!selectedPartner.value && !selectedDepartment.value && !selectedProject.value && !selectedEmployee.value && !auxiliaryDirty()) {
    message.warning('请至少选择一个辅助核算项')
    return
  }
  savingAuxiliary.value = true
  try {
    const result = await assignEntryAuxiliary(entry.value.id, {
      partner_id: selectedPartner.value,
      department_id: selectedDepartment.value,
      project_id: selectedProject.value,
      employee_id: selectedEmployee.value,
    })
    message.success(result.updatedLines > 0 ? `已更新 ${result.updatedLines} 条分录` : '辅助核算无变更')
    snapshotAuxiliary()
    await entryStore.fetchEntries()
    entry.value = entryStore.activeEntries.find((item) => item.id === entry.value?.id) || entry.value
  } catch (error: unknown) {
    const detail = (error as { response?: { data?: { detail?: { message?: string } | string } } })?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : detail?.message || '保存失败')
  } finally {
    savingAuxiliary.value = false
  }
}

// 关掉「记账凭证」标签等于离开这张凭证。辅助核算改一半没保存，要先问一句。
useTabGuard(auxiliaryDirty, {
  async save() {
    await saveAuxiliary()
    // saveAuxiliary 失败是自己吞掉并弹提示的，这里靠快照判断到底存没存进去
    if (auxiliaryDirty()) throw new Error('辅助核算未保存成功')
  },
})

function handlePrint() {
  window.print()
}

// 返回凭证列表：优先退回进入详情前的列表地址（月份/状态/搜索/滚动随历史一起还原）；
// 深链或新开窗口没有可退历史时，退到页签记住的列表地址（tabs.fullPath 只在列表页停留时更新，不会被详情页覆盖）。
function backToList() {
  const backPath = String((window.history.state as { back?: string } | null)?.back || '')
  if (backPath === '/vouchers' || backPath.startsWith('/vouchers?')) {
    router.back()
    return
  }
  router.push(tabsStore.tabs.find((tab) => tab.path === '/vouchers')?.fullPath || '/vouchers')
}

async function loadRdLabels() {
  try {
    const projects = await getRdProjects(true)
    rdProjectLabels.value = Object.fromEntries(projects.map((item) => [item.id, `${item.code} · ${item.name}`]))
  } catch {
    // 打印凭证仍可使用项目 ID
  }
}

// ── 上一张 / 下一张：全部已过账凭证按凭证号升序翻页 ──
// 凭证号 VOU-YYYYMM-NNNNNN 月份与序号都是零填充，字典序即业务顺序。
const siblingIds = computed(() => entryStore.activeEntries
  .filter((item) => item.voucherRef)
  .sort((a, b) => String(a.voucherRef).localeCompare(String(b.voucherRef)))
  .map((item) => item.id))
const currentIndex = computed(() => (entry.value ? siblingIds.value.indexOf(entry.value.id) : -1))
const prevId = computed(() => (currentIndex.value > 0 ? siblingIds.value[currentIndex.value - 1] : ''))
const nextId = computed(() => (currentIndex.value >= 0 && currentIndex.value < siblingIds.value.length - 1
  ? siblingIds.value[currentIndex.value + 1]
  : ''))
const positionLabel = computed(() => (currentIndex.value >= 0
  ? `第 ${currentIndex.value + 1} / ${siblingIds.value.length} 张`
  : ''))
function goToVoucher(id: string) {
  if (!id) return
  // replace 而非 push：翻页不堆历史，浏览器返回键仍直接回到凭证列表
  router.replace(`/vouchers/${id}`)
}

function onKeydown(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null
  if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
  if (event.key === 'ArrowLeft' && prevId.value) goToVoucher(prevId.value)
  if (event.key === 'ArrowRight' && nextId.value) goToVoucher(nextId.value)
}

async function resolveEntry(id: string) {
  loading.value = true
  loadError.value = ''
  let found = entryStore.activeEntries.find((item) => item.id === id)
  if (!found) {
    try {
      await entryStore.fetchEntries()
    } catch (err) {
      // 拉取失败：给出错误态而不是空白页，loading 必须落回 false，否则页面永远转圈
      loadError.value = errorSummary(err, '凭证加载失败')
      loading.value = false
      return
    }
    found = entryStore.activeEntries.find((item) => item.id === id)
  }
  if (!found) {
    message.error('未找到记录')
    backToList()
    return
  }
  entry.value = found
  loading.value = false
  voidingAttachmentId.value = ''
  voidAttachmentReason.value = ''
  revokeAttachmentPreview()
  previewModalShow.value = false
  await loadAuxiliaryOptions()
  await loadEntryAttachments(found.id)
  // 附单据数补齐失败不阻断详情页（只是不渲染「附单据 X 张」）
  try {
    const { counts } = await getAttachmentCountsByEntries([found.id])
    attachmentCount.value = counts?.[found.id] ?? attachmentCount.value
  } catch {
    attachmentCount.value = 0
  }
}

onMounted(async () => {
  await loadRdLabels()
  await resolveEntry(route.params.id as string)
  window.addEventListener('keydown', onKeydown)
})

watch(() => route.params.id, async (id) => {
  if (!id || typeof id !== 'string') return
  window.scrollTo({ top: 0 })
  await resolveEntry(id)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  revokeAttachmentPreview()
})
</script>

<template>
  <div v-if="loadError" class="voucher-detail-error">
    <SoloErrorState
      title="凭证加载失败"
      description="没能取到这张凭证的数据，可能是网络波动。"
      :detail="loadError"
      @retry="resolveEntry(route.params.id as string)"
    />
    <NButton text @click="backToList">← 返回凭证列表</NButton>
  </div>
  <SoloSkeleton v-else-if="loading" variant="card" :rows="5" class="voucher-detail-loading" />
  <div v-else-if="entry" class="voucher-detail-page">
    <div class="no-print toolbar">
      <NButton text @click="backToList">← 返回凭证列表</NButton>
      <div v-if="positionLabel" class="toolbar-nav">
        <NButton text size="small" :disabled="!prevId" title="方向键 ←" @click="goToVoucher(prevId)">
          <template #icon><i class="ri-arrow-left-line" /></template>
          上一张
        </NButton>
        <span class="nav-position">{{ positionLabel }}</span>
        <NButton text size="small" :disabled="!nextId" title="方向键 →" @click="goToVoucher(nextId)">
          下一张
          <template #icon><i class="ri-arrow-right-line" /></template>
        </NButton>
      </div>
      <div class="toolbar-right">
        <NButton v-if="isDraft" secondary @click="router.push('/vouchers/print?ids=' + entry.id)">打印预览</NButton>
        <NButton type="primary" @click="handlePrint">打印凭证</NButton>
      </div>
    </div>

    <NCard v-if="isDraft" :bordered="false" class="no-print auxiliary-card">
      <div class="auxiliary-head">
        <span class="auxiliary-title">辅助核算</span>
        <span class="auxiliary-hint">草稿凭证可补挂；已过账凭证请红冲后重做</span>
      </div>
      <div class="auxiliary-form">
        <NSelect v-model:value="selectedPartner" :options="partnerOptions" placeholder="往来单位" clearable filterable style="width: 210px;" />
        <NSelect v-model:value="selectedDepartment" :options="departmentOptions" placeholder="部门" clearable filterable style="width: 150px;" />
        <NSelect v-model:value="selectedProject" :options="projectOptions" placeholder="项目" clearable filterable style="width: 170px;" />
        <NSelect v-model:value="selectedEmployee" :options="employeeOptions" placeholder="职员" clearable filterable style="width: 160px;" />
        <NButton type="primary" :loading="savingAuxiliary" @click="saveAuxiliary">挂到凭证</NButton>
      </div>
    </NCard>

    <VoucherSheet
      :entry="entry as unknown as Record<string, any>"
      :company-name="companyName"
      :rd-project-labels="rdProjectLabels"
      :attachment-count="attachmentCount"
    />

    <NCard :bordered="false" class="no-print attachments-card">
      <div class="attachments-head">
        <div class="attachments-title-group">
          <span class="auxiliary-title">原始凭证 / 附件</span>
          <NTag size="small" :bordered="false" :type="attachmentCount > 0 ? 'info' : 'default'">
            {{ attachmentCount }} 份
          </NTag>
        </div>
        <div class="attachments-upload-bar">
          <input
            ref="attachmentInputRef"
            type="file"
            :accept="VOUCHER_ATTACHMENT_ACCEPT"
            multiple
            hidden
            @change="handleAttachmentFileChange"
          />
          <NButton size="small" type="primary" secondary :loading="uploadingAttachments" @click="triggerAttachmentPicker">
            上传附件
          </NButton>
        </div>
      </div>

      <div v-if="voucherAttachments.length > 0" class="voucher-attachment-list">
        <div
          v-for="item in voucherAttachments"
          :key="item.id"
          class="voucher-attachment-item"
          :class="{ void: item.status === 'void' }"
        >
          <div class="voucher-attachment-main">
            <div class="voucher-attachment-title">
              <NTag size="tiny" :bordered="false" type="info" style="flex-shrink: 0;">{{ voucherAttachmentTypeLabel(item.documentType) }}</NTag>
              <span class="voucher-attachment-name" :title="item.fileName">{{ item.fileName }}</span>
            </div>
            <div v-if="item.status === 'void'" class="voucher-attachment-void">
              已作废{{ item.voidReason ? `：${item.voidReason}` : '' }}
            </div>
            <div v-if="voidingAttachmentId === item.id" class="void-reason-input">
              <NInput v-model:value="voidAttachmentReason" size="tiny" placeholder="作废原因" />
            </div>
          </div>
          <div class="voucher-attachment-actions">
            <NButton
              v-if="isImageAttachment(item.fileExt || item.fileName)"
              size="tiny"
              quaternary
              type="primary"
              @click="handlePreviewAttachment(item)"
            >
              预览
            </NButton>
            <NButton size="tiny" quaternary @click="handleDownloadAttachment(item)">下载</NButton>
            <template v-if="item.status === 'active'">
              <NButton size="tiny" quaternary type="error" @click="startVoidAttachment(item)">
                {{ voidingAttachmentId === item.id ? '取消' : '作废' }}
              </NButton>
              <NButton
                v-if="voidingAttachmentId === item.id"
                size="tiny"
                type="error"
                @click="confirmVoidAttachment(item)"
              >
                确认作废
              </NButton>
            </template>
          </div>
        </div>
      </div>
      <NEmpty v-else-if="!attachmentsLoading" description="暂无附件" class="attachments-empty" />
    </NCard>

    <NModal
      v-model:show="previewModalShow"
      preset="card"
      title="原件预览"
      style="width: min(860px, 92vw);"
      @after-leave="revokeAttachmentPreview"
    >
      <div class="preview-modal-body">
        <img v-if="previewModalUrl" :src="previewModalUrl" :alt="previewModalTitle" class="preview-modal-img" />
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.voucher-detail-page { max-width: var(--page-width-narrow); margin: 0 auto; }
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.toolbar-right { display: flex; gap: 8px; }
.toolbar-nav { display: flex; align-items: center; gap: 6px; }
.nav-position {
  min-width: 76px;
  text-align: center;
  font-size: 12.5px;
  color: var(--solo-text-muted);
  font-variant-numeric: tabular-nums;
}
.auxiliary-card { margin-bottom: 16px; }
.auxiliary-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
.auxiliary-title { font-weight: 700; }
.auxiliary-hint { font-size: 12px; color: var(--solo-text-muted); }
.auxiliary-form { display: flex; flex-wrap: wrap; gap: 10px; }
.attachments-card { margin-top: 16px; }
.attachments-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}
.attachments-title-group { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.attachments-upload-bar { display: flex; align-items: center; gap: 8px; }
.voucher-attachment-list { display: flex; flex-direction: column; gap: 8px; }
.voucher-attachment-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--solo-border);
  border-radius: 8px;
  background: var(--solo-surface);
}
.voucher-attachment-item.void { opacity: 0.55; }
.voucher-attachment-main { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 4px; }
.voucher-attachment-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
.voucher-attachment-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.voucher-attachment-void { font-size: 12px; color: var(--solo-warning); }
.void-reason-input { margin-top: 4px; max-width: 280px; }
.voucher-attachment-actions { display: flex; align-items: center; gap: 4px; flex: 0 0 auto; }
.attachments-empty { padding: 16px 0; }
.preview-modal-body { display: flex; justify-content: center; align-items: center; max-height: 75vh; overflow: auto; }
.preview-modal-img { max-width: 100%; max-height: 72vh; object-fit: contain; display: block; }
@media print {
  .voucher-detail-page { max-width: 100%; margin: 0; padding: 0; }
}
</style>
