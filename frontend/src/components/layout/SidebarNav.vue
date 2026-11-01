<script setup lang="ts">
/**
 * 左侧导航轨 · 对应原型 `.rail`
 *
 * 结构：图标在上、标签在下的 58×54 方块（选中为金砖 + 4px 实心投影），
 * 底部是 58×30 的等宽小字按钮与 Core 连接块。
 * 与原型一致：品牌放在 HUD，不在侧栏重复一遍。
 */
import {
  ChevronsLeft,
  ChevronsRight,
  Frame,
  History,
  House,
  Images,
  LogOut,
  Settings,
  UserRound,
} from 'lucide-vue-next'
import { type FunctionalComponent } from 'vue'

defineProps<{ routeName: string; activeDomainId: string; taskCount: number; collapsed: boolean; hasAccount?: boolean; online: boolean }>()
const emit = defineEmits<{ navigate: [path: string]; toggle: []; logout: []; history: [] }>()

/* 「工作区」就是漫剧制作（design-spec §1 收敛后唯一创作的领域）。
   此前它依赖一个通用「与 Kantoku 协作」工作区，并另设「创作域」分组重复指向同一个漫剧入口；
   现在两者合并为一个条目：点击直接落到漫剧制作的工作台首页，不再经过领域选择层。 */
const navItems: { name: string; label: string; path: string; icon: FunctionalComponent; match: string[] }[] = [
  { name: 'home', label: '首页', path: '/', icon: House, match: ['home'] },
  { name: 'workspace', label: '工作区', path: '/workspace/comic', icon: Frame, match: ['workspace', 'workspace_run'] },
  { name: 'assets', label: '资产', path: '/assets', icon: Images, match: ['assets'] },
]
</script>

<template>
  <aside class="app-sidebar px-rail" :class="{ collapsed }" aria-label="全局导航">
    <nav class="px-rail__nav" aria-label="产品导航">
      <button
        v-for="item in navItems"
        :key="item.name"
        type="button"
        class="px-rail__nav-item"
        :class="{ active: item.match.includes(routeName) }"
        :title="item.label"
        :aria-current="item.match.includes(routeName) ? 'page' : undefined"
        @click="emit('navigate', item.path)"
      >
        <i><component :is="item.icon" :size="17" :stroke-width="2.2" /></i>
        <span>{{ item.label }}</span>
        <em v-if="item.name === 'runs' && taskCount">{{ taskCount }}</em>
      </button>

      <!-- 「历史」不是一条路由，而是一个动作：打开作品与历史对话抽屉。
           它必须常驻在左栏——此前只藏在工作区顶栏一个素色小按钮里，
           用户根本找不到回上一个作品的入口。 -->
      <button type="button" class="px-rail__nav-item" title="作品与历史对话" @click="emit('history')">
        <i><History :size="17" :stroke-width="2.2" /></i>
        <span>历史</span>
      </button>
    </nav>

    <span class="px-rail__gap" />

    <button v-if="hasAccount" type="button" class="px-rail__mini" title="个人中心" @click="emit('navigate', '/account')">个人中心</button>
    <button type="button" class="px-rail__mini" title="设置" @click="emit('navigate', '/settings')">设置</button>
    <button v-if="hasAccount" type="button" class="px-rail__mini" title="退出登录" @click="emit('logout')">退出登录</button>

    <div class="px-rail__health" :title="online ? 'Kantoku Core · 本机已连接' : 'Kantoku Core · 连接中断'"><i :class="{ 'is-down': !online }" />Core<br />{{ online ? '已连接' : '连接中断' }}</div>

    <button type="button" class="px-rail__toggle" :title="collapsed ? '展开侧栏' : '折叠侧栏'" :aria-expanded="!collapsed" @click="emit('toggle')">
      <ChevronsRight v-if="collapsed" :size="14" />
      <ChevronsLeft v-else :size="14" />
    </button>
  </aside>
</template>
