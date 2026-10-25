<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NSelect, useMessage } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { useAuthStore } from '@/stores/auth'
import { useTabsStore } from '@/stores/tabs'
import { getRdProjects } from '@/api/rd'
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

const companyName = computed(
  () => authStore.user?.companies.find((company) => company.company_id === authStore.currentCompanyId)?.name || '',
)

// ── 辅助核算（仅草稿可改：已过账凭证不可变） ──
const isDraft = computed(() => ['draft', 'pending_review'].includes(String(entry.value?.voucherStatus || '')))
const partners = ref<AuxiliaryItem[]>([])
const departments = ref<AuxiliaryItem[]>([])
const projects = ref<AuxiliaryItem[]>([])
const selectedPartner = ref<string | null>(null)
const selectedDepartment = ref<string | null>(null)
const selectedProject = ref<string | null>(null)
const savingAuxiliary = ref(false)
/** 已落库的辅助核算快照：和当前选择不一致 = 有未保存改动，关标签要先拦一下 */
const savedAuxiliary = ref<{ partner: string | null; department: string | null; project: string | null }>({
  partner: null,
  department: null,
  project: null,
})

function snapshotAuxiliary() {
  savedAuxiliary.value = {
    partner: selectedPartner.value,
    department: selectedDepartment.value,
    project: selectedProject.value,
  }
}

function auxiliaryDirty(): boolean {
  return (
    selectedPartner.value !== savedAuxiliary.value.partner ||
    selectedDepartment.value !== savedAuxiliary.value.department ||
    selectedProject.value !== savedAuxiliary.value.project
  )
}

const partnerOptions = computed(() => partners.value.map((item) => ({ label: `${item.code} ${item.name}`, value: item.id })))
const departmentOptions = computed(() => departments.value.map((item) => ({ label: item.name, value: item.id })))
const projectOptions = computed(() => projects.value.map((item) => ({ label: `${item.code} ${item.name}`, value: item.id })))

async function loadAuxiliaryOptions() {
  if (!isDraft.value) return
  try {
    const data = await getAuxiliaryOptions('', 100)
    partners.value = data.partners
    departments.value = data.departments
    projects.value = data.projects
    // 回显：取分录里第一个挂了辅助的行
    const lines = (entry.value as any)?.voucherLines || {}
    const allLines = [...(lines.debit || []), ...(lines.credit || [])]
    const first = allLines.find((line: any) => line?.auxiliary?.partner_id || line?.auxiliary?.department_id || line?.auxiliary?.project_id)
    if (first) {
      selectedPartner.value = first.auxiliary.partner_id || null
      selectedDepartment.value = first.auxiliary.department_id || null
      selectedProject.value = first.auxiliary.project_id || null
    }
    snapshotAuxiliary()
  } catch {
    // 选项加载失败不阻塞凭证查看
  }
}

async function saveAuxiliary() {
  if (!entry.value) return
  if (!selectedPartner.value && !selectedDepartment.value && !selectedProject.value) {
    message.warning('请至少选择一个辅助核算项')
    return
  }
  savingAuxiliary.value = true
  try {
    const result = await assignEntryAuxiliary(entry.value.id, {
      partner_id: selectedPartner.value,
      department_id: selectedDepartment.value,
      project_id: selectedProject.value,
    })
    message.success(`已挂到 ${result.updatedLines} 条分录`)
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
  await loadAuxiliaryOptions()
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
        <NSelect v-model:value="selectedPartner" :options="partnerOptions" placeholder="往来单位" clearable filterable style="width: 220px;" />
        <NSelect v-model:value="selectedDepartment" :options="departmentOptions" placeholder="部门" clearable filterable style="width: 160px;" />
        <NSelect v-model:value="selectedProject" :options="projectOptions" placeholder="项目" clearable filterable style="width: 180px;" />
        <NButton type="primary" :loading="savingAuxiliary" @click="saveAuxiliary">挂到凭证</NButton>
      </div>
    </NCard>

    <VoucherSheet :entry="entry as unknown as Record<string, any>" :company-name="companyName" :rd-project-labels="rdProjectLabels" />
  </div>
</template>

<style scoped>
.voucher-detail-page { max-width: 760px; margin: 0 auto; }
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
@media print {
  .voucher-detail-page { max-width: 100%; margin: 0; padding: 0; }
}
</style>
