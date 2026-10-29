<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NForm, NFormItem, NInput, NModal, useMessage } from 'naive-ui'
import { SoloButton } from '@/components/ui'
import { setPassword, updateProfile } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

/**
 * L0 起跑线：首次登录时的唯一一次引导。
 *
 * 与旧的「完善账号信息」弹窗合并成一个三步流程——之前是先弹昵称密码、
 * 用户关掉之后再被另一套引导打断，连续两次打断比不引导更糟。
 */
const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ 'update:show': [boolean] }>()

const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()

const step = ref(1)
const nickname = ref('')
const password = ref('')
const saving = ref(false)

const currentCompany = computed(
  () =>
    authStore.companies.find((item) => item.company_id === authStore.currentCompanyId) ?? null,
)
// 只有真的缺东西才显示第 2 步，已经完善过的账号直接跳到第 3 步
const needProfile = computed(
  () => !String(authStore.user?.nickname || '').trim() || !authStore.user?.has_password,
)

watch(
  () => props.show,
  (visible) => {
    if (!visible) return
    step.value = 1
    nickname.value = authStore.user?.nickname || ''
    password.value = ''
  },
)

function close() {
  emit('update:show', false)
}

function next() {
  if (step.value === 1 && !needProfile.value) {
    step.value = 3
    return
  }
  step.value = Math.min(3, step.value + 1)
}

async function saveProfile() {
  const user = authStore.user
  const nextNickname = nickname.value.trim()
  const nicknameChanged = nextNickname !== String(user?.nickname || '').trim()
  const wantPassword = !user?.has_password && Boolean(password.value)
  if (!nicknameChanged && !wantPassword) {
    next()
    return
  }
  if (nextNickname.length > 20) {
    message.warning('昵称不能超过 20 个字')
    return
  }
  if (password.value && password.value.length < 8) {
    message.warning('密码长度至少 8 位')
    return
  }
  saving.value = true
  try {
    if (nicknameChanged) await updateProfile({ nickname: nextNickname })
    if (wantPassword) await setPassword({ new_password: password.value })
    await authStore.fetchProfile()
    message.success('已保存')
    step.value = 3
  } catch (err) {
    const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(detail || '保存失败，请重试')
  } finally {
    saving.value = false
  }
}

function go(path: string) {
  close()
  router.push(path)
}
</script>

<template>
  <NModal
    :show="props.show"
    preset="card"
    :title="step === 1 ? '先认识一下你的账套' : step === 2 ? '补上署名' : '从哪开始'"
    class="setup-welcome"
    :style="{ width: '520px', maxWidth: '92vw' }"
    :mask-closable="true"
    @update:show="emit('update:show', $event)"
  >
    <template v-if="step === 1">
      <div class="welcome-block">
        <p class="welcome-company">{{ currentCompany?.name || '当前账套' }}</p>
        <p class="welcome-line">
          启用日期
          <strong>{{ currentCompany?.startDate || '未填写' }}</strong>
        </p>
      </div>
      <p class="welcome-note">
        启用日期之前的钱和欠款记在「期初」，不逐笔记账。这样第一张报表就是对的。
      </p>
    </template>

    <template v-else-if="step === 2">
      <NForm label-placement="top" :show-feedback="false">
        <NFormItem label="昵称">
          <NInput v-model:value="nickname" maxlength="20" placeholder="怎么称呼你" />
        </NFormItem>
        <NFormItem v-if="!authStore.user?.has_password" label="登录密码（可选）">
          <NInput
            v-model:value="password"
            type="password"
            show-password-on="click"
            placeholder="至少 8 位，设置后可用密码登录"
          />
        </NFormItem>
      </NForm>
      <p class="welcome-note">只有你自己看得到，用来在手机号登录之外多一种进入方式。</p>
    </template>

    <template v-else>
      <div class="start-options">
        <button type="button" class="start-option" @click="go('/copilot?scene=new')">
          <i class="ri-seedling-line" aria-hidden="true" />
          <span class="start-copy">
            <strong>我是新成立的公司 · 从零建账</strong>
            <span>AI 会计对话引导录入主体信息，0 期初直接开记</span>
          </span>
        </button>
        <button type="button" class="start-option" @click="go('/copilot?scene=migration')">
          <i class="ri-exchange-box-line" aria-hidden="true" />
          <span class="start-copy">
            <strong>从其他代账/软件转入 · 导入期初</strong>
            <span>支持在 AI 对话窗口口述余额或上传科目余额表 Excel</span>
          </span>
        </button>
        <button type="button" class="start-option" @click="go('/copilot')">
          <i class="ri-sparkling-fill" aria-hidden="true" />
          <span class="start-copy">
            <strong>说一句就记账</strong>
            <span>「昨天请客户吃饭 380 微信付」</span>
          </span>
        </button>
        <button type="button" class="start-option" @click="go('/guide')">
          <i class="ri-eye-line" aria-hidden="true" />
          <span class="start-copy">
            <strong>先看看示例账套</strong>
            <span>拿一本三个月的演示账对着看</span>
          </span>
        </button>
      </div>
    </template>

    <template #footer>
      <div class="welcome-footer">
        <SoloButton variant="ghost" size="small" @click="close">稍后再说</SoloButton>
        <SoloButton
          v-if="step === 1"
          variant="primary"
          size="small"
          @click="next"
        >
          下一步
        </SoloButton>
        <SoloButton
          v-else-if="step === 2"
          variant="primary"
          size="small"
          :loading="saving"
          @click="saveProfile"
        >
          保存
        </SoloButton>
        <SoloButton v-else variant="primary" size="small" @click="close">知道了</SoloButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.welcome-block {
  padding: 12px 14px;
  border-radius: var(--solo-radius-control);
  background: var(--solo-bg-soft);
}

.welcome-company {
  margin: 0;
  color: var(--solo-text-primary);
  font-size: 15px;
  font-weight: 650;
}

.welcome-line {
  margin: 6px 0 0;
  color: var(--solo-text-secondary);
  font-size: 12.5px;
}

.welcome-line strong {
  color: var(--solo-text-primary);
  font-weight: 650;
}

.welcome-note {
  margin: 12px 0 0;
  color: var(--solo-text-secondary);
  font-size: 12.5px;
  line-height: 1.6;
}

.start-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.start-option {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 12px 14px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-control);
  background: var(--solo-bg-card);
  color: var(--solo-text-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 160ms ease-out, background-color 160ms ease-out;
}

.start-option:hover {
  border-color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}

.start-option:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: 1px;
}

.start-option > i {
  color: var(--solo-primary);
  font-size: 18px;
}

.start-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.start-copy strong {
  font-size: 13px;
  font-weight: 650;
}

.start-copy span {
  color: var(--solo-text-secondary);
  font-size: 11.5px;
}

.welcome-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
</style>
