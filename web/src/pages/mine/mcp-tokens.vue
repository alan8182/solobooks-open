<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NButton,
  NCheckbox,
  NDataTable,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NTag,
  NText,
  NAlert,
  useMessage,
  useDialog,
} from 'naive-ui'
import { createMcpToken, listMcpTokens, revokeMcpToken } from '@/api/mcpTokens'
import type { McpTokenItem } from '@/api/mcpTokens'
import { useAuthStore } from '@/stores/auth'

const message = useMessage()
const dialog = useDialog()
const authStore = useAuthStore()

const origin = window.location.origin

const tokens = ref<McpTokenItem[]>([])
const loading = ref(false)

// ── 新建令牌 ──
const createVisible = ref(false)
const creating = ref(false)
const newName = ref('')
const expiresDays = ref(365)
const scopeRead = ref(true)
const scopeWrite = ref(false)

const createdVisible = ref(false)
const createdToken = ref('')
const createdNotice = ref('')

const scopeOptions = computed(() => {
  const scopes = []
  if (scopeRead.value) scopes.push('read')
  if (scopeWrite.value) scopes.push('write:draft')
  return scopes.join(',')
})

const companyOptions = computed(() =>
  authStore.user?.companies?.map((company: any) => ({ label: company.name, value: company.company_id })) || [],
)
const selectedCompanyId = ref<string | null>(null)

function scopeLabel(scopes: string): string {
  const parts = scopes.split(',').map((s) => s.trim())
  if (parts.includes('write:draft')) return '读写草稿'
  return '只读'
}

function tokenStatus(row: McpTokenItem): { label: string; type: 'success' | 'error' | 'warning' } {
  if (row.revokedAt) return { label: '已吊销', type: 'error' }
  if (row.expiresAt && new Date(row.expiresAt).getTime() < Date.now()) return { label: '已过期', type: 'warning' }
  return { label: '有效', type: 'success' }
}

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const columns = [
  { title: '名称', key: 'name', width: 150, ellipsis: { tooltip: true } },
  {
    title: '权限',
    key: 'scopes',
    width: 100,
    render: (row: McpTokenItem) =>
      h(NTag, { size: 'small', type: row.scopes.includes('write:draft') ? 'warning' : 'default' }, { default: () => scopeLabel(row.scopes) }),
  },
  { title: '状态', key: 'status', width: 80, render: (row: McpTokenItem) => {
      const status = tokenStatus(row)
      return h(NTag, { size: 'small', type: status.type }, { default: () => status.label })
    } },
  { title: '最近使用', key: 'lastUsedAt', width: 130, render: (row: McpTokenItem) => formatTime(row.lastUsedAt) },
  { title: '创建时间', key: 'createdAt', width: 130, render: (row: McpTokenItem) => formatTime(row.createdAt) },
  {
    title: '操作',
    key: 'actions',
    width: 90,
    render: (row: McpTokenItem) =>
      row.revokedAt
        ? h(NText, { depth: 3 }, { default: () => '—' })
        : h(
            NButton,
            { size: 'small', quaternary: true, type: 'error', onClick: () => confirmRevoke(row) },
            { default: () => '吊销' },
          ),
  },
]

async function fetchTokens() {
  loading.value = true
  try {
    const res = await listMcpTokens()
    tokens.value = res.tokens
  } catch {
    message.error('加载令牌列表失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  newName.value = ''
  scopeRead.value = true
  scopeWrite.value = false
  expiresDays.value = 365
  selectedCompanyId.value = authStore.currentCompanyId || null
  createVisible.value = true
}

async function handleCreate() {
  if (!newName.value.trim()) {
    message.warning('请填写令牌名称')
    return
  }
  if (!scopeRead.value && !scopeWrite.value) {
    message.warning('请至少选择一种权限')
    return
  }
  creating.value = true
  try {
    const res = await createMcpToken({
      name: newName.value.trim(),
      scopes: scopeOptions.value || 'read',
      company_id: selectedCompanyId.value || undefined,
      expires_days: expiresDays.value,
    })
    createdToken.value = res.token
    createdNotice.value = res.notice
    createVisible.value = false
    createdVisible.value = true
    await fetchTokens()
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : detail?.message || '签发失败')
  } finally {
    creating.value = false
  }
}

const configExample = computed(() => `{
  "mcpServers": {
    "solobooks": {
      "url": "${origin}/mcp",
      "headers": { "Authorization": "Bearer <你的令牌>" }
    }
  }
}`)

// 签发弹窗里的完整配置：直接填入刚签发的令牌，粘贴即用
const createdConfig = computed(() => `{
  "mcpServers": {
    "solobooks": {
      "url": "${origin}/mcp",
      "headers": { "Authorization": "Bearer ${createdToken.value}" }
    }
  }
}`)

async function writeClipboard(text: string, successMsg: string) {
  try {
    await navigator.clipboard.writeText(text)
    message.success(successMsg)
  } catch {
    message.warning('复制失败，请手动选中复制')
  }
}

const copyToken = () => writeClipboard(createdToken.value, '令牌已复制到剪贴板')
const copyConfigExample = () => writeClipboard(configExample.value, '配置示例已复制，替换 <你的令牌> 后即可使用')
const copyCreatedConfig = () => writeClipboard(createdConfig.value, '完整配置已复制，粘贴到客户端的 mcpServers 中即可')

function confirmRevoke(row: McpTokenItem) {
  dialog.warning({
    title: '吊销令牌',
    content: `确定吊销「${row.name}」？使用该令牌的 AI 客户端将立即失去访问权限。`,
    positiveText: '吊销',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await revokeMcpToken(row.id)
        message.success('已吊销')
        await fetchTokens()
      } catch {
        message.error('吊销失败')
      }
    },
  })
}

onMounted(fetchTokens)
</script>

<template>
  <div class="mcp-tokens-page">
    <div class="page-header">
      <h2>AI 连接（MCP）</h2>
      <NButton type="primary" size="small" @click="openCreate">新建令牌</NButton>
    </div>

    <NAlert type="info" :show-icon="true" class="intro-alert">
      把「梭罗记账」接入你的 AI 助手（Claude Desktop、ZCode 等）：签发个人访问令牌后，AI
      可以查询账目、流水、结账检查等。即使授予「读写草稿」，AI 也只能起草——所有入账仍需你在产品内确认。
    </NAlert>

    <NDataTable
      :columns="columns"
      :data="tokens"
      :loading="loading"
      :bordered="false"
      striped
      size="small"
    />

    <div class="config-hint">
      <div class="config-hint-header">
        <h3>客户端配置示例</h3>
        <NButton size="tiny" quaternary @click="copyConfigExample">
          <template #icon><i class="ri-file-copy-line" /></template>
          复制
        </NButton>
      </div>
      <pre>{{ configExample }}</pre>
    </div>

    <NModal v-model:show="createVisible" preset="card" title="新建 MCP 令牌" style="max-width: 440px">
      <NForm label-placement="top">
        <NFormItem label="令牌名称">
          <NInput v-model:value="newName" placeholder="如：我的 Claude Desktop" maxlength="128" />
        </NFormItem>
        <NFormItem label="权限">
          <div class="scope-options">
            <NCheckbox v-model:checked="scopeRead" :disabled="true">只读（必选）</NCheckbox>
            <NCheckbox v-model:checked="scopeWrite">读写草稿（AI 可起草待确认凭证）</NCheckbox>
          </div>
        </NFormItem>
        <NFormItem label="绑定公司">
          <NSelect v-model:value="selectedCompanyId" :options="companyOptions" placeholder="选择公司" />
        </NFormItem>
        <NFormItem label="有效期（天）">
          <NInputNumber v-model:value="expiresDays" :min="1" :max="3650" style="width: 100%" />
        </NFormItem>
      </NForm>
      <template #footer>
        <div class="modal-footer">
          <NButton :disabled="creating" @click="createVisible = false">取消</NButton>
          <NButton type="primary" :loading="creating" @click="handleCreate">签发令牌</NButton>
        </div>
      </template>
    </NModal>

    <NModal v-model:show="createdVisible" preset="card" title="令牌已签发" style="max-width: 560px">
      <NAlert type="warning" :show-icon="true" class="mb-3">{{ createdNotice }}</NAlert>

      <div class="created-section">
        <div class="created-section-header">
          <h4>令牌</h4>
          <NButton size="tiny" quaternary @click="copyToken">
            <template #icon><i class="ri-file-copy-line" /></template>
            复制令牌
          </NButton>
        </div>
        <pre class="token-display">{{ createdToken }}</pre>
      </div>

      <div class="created-section">
        <div class="created-section-header">
          <h4>客户端配置（已填入令牌）</h4>
          <NButton size="tiny" type="primary" @click="copyCreatedConfig">
            <template #icon><i class="ri-file-copy-line" /></template>
            复制完整配置
          </NButton>
        </div>
        <pre class="token-display">{{ createdConfig }}</pre>
        <p class="created-hint">粘贴到 Claude Desktop / ZCode 等客户端配置文件的 mcpServers 中，重启客户端即可使用。</p>
      </div>

      <template #footer>
        <div class="modal-footer">
          <NButton @click="createdVisible = false">完成</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.mcp-tokens-page {
  max-width: 960px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}

.intro-alert {
  margin-bottom: 16px;
}

.mb-3 {
  margin-bottom: 12px;
}

.config-hint {
  margin-top: 24px;
  padding: 16px;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius);
  background: var(--solo-bg-soft);
}

.config-hint-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.config-hint-header h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.config-hint pre {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.scope-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.token-display {
  margin: 0;
  padding: 12px;
  border-radius: var(--solo-radius);
  background: var(--solo-bg-soft);
  font-size: 12.5px;
  word-break: break-all;
  white-space: pre-wrap;
  user-select: all;
}

.created-section {
  margin-bottom: 16px;
}

.created-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.created-section-header h4 {
  margin: 0;
  font-size: 13.5px;
  font-weight: 600;
}

.created-hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--solo-text-muted);
  line-height: 1.6;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
