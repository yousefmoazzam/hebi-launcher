import Vue from 'vue'

import LauncherPage from './components/LauncherPage.vue'

import './assets/styles/index.css' // tailwind

new Vue({
  el: '#vapp',
  render: h => h(LauncherPage)
})
