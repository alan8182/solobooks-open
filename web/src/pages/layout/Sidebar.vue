<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAV_ITEMS, type BossGroup, type NavGroup, type NavItem } from '@/utils/constants'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import { itemVisible } from '@/utils/workspace'

const props = withDefaults(defineProps<{ collapsed: boolean; mobile?: boolean }>(), {
  mobile: false,
})
const emit = defineEmits<{ toggle: []; navigate: [] }>()

const route = useRoute()
const router = useRouter()
const viewStore = useWorkspaceViewStore()
const expandedMenu = ref<string | null>(null)
const visibleItems = computed<NavItem[]>(() => {
  return NAV_ITEMS
    .filter((item) => itemVisible(item.workspace, viewStore.mode))
    .map((item) => ({
      ...item,
      children: item.children?.filter((child) => itemVisible(child.workspace, viewStore.mode)),
    }))
    .filter((item) => Boolean(item.path) || Boolean(item.children?.length))
})
const BOSS_GROUPS: BossGroup[] = ['每天', '每月', '复盘', '设置']
const WORKSPACE_GROUPS: NavGroup[] = ['日常', '账务', '周期', '工具']

// 设置收纳：老板视图折进左下角小图标（低频入口不占主航）；
// 会计视图必须留在导航里——会计科目、期初设置、备份与归档这些"不配就用不了"，
// 藏进 36px 图标后新用户根本不知道它们存在。
// 移动端抽屉本来就没有左下角图标位，设置一律留在导航里。
const SETTINGS_LABEL = '设置'
const navPool = computed<NavItem[]>(() => {
  if (props.mobile || viewStore.isAccounting) return visibleItems.value
  return visibleItems.value.filter((item) => item.label !== SETTINGS_LABEL)
})
const settingsItem = computed<NavItem | null>(
  () => visibleItems.value.find((item) => item.label === SETTINGS_LABEL) ?? null,
)
// 会计视图已把设置放回导航，左下角图标不再重复出现
const showSettingsLauncher = computed(
  () => !props.mobile && !viewStore.isAccounting && settingsItem.value !== null,
)
const settingsOpen = ref(false)
const settingsAnchorRef = ref<HTMLElement | null>(null)
const settingsActive = computed(
  () => settingsItem.value?.children?.some((child) => isActive(child.path)) ?? false,
)

// 菜单只由点击开关（不再悬停触发）；点别处或按 Esc 都收起。
function onDocClick(e: MouseEvent) {
  if (!settingsOpen.value) return
  const anchor = settingsAnchorRef.value
  if (anchor && !anchor.contains(e.target as Node)) settingsOpen.value = false
}

function onDocKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && settingsOpen.value) settingsOpen.value = false
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onDocKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onDocKeydown)
})

const navGroups = computed(() => {
  if (viewStore.isBoss) {
    return BOSS_GROUPS.map((label) => ({
      label,
      items: navPool.value.filter((item) => item.bossGroup === label),
    })).filter((group) => group.items.length > 0)
  }
  return WORKSPACE_GROUPS.map((label) => ({
    label,
    items: navPool.value.filter((item) => item.group === label),
  })).filter((group) => group.items.length > 0)
})

function isActive(path: string) {
  if (path === '/' || path === '/mine') return route.path === path
  return route.path.startsWith(path)
}

function isParentActive(item: NavItem) {
  return item.children?.some((child) => isActive(child.path)) ?? false
}

function itemDisplayLabel(item: NavItem) {
  return viewStore.isBoss && item.bossLabel ? item.bossLabel : item.label
}

function isExpanded(label: string) {
  return expandedMenu.value === label
}

function toggleMenu(label: string) {
  expandedMenu.value = isExpanded(label) ? null : label
}

function navigate(path: string) {
  settingsOpen.value = false
  router.push(path)
  emit('navigate')
}

watch(
  [() => route.path, () => viewStore.mode],
  () => {
    settingsOpen.value = false
    const activeParent = navPool.value.find((item) => isParentActive(item))
    expandedMenu.value = activeParent?.label ?? null
  },
  { immediate: true },
)
</script>

<template>
  <aside class="sidebar" :class="{ collapsed, mobile }">
    <div class="sidebar-header">
      <div class="logo-area" @click="router.push('/')">
        <div class="logo-icon">
          <img src="/solo-logo.png" alt="梭罗记账" />
        </div>
        <div v-if="!collapsed" class="logo-copy">
          <span class="logo-text">梭罗记账</span>
          <span class="logo-sub">SoloBooks</span>
        </div>
      </div>
      <button
        v-if="!mobile"
        class="toggle-btn"
        @click="emit('toggle')"
        :title="collapsed ? '展开' : '收起'"
      >
        <i :class="collapsed ? 'ri-arrow-right-s-line' : 'ri-arrow-left-s-line'" />
      </button>
    </div>

    <nav class="nav-list">
      <div v-for="group in navGroups" :key="group.label" class="nav-group">
        <span v-if="!collapsed" class="group-label">{{ group.label }}</span>
        <div
          v-for="item in group.items"
          :key="item.label"
          class="nav-entry"
          :class="{
            'has-children': item.children,
            open: item.children && isExpanded(item.label),
            'parent-active': item.children && isParentActive(item),
          }"
        >
          <button
            v-if="item.children"
            type="button"
            class="nav-item"
            :class="{ active: isParentActive(item) }"
            :title="collapsed ? itemDisplayLabel(item) : undefined"
            :aria-expanded="isExpanded(item.label)"
            @click="toggleMenu(item.label)"
          >
            <span class="nav-item-inner">
              <i :class="[item.icon, 'nav-icon']" />
              <span v-if="!collapsed" class="nav-label">{{ itemDisplayLabel(item) }}</span>
            </span>
            <i
              v-if="!collapsed"
              class="ri-arrow-down-s-line nav-chevron"
              :class="{ rotated: isExpanded(item.label) }"
              aria-hidden="true"
            />
          </button>

          <button
            v-else-if="item.path"
            type="button"
            class="nav-item"
            :class="{ active: isActive(item.path) }"
            :title="collapsed ? itemDisplayLabel(item) : undefined"
            @click="navigate(item.path)"
          >
            <span class="nav-item-inner">
              <i :class="[item.icon, 'nav-icon']" />
              <span v-if="!collapsed" class="nav-label">{{ itemDisplayLabel(item) }}</span>
            </span>
          </button>

          <div
            v-if="item.children && (isExpanded(item.label) || collapsed)"
            class="subnav"
            :class="{ flyout: collapsed }"
            :aria-label="`${item.label}二级菜单`"
          >
            <span v-if="collapsed" class="subnav-title">{{ item.label }}</span>
            <button
              v-for="child in item.children"
              :key="child.path"
              type="button"
              class="subnav-item"
              :class="{ active: isActive(child.path) }"
              @click="navigate(child.path)"
            >
              <i :class="[child.icon, 'subnav-icon']" />
              <span>{{ child.label }}</span>
            </button>
          </div>
        </div>
      </div>
    </nav>

    <div v-if="!mobile" class="view-switch-footer">
      <div v-if="showSettingsLauncher" ref="settingsAnchorRef" class="settings-anchor" :class="{ open: settingsOpen }">
        <button
          type="button"
          class="settings-btn"
          :class="{ active: settingsActive }"
          :title="settingsActive ? '设置（当前所在）' : '设置'"
          aria-label="设置菜单"
          aria-haspopup="menu"
          :aria-expanded="settingsOpen"
          @click="settingsOpen = !settingsOpen"
        >
          <i class="ri-settings-4-line" />
          <span v-if="!collapsed" class="settings-btn-label">设置</span>
        </button>
        <div class="settings-flyout" :aria-label="`设置二级菜单`">
          <span class="subnav-title">设置</span>
          <button
            v-for="child in settingsItem?.children ?? []"
            :key="child.path"
            type="button"
            class="subnav-item"
            :class="{ active: isActive(child.path) }"
            @click="navigate(child.path)"
          >
            <i :class="[child.icon, 'subnav-icon']" />
            <span>{{ child.label }}</span>
          </button>
        </div>
      </div>
      <button
        type="button"
        class="view-switch-hint"
        :title="viewStore.isBoss ? '切换到会计视图' : '切换到老板视图'"
        @click="viewStore.toggleMode()"
      >
        <i :class="viewStore.isBoss ? 'ri-file-list-3-line' : 'ri-dashboard-3-line'" />
        <span v-if="!collapsed">
          {{ viewStore.isBoss ? '需要做凭证？切换到会计视图' : '查看老板摘要' }}
        </span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  width: var(--sidebar-width);
  background: var(--solo-bg-sidebar);
  border-right: 1px solid var(--solo-border-sidebar);
  display: flex;
  flex-direction: column;
  z-index: 100;
  transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: visible;
  box-shadow: 1px 0 0 var(--solo-border-sidebar);
}
.sidebar.collapsed {
  width: var(--sidebar-collapsed-width);
}
.sidebar.mobile {
  position: static;
  width: 100%;
  height: auto;
  min-height: 0;
  flex: 1;
  border-right: 0;
  box-shadow: none;
}
.sidebar.mobile .nav-list {
  overflow-y: auto;
}
.sidebar.mobile .sidebar-header {
  padding-inline: 16px;
}
.sidebar-header {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 18px;
  height: var(--navbar-height);
  border-bottom: 1px solid var(--solo-border-sidebar);
}
.logo-area {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}
.logo-icon {
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.logo-icon img {
  width: 100%;
  height: 100%;
  display: block;
}
.logo-copy {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
}
.logo-text {
  font-size: 17px;
  font-weight: 750;
  color: var(--solo-text-primary);
  letter-spacing: 2px;
}
.logo-sub {
  margin-top: 4px;
  color: var(--solo-text-muted);
  font-size: 9px;
  letter-spacing: 0.8px;
  text-transform: uppercase;
}
.toggle-btn {
  position: absolute;
  right: -12px;
  top: calc(var(--navbar-height) / 2 - 12px);
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #fff;
  border: 1px solid var(--solo-border);
  color: var(--solo-text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: var(--solo-shadow-sm);
  transition: all 0.2s;
  z-index: 105;
}
.toggle-btn:hover {
  color: var(--solo-primary);
  border-color: var(--solo-primary);
  transform: scale(1.1);
}
.nav-list {
  flex: 1;
  padding: 20px 12px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.nav-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.group-label {
  padding: 0 10px 6px;
  color: var(--solo-text-muted);
  font-size: 10px;
  font-weight: 650;
  letter-spacing: 1.5px;
}
/* Hide scrollbar */
.nav-list::-webkit-scrollbar {
  display: none;
}
.sidebar.collapsed .nav-list {
  overflow: visible;
}
.view-switch-footer {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 2px;
  padding: 10px 12px 14px;
  border-top: 1px solid var(--solo-border-sidebar);
}
.settings-anchor {
  position: relative;
  flex-shrink: 0;
}
.settings-btn {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 34px;
  padding: 7px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--solo-text-muted);
  font: inherit;
  font-size: 13px;
  font-weight: 550;
  text-align: left;
  cursor: pointer;
  transition: background-color 160ms ease-out, color 160ms ease-out;
}
.settings-btn > i {
  font-size: 18px;
  width: 20px;
  text-align: center;
  flex-shrink: 0;
}
.sidebar.collapsed .settings-btn {
  width: 32px;
  justify-content: center;
  padding-inline: 0;
}
.settings-btn:hover {
  color: var(--solo-primary);
  background: var(--solo-bg-soft);
}
.settings-btn.active {
  color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}
.settings-flyout {
  position: absolute;
  bottom: calc(100% + 12px);
  left: 0;
  z-index: 130;
  display: none;
  width: 184px;
  max-height: 62vh;
  overflow-y: auto;
  padding: 8px;
  border: 1px solid var(--solo-border);
  border-radius: 8px;
  background: var(--solo-bg-sidebar);
  box-shadow: var(--solo-shadow);
  flex-direction: column;
  gap: 2px;
}
.settings-anchor.open > .settings-flyout {
  display: flex;
}
.sidebar.collapsed .settings-flyout {
  bottom: 0;
  left: calc(100% + 12px);
}
.sidebar.collapsed .settings-anchor {
  align-self: center;
}
.view-switch-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  padding: 8px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--solo-text-muted);
  font: inherit;
  font-size: 11px;
  text-align: left;
  cursor: pointer;
  transition: background-color 160ms ease-out, color 160ms ease-out;
}
.view-switch-hint:hover {
  background: var(--solo-bg-soft);
  color: var(--solo-primary);
}
.view-switch-hint:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: -2px;
}
.sidebar.collapsed .view-switch-hint {
  justify-content: center;
  padding-inline: 0;
}
.nav-entry {
  position: relative;
}
.nav-item {
  width: 100%;
  min-height: 40px;
  padding: 9px 10px;
  border: 0;
  border-radius: 8px;
  cursor: pointer;
  color: var(--solo-text-sidebar);
  background: transparent;
  text-align: left;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  white-space: nowrap;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.nav-item-inner {
  display: flex;
  align-items: center;
  gap: 12px;
}
.nav-item:hover {
  color: var(--solo-text-sidebar-hover);
  background: var(--solo-bg-soft);
}
.nav-item:hover .nav-icon {
  color: var(--solo-primary);
}
.nav-item.active {
  color: var(--solo-text-sidebar-active);
  background: var(--solo-bg-blue-soft);
  box-shadow: none;
}
.nav-item.active .nav-icon {
  color: var(--solo-primary);
  transform: none;
}
.nav-icon {
  font-size: 18px;
  width: 20px;
  text-align: center;
  flex-shrink: 0;
  transition: transform 0.2s;
  color: inherit;
}
.nav-label {
  font-size: 13px;
  font-weight: 550;
  letter-spacing: 0.1px;
}
.nav-chevron {
  color: var(--solo-text-muted);
  font-size: 16px;
  transition: transform 180ms cubic-bezier(0.16, 1, 0.3, 1);
}
.nav-chevron.rotated {
  transform: rotate(180deg);
}
.subnav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 3px 0 4px 30px;
  padding-left: 8px;
  border-left: 1px solid var(--solo-border-sidebar);
}
.subnav-item {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  min-height: 34px;
  padding: 7px 9px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--solo-text-muted);
  cursor: pointer;
  font-size: 12px;
  text-align: left;
  transition: background-color 160ms ease-out, color 160ms ease-out;
}
.subnav-item:hover {
  background: var(--solo-bg-soft);
  color: var(--solo-text-primary);
}
.subnav-item.active {
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  font-weight: 600;
}
.subnav-icon {
  width: 16px;
  flex-shrink: 0;
  font-size: 15px;
  text-align: center;
}
.sidebar.collapsed .nav-item {
  justify-content: center;
  padding-inline: 10px;
}
.sidebar.collapsed .nav-item-inner {
  gap: 0;
}
.subnav.flyout {
  position: absolute;
  top: 0;
  left: calc(100% + 10px);
  z-index: 120;
  display: none;
  width: 184px;
  margin: 0;
  padding: 8px;
  border: 1px solid var(--solo-border);
  border-radius: 8px;
  background: var(--solo-bg-sidebar);
  box-shadow: var(--solo-shadow);
}
.nav-entry:hover > .subnav.flyout,
.nav-entry:focus-within > .subnav.flyout,
.nav-entry.open > .subnav.flyout {
  display: flex;
}
.subnav-title {
  padding: 4px 8px 7px;
  color: var(--solo-text-muted);
  font-size: 11px;
  font-weight: 650;
}
@media (max-width: 1024px) {
  .sidebar,
  .sidebar.collapsed {
    display: none;
  }
  .sidebar.mobile {
    display: flex;
  }
}
</style>
