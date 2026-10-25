<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { NConfigProvider, NMessageProvider, NDialogProvider, zhCN, dateZhCN } from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'
import StartDateGuideModal from '@/components/business/StartDateGuideModal.vue'
import RescueDialog from '@/components/business/RescueDialog.vue'
import BetaContactWidget from '@/components/business/BetaContactWidget.vue'

/* naive-ui 默认的 success/warning/info/error 是另一套色（#18a058 / #f0a020 /
   #2080f0 / #d03050），白底上仅 2.1–3.9:1，和品牌语义色并存还会多出三个蓝绿色。
   这里把它们对齐到 design token，同时把 depth=3 文本与输入框占位符收进 token
   （默认 #c2c2c2 在白底上只有 1.78:1，等于看不见）。 */
const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#1747B0',
    primaryColorHover: '#2B66E2',
    primaryColorPressed: '#0D3282',
    primaryColorSuppl: '#1747B0',

    infoColor: '#1747B0',
    infoColorHover: '#2B66E2',
    infoColorPressed: '#0D3282',
    infoColorSuppl: '#1747B0',

    successColor: '#0F7354',
    successColorHover: '#128A64',
    successColorPressed: '#0B5B42',
    successColorSuppl: '#0F7354',

    warningColor: '#A05C10',
    warningColorHover: '#BD6D14',
    warningColorPressed: '#824A0C',
    warningColorSuppl: '#A05C10',

    errorColor: '#BD3D3D',
    errorColorHover: '#D14A4A',
    errorColorPressed: '#9C3030',
    errorColorSuppl: '#BD3D3D',

    borderRadius: '8px',
    // 圆角全站只有四档：4 / 8 / 12 / 999。naive 自己的 small 档默认 3px，
    // 跟我们的 4px 差一点点，统一对齐后组件内部（tag、输入框内小元素）不会露馅。
    borderRadiusSmall: '4px',
    bodyColor: '#F6F7F9',
    cardColor: '#FFFFFF',
    textColorBase: '#182230',
    textColor1: '#182230',
    textColor2: '#182230',
    textColor3: '#636e7c',
    placeholderColor: '#636e7c',
    // naive 默认的禁用文字是 #c2c2c2（白底 1.78:1），禁用态虽然 WCAG 豁免，
    // 但"看不见这个选项存在"是可用性问题，不是合规问题。
    textColorDisabled: '#6b7280',
  },

  /* 步骤条的「未到达」态按 naive 默认会退到禁用灰（1.78:1），
     空态引导里的三步说明直接看不见。这里把它压到次级灰：
     仍然弱于当前步骤的品牌蓝，但读得清。 */
  Steps: {
    indicatorTextColorWait: '#636e7c',
    indicatorColorWait: '#f1f4f8',
    headerTextColorWait: '#586474',
    descriptionTextColorWait: '#636e7c',
  },

  /* 分段控件只有一种长相：浅底凹槽 + 白色滑块。
     此前"月度/季度/年度"是裸 naive 样式、"全部/待复核/使用中"由页面自己套了一个
     白底描边容器 —— 同一交互两种视觉。定在主题层后，全站 20 个 NRadioGroup
     自动统一，页面不再各自发明容器。 */
  Radio: {
    buttonColor: 'transparent',
    buttonColorHover: 'rgba(255, 255, 255, 0.55)',
    buttonColorActive: '#FFFFFF',
    buttonTextColor: '#586474',
    buttonTextColorHover: '#182230',
    buttonTextColorActive: '#1747B0',
    buttonBorderColor: 'transparent',
    buttonBorderColorHover: 'transparent',
    buttonBorderColorActive: 'transparent',
    // 6px 不是第五档圆角：它是"凹槽 8px − 内边距 3px"的几何派生值，
    // 换成 4 会让滑块在凹槽里显得过分内缩。
    buttonBorderRadius: '6px',
    buttonBoxShadow: 'none',
    buttonBoxShadowHover: 'none',
    buttonBoxShadowActive: '0 1px 2px rgba(24, 34, 48, 0.10)',
    buttonBoxShadowFocus: '0 0 0 3px rgba(23, 71, 176, 0.12)',
  },

  /* Tag 的底色 naive 是拿文字色自算的（warning 落成 #f1e7db），
     配 token 化的深色文字只有 4.25:1——差一点过不去。这里把底色固定成
     与 SoloStatusBadge 同一组 *_bg token，两套标签从此同色。
     注意 colorBordered* 和 color* 是两套：带边框的 Tag（默认 bordered=true）
     用的是前者，只改 color* 对「待对账」这类带框标签无效。 */
  Tag: {
    colorSuccess: '#eaf7f1',
    colorWarning: '#fff5e8',
    colorError: '#fbeeee',
    colorInfo: '#edf3ff',
    colorBorderedSuccess: '#eaf7f1',
    colorBorderedWarning: '#fff5e8',
    colorBorderedError: '#fbeeee',
    colorBorderedInfo: '#edf3ff',
    textColorSuccess: '#0f7354',
    textColorWarning: '#a05c10',
    textColorError: '#bd3d3d',
    textColorInfo: '#1747b0',
    borderSuccess: '#c9e8d9',
    borderWarning: '#f2ddbd',
    borderError: '#efd0d0',
    borderInfo: '#cbd9f5',
    borderRadius: '4px',
  },
}
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="themeOverrides">
    <!-- 提示落在右下角。默认居中顶部会盖住企业切换器和全局搜索；右上角看着安全，
         但页面右上角正是「导出 / AI 记账 / 生成匹配候选」那一排主操作，
         切视图的提示条会正好压在上面、挡住点击。右下角是唯一没有常驻控件的位置。 -->
    <NMessageProvider
      placement="bottom-right"
      :container-style="{ bottom: '24px', right: '24px' }"
    >
      <NDialogProvider>
        <router-view />
        <StartDateGuideModal />
        <RescueDialog />
        <BetaContactWidget />
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>
