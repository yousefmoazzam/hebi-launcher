import Vue from 'vue'

import LoginPage from './components/LoginPage.vue'

import './assets/styles/index.css' // tailwind

new Vue({
  el: '#vapp',
  render: h => h(LoginPage)
})
