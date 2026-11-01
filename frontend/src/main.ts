import { createApp } from 'vue'

import AuthGate from './components/AuthGate.vue'
import { startTimeOfDay } from './components/layout/timeOfDay'
import './styles/tokens.css'
import './styles/globals.css'
import './style.css'
import './styles/pixel.css'
import './styles/pixel-pages.css'
/* 关卡元件层最后加载：它负责收敛前面几套皮肤的按钮、卡片、动效与交互态。 */
import './styles/mario.css'

/* 天空时段由本机时间决定；登录页（AuthGate 的另一支）也要跟着变，所以在挂载前启动。 */
startTimeOfDay()

createApp(AuthGate).mount('#app')
