<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAV_ITEMS, type BossGroup, type NavChildItem, type NavGroup, type NavItem } from '@/utils/constants'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import { itemVisible } from '@/utils/workspace'
import { useClosingStatus } from '@/composables/useClosingStatus'

const props = withDefaults(defineProps<{ collapsed: boolean; mobile?: boolean }>(), {
  mobile: false,
})
const emit = defineEmits<{ toggle: []; navigate: [] }>()

const route = useRoute()
const router = useRouter()
const viewStore = useWorkspaceViewStore()
const closingStatus = useClosingStatus()
const expandedMenu = ref<string | null>(null)

const BOSS_GROUPS: BossGroup[] = ['每月', '每天', '复盘']
const WORKSPACE_GROUPS: NavGroup[] = ['日常', '账务', '周期', '工具']

// 老板视图不做 workspace 过滤：能力一个不少，只是主航只留 bossGroup 项（7 项），
// 其余入口全部折进左下角「更多」。会计视图仍按 workspace 过滤、设置留在导航里。
const visibleItems = computed<NavItem[]>(() => {
  if (viewStore.isBoss) return NAV_ITEMS
  return NAV_ITEMS
    .filter((item) => itemVisible(item.workspace, viewStore.mode))
    .map((item) => ({
      ...item,
      children: item.children?.filter((child) => itemVisible(child.workspace, viewStore.mode)),
    }))
    .filter((item) => Boolean(item.path) || Boolean(item.children?.length))
})

const navPool = computed<NavItem[]>(() => {
  if (viewStore.isAccounting) return visibleItems.value
  // 只有分到「每月 / 每天 / 复盘」的项进主航；设置与专业页都走「更多」
  return visibleItems.value.filter(
    (item) => Boolean(item.bossGroup) && BOSS_GROUPS.includes(item.bossGroup as BossGroup),
  )
})

interface MoreLink {
  path: string
  label: string
  icon: string
}
interface MoreSection {
  label: string
  items: MoreLink[]
}

// 「更多」收录所有非主航入口：顶层项按 group 合并（经营账本 + 发票台账 → 账务），
// 父组用自己的名字（凭证与账簿 / 专项核算 / 设置）。桌面 flyout 与移动端抽屉共用。
const moreSections = computed<MoreSection[]>(() => {
  const sections: MoreSection[] = []
  for (const item of NAV_ITEMS) {
    // 只跳过真正在主航的项；设置虽然带 bossGroup='设置'，仍要进「更多」
    if (item.bossGroup && BOSS_GROUPS.includes(item.bossGroup as BossGroup)) continue
    const links: MoreLink[] = item.path
      ? [{ path: item.path, label: item.label, icon: item.icon }]
      : (item.children ?? []).map((child) => ({
          path: child.path,
          label: childDisplayLabel(child),
          icon: child.icon,
        }))
    if (!links.length) continue
    const label = item.path ? item.group : item.label
    const last = sections[sections.length - 1]
    if (last && last.label === label) last.items.push(...links)
    else sections.push({ label, items: links })
  }
  return sections
})

const showMoreLauncher = computed(() => !props.mobile && viewStore.isBoss)
const moreOpen = ref(false)
const moreAnchorRef = ref<HTMLElement | null>(null)
const moreActive = computed(
  () => moreSections.value.some((section) => section.items.some((link) => isActive(link.path))),
)

// 菜单只由点击开关（不再悬停触发）；点别处或按 Esc 都收起。
function onDocClick(e: MouseEvent) {
  if (!moreOpen.value) return
  const anchor = moreAnchorRef.value
  if (anchor && !anchor.contains(e.target as Node)) moreOpen.value = false
}

function onDocKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && moreOpen.value) moreOpen.value = false
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
  if (viewStore.isBoss) {
    if (path === '/bank' || path === '/private-expenses') {
      return (
        route.path.startsWith('/bank') ||
        route.path.startsWith('/private-expenses') ||
        route.path.startsWith('/reports/advance-balances') ||
        route.path.startsWith('/reports/ar-aging') ||
        route.path.startsWith('/reports/ap-aging')
      )
    }
    if (path === '/reports') {
      if (
        route.path.startsWith('/reports/advance-balances') ||
        route.path.startsWith('/reports/ar-aging') ||
        route.path.startsWith('/reports/ap-aging')
      ) {
        return false
      }
      return route.path.startsWith('/reports')
    }
  }
  return route.path.startsWith(path)
}

function isParentActive(item: NavItem) {
  return item.children?.some((child) => isActive(child.path)) ?? false
}

function itemDisplayLabel(item: NavItem) {
  return viewStore.isBoss && item.bossLabel ? item.bossLabel : item.label
}

function childDisplayLabel(child: NavChildItem) {
  return viewStore.isBoss && child.bossLabel ? child.bossLabel : child.label
}

function isExpanded(label: string) {
  return expandedMenu.value === label
}

function toggleMenu(label: string) {
  expandedMenu.value = isExpanded(label) ? null : label
}

function navigate(path: string) {
  moreOpen.value = false
  router.push(path)
  emit('navigate')
}

watch(
  [() => route.path, () => viewStore.mode],
  () => {
    moreOpen.value = false
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
            :class="{
              active: isActive(item.path),
              'nav-item--hero': item.path === '/closing-wizard',
            }"
            :title="item.path === '/closing-wizard'
              ? closingStatus.badgeTitle.value
              : collapsed ? itemDisplayLabel(item) : undefined"
            @click="navigate(item.path)"
          >
            <span class="nav-item-inner">
              <i :class="[item.icon, 'nav-icon']" />
              <span v-if="!collapsed" class="nav-label">{{ itemDisplayLabel(item) }}</span>
            </span>
            <template v-if="item.path === '/closing-wizard'">
              <span
                v-if="!collapsed"
                class="closing-badge"
                :class="closingStatus.badgeType.value"
                :title="closingStatus.badgeTitle.value"
              >
                {{ closingStatus.badgeText.value }}
              </span>
              <span
                v-else
                class="closing-dot"
                :class="closingStatus.badgeType.value"
              />
            </template>
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
              <span>{{ childDisplayLabel(child) }}</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 移动端没有左下角图标位：把「更多」作为可展开分组放进抽屉 -->
      <div v-if="mobile && viewStore.isBoss" class="nav-group">
        <span v-if="!collapsed" class="group-label">更多</span>
        <div class="nav-entry" :class="{ open: isExpanded('更多'), 'parent-active': moreActive }">
          <button
            type="button"
            class="nav-item"
            :aria-expanded="isExpanded('更多')"
            @click="toggleMenu('更多')"
          >
            <span class="nav-item-inner">
              <i class="ri-apps-2-line nav-icon" />
              <span class="nav-label">更多</span>
            </span>
            <i
              class="ri-arrow-down-s-line nav-chevron"
              :class="{ rotated: isExpanded('更多') }"
              aria-hidden="true"
            />
          </button>
          <div v-if="isExpanded('更多')" class="subnav" aria-label="更多二级菜单">
            <template v-for="section in moreSections" :key="section.label">
              <span class="subnav-title">{{ section.label }}</span>
              <button
                v-for="link in section.items"
                :key="link.path"
                type="button"
                class="subnav-item"
                :class="{ active: isActive(link.path) }"
                @click="navigate(link.path)"
              >
                <i :class="[link.icon, 'subnav-icon']" />
                <span>{{ link.label }}</span>
              </button>
            </template>
          </div>
        </div>
      </div>
    </nav>

    <div v-if="!mobile" class="view-switch-footer">
      <div v-if="showMoreLauncher" ref="moreAnchorRef" class="settings-anchor" :class="{ open: moreOpen }">
        <button
          type="button"
          class="settings-btn"
          :class="{ active: moreActive }"
          :title="moreActive ? '更多（当前所在）' : '更多'"
          aria-label="更多菜单"
          aria-haspopup="menu"
          :aria-expanded="moreOpen"
          @click="moreOpen = !moreOpen"
        >
          <i class="ri-apps-2-line" />
          <span v-if="!collapsed" class="settings-btn-label">更多</span>
        </button>
        <div class="settings-flyout" aria-label="更多二级菜单">
          <!-- 分组各占一列：19 项铺成一条竖列时必然要滚动，四列后最高一列 8 项、
               约 310px，桌面任意高度都能一眼看全。 -->
          <div
            v-for="section in moreSections"
            :key="section.label"
            class="settings-column"
          >
            <span class="subnav-title">{{ section.label }}</span>
            <button
              v-for="link in section.items"
              :key="link.path"
              type="button"
              class="subnav-item"
              :class="{ active: isActive(link.path) }"
              @click="navigate(link.path)"
            >
              <i :class="[link.icon, 'subnav-icon']" />
              <span>{{ link.label }}</span>
            </button>
          </div>
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
  /* 四列按内容自适应，再窄也保留 176px；1024 以下侧栏整体切移动端，不会用到这里 */
  width: min(800px, calc(100vw - 260px));
  max-height: calc(100vh - 120px);
  overflow-y: auto;
  padding: 8px;
  border: 1px solid var(--solo-border);
  border-radius: 8px;
  background: var(--solo-bg-sidebar);
  box-shadow: var(--solo-shadow);
  flex-direction: row;
  gap: 0;
}
/* 分组一列；列间用竖线分隔，组标题留在列首，比横线更易扫读 */
.settings-column {
  display: flex;
  flex: 1 1 176px;
  flex-direction: column;
  min-width: 0;
  padding: 0 10px;
  gap: 2px;
}
.settings-column + .settings-column {
  border-left: 1px solid var(--solo-border-light);
}
.settings-column:first-child {
  padding-left: 4px;
}
.settings-column:last-child {
  padding-right: 4px;
}
/* 导航里的分组标题仍用横线分隔；「更多」已按列分组，不再画横线 */
.subnav .subnav-title:not(:first-child) {
  margin-top: 6px;
  padding-top: 9px;
  border-top: 1px solid var(--solo-border-light);
}
.settings-anchor.open > .settings-flyout {
  display: flex;
  /* 从按钮方向轻轻弹出：只做入场，不做退场，Esc/点外收起要立即消失 */
  transform-origin: bottom left;
  animation: settings-flyout-in 160ms cubic-bezier(0.2, 0, 0, 1);
}
@keyframes settings-flyout-in {
  from {
    opacity: 0;
    transform: translateY(6px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}
@media (prefers-reduced-motion: reduce) {
  .settings-anchor.open > .settings-flyout {
    animation: none;
  }
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
/* 月度结账专属 Hero 视觉特权样式 */
.nav-item--hero {
  background: var(--solo-bg-blue-soft, rgba(14, 165, 233, 0.06));
  border: 1px solid rgba(14, 165, 233, 0.16);
  font-weight: 600;
}
.nav-item--hero .nav-icon {
  color: var(--solo-primary, #0284c7);
}
.nav-item--hero:hover {
  background: rgba(14, 165, 233, 0.12);
  border-color: rgba(14, 165, 233, 0.3);
}
.nav-item--hero.active {
  background: var(--solo-primary);
  color: #fff;
  border-color: var(--solo-primary);
}
.nav-item--hero.active .nav-icon,
.nav-item--hero.active .nav-label {
  color: #fff;
}
.nav-item--hero.active .closing-badge {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
  border-color: rgba(255, 255, 255, 0.35);
}

.closing-badge {
  display: inline-flex;
  align-items: center;
  padding: 1px 7px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 9999px;
  line-height: 1.4;
  white-space: nowrap;
  letter-spacing: 0.2px;
  transition: all 0.2s ease;
}
.closing-badge.warning {
  color: var(--solo-warning);
  background: #fef3c7;
  border: 1px solid #fde68a;
}
.closing-badge.success {
  color: var(--solo-income);
  background: #d1fae5;
  border: 1px solid #a7f3d0;
}

.closing-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  border: 1.5px solid var(--solo-bg-sidebar, #fff);
}
.closing-dot.warning {
  background-color: #f59e0b;
}
.closing-dot.success {
  background-color: #10b981;
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
