<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onActivated, onDeactivated, ref } from 'vue'
import { NDrawer, NDrawerContent } from 'naive-ui'

const props = withDefaults(
  defineProps<{
    show: boolean
    title?: string
    width?: number | string
    placement?: 'left' | 'right' | 'top' | 'bottom'
    closable?: boolean
    maskClosable?: boolean
    /**
     * 是否带遮罩。默认带——确认类、一次性流程类抽屉不该让人在背后乱点。
     *
     * 但「表格里的表单」建议关掉：遮罩是 fixed inset:0，会把页内标签栏一起盖住，
     * 用户填到一半想切去别的标签看数据就没路了。关掉遮罩后切走不丢内容（标签是
     * KeepAlive 缓存的），只有关标签才丢，而关标签有 useTabGuard 兜着。
     */
    mask?: boolean
  }>(),
  {
    title: '',
    width: 600,
    placement: 'right',
    closable: true,
    maskClosable: true,
    mask: true,
  },
)

const emit = defineEmits<{
  'update:show': [value: boolean]
  close: []
}>()

/**
 * 没有遮罩就等于没有「点遮罩关闭」这回事。
 * naive-ui 的 Drawer 即使 showMask=false 也挂着 onClickoutside→handleMaskClick，
 * 只关遮罩不关 maskClosable 的话，点页面任何地方都会把填了一半的表单关掉。
 */
const effectiveMaskClosable = computed(() => props.mask && props.maskClosable)

/**
 * 抽屉是 Teleport 到 body 的，页面被 KeepAlive 缓存时它的 DOM 不会跟着页面一起走——
 * 开着抽屉切到别的标签，抽屉会浮在人家的页面上。所以失活时把它从「显示」里摘掉
 * （displayDirective 是 if，等于卸载），切回来再挂上。
 * 注意不动父级的 show：表单数据在页面的 reactive 里，一个字都不会丢。
 */
const hiddenWhileInactive = ref(false)
onDeactivated(() => {
  hiddenWhileInactive.value = true
})
onActivated(() => {
  hiddenWhileInactive.value = false
})

const showProxy = computed({
  get: () => props.show && !hiddenWhileInactive.value,
  set: (val: boolean) => {
    emit('update:show', val)
    if (!val) emit('close')
  },
})
</script>

<template>
  <NDrawer
    v-model:show="showProxy"
    :width="width"
    :placement="placement"
    :show-mask="mask"
    :block-scroll="mask"
    :mask-closable="effectiveMaskClosable"
    :trap-focus="mask"
    :class="['solo-drawer', { 'solo-drawer--bare': !mask }]"
  >
    <NDrawerContent :closable="closable" :native-scrollbar="false">
      <template #header>
        <div class="solo-drawer-header">
          <slot name="header">
            <h3 class="drawer-title">{{ title }}</h3>
          </slot>
        </div>
      </template>

      <div class="solo-drawer-body">
        <slot />
      </div>

      <template v-if="$slots.footer" #footer>
        <div class="solo-drawer-footer">
          <slot name="footer" />
        </div>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.solo-drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.drawer-title {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  color: var(--solo-text-primary, #182230);
  letter-spacing: -0.2px;
}

.solo-drawer-body {
  padding: 4px 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.solo-drawer-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  width: 100%;
  padding: 4px 0;
}

</style>

<style>
/* 抽屉是 Teleport 到 body 的，自定义类名落在 .n-drawer 上但不带 scoped 的 data-v，
   所以这一条必须放在非 scoped 里。没有遮罩时面板和内容的边界全靠它。 */
.solo-drawer--bare.n-drawer {
  box-shadow: -10px 0 32px rgba(24, 34, 48, 0.14);
}
</style>
