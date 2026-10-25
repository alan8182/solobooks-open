<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { NText } from 'naive-ui'

withDefaults(defineProps<{
  /** 不同报表可替换的口径说明主体 */
  scope?: string
}>(), {
  scope: '本表由系统按已过账复式分录自动编制，用于内部经营分析与报税准备',
})
</script>

<template>
  <div class="report-scope-note" data-testid="report-scope-note">
    <i class="ri-information-line" aria-hidden="true" />
    <NText depth="2" class="report-scope-text">
      {{ scope }}；<strong>不作为法定财务报表</strong>。销售收入按不含税口径列示、增值税已单独计提；
      企业所得税含系统自动测算的纳税调增（补票后可转回）。正式申报请由财税人员复核。
    </NText>
  </div>
</template>

<style scoped>
.report-scope-note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 8px 12px;
  border-radius: 8px;
  /* 原先这里是临时写的 rgba(128,128,128,0.08)，与 --solo-bg-soft 叠出的灰比
     --solo-text-muted 只有 4.44:1，卡在 AA 线下。改为 token 底色 + 正文级字色。 */
  background: var(--solo-bg-soft);
  margin-bottom: 12px;
}

.report-scope-note .ri-information-line {
  flex-shrink: 0;
  margin-top: 2px;
  font-size: 14px;
  opacity: 0.7;
}

.report-scope-text {
  font-size: 12px;
  line-height: 1.6;
}
</style>
