<script setup lang="ts">
import { NConfigProvider, NMessageProvider, NDialogProvider, zhCN, dateZhCN } from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#1747B0',
    primaryColorHover: '#2B66E2',
    primaryColorPressed: '#0D3282',
    primaryColorSuppl: '#1747B0',
    borderRadius: '8px',
    bodyColor: '#F6F7F9',
    cardColor: '#FFFFFF',
    textColorBase: '#182230',
  },
}
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="themeOverrides">
    <NMessageProvider><NDialogProvider><router-view /></NDialogProvider></NMessageProvider>
  </NConfigProvider>
</template>
