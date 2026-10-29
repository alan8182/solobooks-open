<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<!-- 根路径登录态分流：匿名访客在 / 直接看到官网落地页（对外域名即官网地址），
     已登录用户透传给 AppLayout 工作台。token 读取与路由守卫同源（localStorage 直读），
     不用 auth store——它在整页刷新后到用户档案拉回前是空的，会造成登录用户闪现官网。 -->
<script setup lang="ts">
import { computed } from 'vue'
import { RouterView } from 'vue-router'
import Landing from '@/pages/landing/index.vue'

const isAuthenticated = computed(() => Boolean(localStorage.getItem('auth_token')))
</script>

<template>
  <Landing v-if="!isAuthenticated" />
  <RouterView v-else />
</template>
