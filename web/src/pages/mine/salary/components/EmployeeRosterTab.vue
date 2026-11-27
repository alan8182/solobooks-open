<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import {
  NButton, NSpace, NTag, useMessage,
} from 'naive-ui'
import { deleteEmployee, type Employee, type DepartmentItem } from '@/api/salary'
import { formatMoney } from '@/utils/format'
import { useDeleteConfirm } from '@/composables/useDeleteConfirm'
import { SoloEmpty } from '@/components/ui'
import GuideTrigger from '@/components/business/GuideTrigger.vue'

const props = defineProps<{
  employees: Employee[]
  departments: DepartmentItem[]
}>()

const emit = defineEmits<{
  (e: 'add'): void
  (e: 'edit', emp: Employee): void
  (e: 'archived'): void
}>()

const message = useMessage()
const { confirmDelete } = useDeleteConfirm()

function isRdDepartment(deptName?: string) {
  if (!deptName) return false
  return Boolean(props.departments.find((d) => d.name === deptName)?.isRd)
}

const rosterStats = computed(() => {
  const baseTotal = Math.round(
    props.employees.reduce((sum, emp) => sum + Number(emp.base_salary || 0), 0) * 100,
  ) / 100
  const rdCount = props.employees.filter((emp) => isRdDepartment(emp.department)).length
  return {
    count: props.employees.length,
    baseTotal,
    rdCount,
  }
})

function handleArchive(emp: Employee) {
  if (!emp.id) return
  confirmDelete({
    title: '确认离职归档',
    content: `确定将员工“${emp.name}”设为离职归档吗？归档后将不再参与后续月份的薪酬计算。`,
    positiveText: '确认归档',
    onConfirm: async () => {
      try {
        await deleteEmployee(emp.id!)
        message.success(`员工“${emp.name}”已离职归档`)
        emit('archived')
      } catch (err: any) {
        message.error(err.response?.data?.detail || '归档员工失败')
      }
    },
  })
}
</script>

<template>
  <div class="roster-tab">
    <div v-if="employees.length > 0" class="roster-summary">
      <div class="summary-item">
        <span class="summary-label">在职人数</span>
        <strong class="summary-val">{{ rosterStats.count }} 人</strong>
      </div>
      <div class="summary-divider" />
      <div class="summary-item">
        <span class="summary-label">月固定基本工资合计</span>
        <strong class="summary-val">{{ formatMoney(rosterStats.baseTotal) }}</strong>
      </div>
      <div class="summary-divider" />
      <div class="summary-item">
        <span class="summary-label">研发部门人数</span>
        <strong class="summary-val">{{ rosterStats.rdCount }} 人</strong>
      </div>
    </div>

    <div class="roster-toolbar">
      <div class="toolbar-left">
        <span class="toolbar-hint">
          <i class="ri-information-line" />
          花名册是月度工资表的基准数据源，修改后将自动联动同步至当月草稿工资表。
        </span>
      </div>
      <div class="toolbar-right">
        <GuideTrigger guide="social-enrollment" label="员工参保/停保怎么办理？" />
        <NButton type="primary" size="small" @click="emit('add')">
          <template #icon><i class="ri-user-add-line" /></template>
          录入员工
        </NButton>
      </div>
    </div>

    <div v-if="employees.length > 0" class="table-container">
      <table class="roster-table">
        <thead>
          <tr>
            <th style="min-width: 100px">员工姓名</th>
            <th style="min-width: 100px">所属部门</th>
            <th style="min-width: 110px">入职日期</th>
            <th class="col-amount" style="min-width: 120px">基本工资（元）</th>
            <th class="col-amount" style="min-width: 120px">社保基数（元）</th>
            <th class="col-amount" style="min-width: 120px">公积金基数（元）</th>
            <th
              class="col-amount"
              style="min-width: 120px"
              title="员工在个人所得税 App 自己填报的每月扣除合计，这里只用于估算要代扣多少工资个税"
            >专项附加扣除（元）</th>
            <th style="width: 140px; text-align: center">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="emp in employees" :key="emp.id">
            <td class="emp-name-cell">
              <span class="emp-name">{{ emp.name }}</span>
              <NTag v-if="emp.is_owner" size="tiny" type="info" :bordered="false" class="owner-tag">
                业主
              </NTag>
            </td>
            <td>
              <NTag
                size="small"
                :bordered="false"
                :type="isRdDepartment(emp.department) ? 'info' : 'default'"
              >
                {{ emp.department || '未分配' }}
              </NTag>
            </td>
            <td class="num-cell">{{ emp.hire_date || '—' }}</td>
            <td class="col-amount num-cell">{{ formatMoney(emp.base_salary) }}</td>
            <td class="col-amount num-cell">{{ formatMoney(emp.social_security_base) }}</td>
            <td class="col-amount num-cell">
              <span
                v-if="emp.housing_fund_base == null"
                class="hf-follow-text"
                :title="`跟随社保基数：¥${formatMoney(emp.social_security_base)}`"
              >同社保</span>
              <NTag v-else-if="Number(emp.housing_fund_base) === 0" size="tiny" :bordered="false" type="default">
                不缴 (0.00)
              </NTag>
              <template v-else>{{ formatMoney(emp.housing_fund_base) }}</template>
            </td>
            <td class="col-amount num-cell">
              {{ Number(emp.special_additional_deduction || 0) > 0 ? formatMoney(emp.special_additional_deduction || 0) : '—' }}
            </td>
            <td class="action-cell">
              <NSpace justify="center" size="small">
                <NButton size="tiny" quaternary type="primary" @click="emit('edit', emp)">编辑</NButton>
                <NButton size="tiny" quaternary type="error" @click="handleArchive(emp)">离职归档</NButton>
              </NSpace>
            </td>
          </tr>
        </tbody>
        <tfoot>
          <tr class="roster-totals-row">
            <td colspan="3">合计 · {{ rosterStats.count }} 人</td>
            <td class="col-amount num-cell">{{ formatMoney(rosterStats.baseTotal) }}</td>
            <td colspan="4" />
          </tr>
        </tfoot>
      </table>
    </div>

    <div v-else class="empty-roster">
      <SoloEmpty
        title="花名册暂无在职员工"
        description="录入员工的基本工资与社保基数后，系统每个月将自动为您预填工资结算表与社保分摊。"
      />
      <div class="empty-action">
        <NButton type="primary" size="medium" @click="emit('add')">
          <template #icon><i class="ri-user-add-line" /></template>
          录入首位在职员工
        </NButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.roster-tab {
  display: flex;
  flex-direction: column;
}

.roster-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  padding: 12px 16px;
  margin-bottom: 12px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg);
}
.summary-item {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
}
.summary-label {
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.summary-val {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 15px;
  font-weight: 700;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
}
.summary-divider {
  width: 1px;
  height: 14px;
  background: var(--solo-border-light);
}

.roster-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  margin-bottom: 12px;
  gap: 12px;
}

.toolbar-hint {
  font-size: 12.5px;
  color: var(--solo-text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 0 0 auto;
}

.table-container {
  overflow-x: auto;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
}

.roster-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  text-align: left;
}

.roster-table th {
  background: var(--solo-bg-soft);
  color: var(--solo-text-secondary);
  font-weight: 600;
  padding: 10px 14px;
  border-bottom: 1px solid var(--solo-border-light);
  white-space: nowrap;
}

.roster-table td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--solo-border-light);
  color: var(--solo-text-primary);
}

.roster-table tbody tr:last-child td {
  border-bottom: none;
}

.roster-table tbody tr:hover {
  background: var(--solo-bg-soft);
}

.roster-totals-row td {
  border-top: 1px solid var(--solo-border-light);
  border-bottom: none;
  font-weight: 600;
  background: var(--solo-bg-soft);
}

.col-amount {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.num-cell {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-variant-numeric: tabular-nums;
}

.emp-name-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.emp-name {
  font-weight: 500;
}

.owner-tag {
  font-weight: 600;
}

.hf-follow-text {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.empty-roster {
  padding: 40px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}
</style>
