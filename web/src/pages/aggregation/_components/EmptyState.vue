<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
// 三步引导空状态。仅在 hasDimensions === false 时渲染（设计文档 §8.3）。
// 文案与步骤顺序是产品结论，不随代码改动；「创建示例维度」一次铺出
// 1 个维度 + 2 个归集对象，让用户能立刻看到后续页面的样子。
import { ref } from 'vue'
import { NButton, NCard, NEmpty, NSpace, NStep, NSteps, useMessage } from 'naive-ui'
import { useRouter } from 'vue-router'
import { createDimension, createMember } from '@/api/aggregation'
import { errorText } from '@/utils/aggregation'

const router = useRouter()
const message = useMessage()

const creating = ref(false)

/** 今天，YYYY-MM-DD。示例成员的 startDate 用当天，省得用户自己填 */
function today(): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

/**
 * 创建示例维度：1 次 createDimension + 2 次 createMember。
 * ⚠️ 串行而非并行——后两个请求依赖第一个返回的 dimensionId。
 * ⚠️ 失败不 rollback：维度与成员是独立资源，留下已建部分反而让用户看得见进展，
 *    补建比回滚更好。任一步失败即 message.error 并中断后续步骤。
 */
async function handleCreateSample(): Promise<void> {
  if (creating.value) return
  creating.value = true
  try {
    const dimension = await createDimension({ code: 'RD', name: '研发项目', kind: 'project' })
    await createMember(dimension.id, {
      code: 'RD-2026-01',
      name: '研发项目A',
      startDate: today(),
    })
    await createMember(dimension.id, {
      code: 'RD-2026-02',
      name: '研发项目B',
      startDate: today(),
    })
    message.success('已创建示例维度「研发项目」及 2 个归集对象')
  } catch (err) {
    message.error(errorText(err))
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <NCard :bordered="false" class="empty-card">
    <NEmpty description="还没有归集维度">
      <template #extra>
        <div class="empty-inner">
          <NSteps :current="0" size="small" class="empty-steps">
            <NStep
              title="建立维度"
              description="项目 / 部门 / 客户 / 供应商 / 自定义"
            />
            <NStep
              title="添加归集对象"
              description="在维度下建对象，可设起止日期"
            />
            <NStep
              title="配置规则"
              description="按科目前缀、方向、关键词自动归集"
            />
          </NSteps>
          <NSpace justify="center" :size="12">
            <NButton type="primary" :loading="creating" @click="handleCreateSample">
              创建示例维度
            </NButton>
            <NButton @click="router.push('/aggregation/dimensions')">
              手动建立维度
            </NButton>
          </NSpace>
          <p class="empty-note">
            示例维度会创建「研发项目」及 2 个归集对象，可随时归档。
          </p>
        </div>
      </template>
    </NEmpty>
  </NCard>
</template>

<style scoped>
.empty-card {
  border-radius: var(--solo-radius-lg);
}
.empty-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  width: min(760px, 88vw);
  padding: 8px 0 4px;
}
.empty-steps {
  width: 100%;
}
.empty-note {
  margin: 0;
  font-size: 12px;
  color: var(--solo-text-muted);
}
</style>