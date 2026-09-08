<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAV_ITEMS, type NavItem } from '@/utils/constants'

defineProps<{ collapsed: boolean }>()
const emit = defineEmits<{ toggle: [] }>()

const route = useRoute()
const router = useRouter()
const expandedMenus = ref<string[]>([])
const navGroups = computed(() => {
  return ['经营', '财务', '管理'].map((label) => ({
    label,
    items: NAV_ITEMS.filter((item) => item.group === label),
  }))
})

function isActive(path: string) {
  if (path === '/' || path === '/mine') return route.path === path
  return route.path.startsWith(path)
}

function isParentActive(item: NavItem) {
  return item.children?.some((child) => isActive(child.path)) ?? false
}

function isExpanded(label: string) {
  return expandedMenus.value.includes(label)
}

function toggleMenu(label: string) {
  expandedMenus.value = isExpanded(label)
    ? expandedMenus.value.filter((item) => item !== label)
    : [...expandedMenus.value, label]
}

function navigate(path: string) {
  router.push(path)
}

watch(
  () => route.path,
  () => {
    const activeParents = NAV_ITEMS
      .filter((item) => isParentActive(item))
      .map((item) => item.label)
    expandedMenus.value = Array.from(new Set([...expandedMenus.value, ...activeParents]))
  },
  { immediate: true },
)
</script>

<template>
  <aside class="sidebar" :class="{ collapsed }">
    <div class="sidebar-header">
      <div class="logo-area" @click="router.push('/')">
        <div class="logo-icon">
          <i class="ri-line-chart-line" />
        </div>
        <div v-if="!collapsed" class="logo-copy">
          <span class="logo-text">司理</span>
          <span class="logo-sub">SoloBooks</span>
        </div>
      </div>
      <button class="toggle-btn" @click="emit('toggle')" :title="collapsed ? '展开' : '收起'">
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
            :title="collapsed ? item.label : undefined"
            :aria-expanded="isExpanded(item.label)"
            @click="toggleMenu(item.label)"
          >
            <span class="nav-item-inner">
              <i :class="[item.icon, 'nav-icon']" />
              <span v-if="!collapsed" class="nav-label">{{ item.label }}</span>
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
            :title="collapsed ? item.label : undefined"
            @click="navigate(item.path)"
          >
            <span class="nav-item-inner">
              <i :class="[item.icon, 'nav-icon']" />
              <span v-if="!collapsed" class="nav-label">{{ item.label }}</span>
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

    <div v-if="!collapsed" class="sidebar-foot">
      <span class="foot-kicker">本月账务状态</span>
      <strong>记录完整</strong>
      <span>继续保持及时记录</span>
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
  border-radius: 10px;
  background: var(--solo-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 18px;
  box-shadow: none;
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
  border-radius: 7px;
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
  border-radius: 10px;
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
.sidebar-foot {
  margin: 0 14px 18px;
  padding: 14px;
  border: 1px solid var(--solo-border);
  border-radius: 12px;
  background: var(--solo-bg-soft);
  display: flex;
  flex-direction: column;
  gap: 3px;
  color: var(--solo-text-muted);
  font-size: 11px;
}
.sidebar-foot strong {
  color: var(--solo-text-primary);
  font-size: 13px;
}
.foot-kicker {
  color: var(--solo-primary);
  font-size: 10px;
  font-weight: 650;
}
@media (max-width: 1024px) {
  .sidebar,
  .sidebar.collapsed {
    display: none;
  }
}
</style>
