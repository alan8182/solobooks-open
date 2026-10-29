<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<!-- 报表页「老板视图 / 会计视图」切换控件；偏好按公司持久化，四张法定报表共用 -->
<script setup lang="ts">
import { NRadioButton, NRadioGroup } from 'naive-ui'
import { useStatementViewMode, type StatementViewMode } from '@/composables/useStatementViewMode'

const { mode, setMode } = useStatementViewMode()
</script>

<template>
  <NRadioGroup
    :value="mode"
    size="small"
    data-testid="statement-view-toggle"
    @update:value="setMode($event as StatementViewMode)"
  >
    <NRadioButton value="boss">老板视图</NRadioButton>
    <NRadioButton value="accounting">会计视图</NRadioButton>
  </NRadioGroup>
</template>
