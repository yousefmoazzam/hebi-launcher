<template>
  <div id="root">
    <h1 class="flex justify-center text-4xl">Launcher</h1>
    <prompt-modal-box v-for="(modal, index) in promptModalBoxes"
      :promptText="modal.promptText" :key="index"
      v-on:prompt-yes-response="modal.yesResponseListener"
      v-on:prompt-no-response="modal.noResponseListener" />
    <div class="flex justify-center p-1">
      <p class="whitespace-pre">{{ 'User session status: ' }}</p>
      <p class="whitespace-pre">{{ userSessionStatus }}</p>
    </div>
    <div class="flex justify-center">
      <button class="rounded bg-blue-400 hover:bg-blue-700 text-white m-1 p-1 cursor-pointer"
        v-on:click="startNewSessionButtonClickListener">
        Start a new session
      </button>
      <button :class="[isSessionRunning ? 'cursor-pointer bg-green-400 hover:bg-green-700 text-white' : 'cursor-not-allowed bg-gray-200 text-gray-500', 'rounded  m-1 p-1']"
        :disabled="!isSessionRunning"
        v-on:click="continueSessionButtonClickListener">
        Continue session
      </button>
    </div>
    <div class="flex justify-center mt-1">
      <p class="">{{ additionalMessage }}</p>
      <svg v-if="isSessionLaunching"
        viewBox="0 0 24 24"
        class="animate-spin rounded-full h-5 w-5 border-b-2 border-gray-900 ml-2">
      </svg> 
    </div>
  </div>
</template>

<script>
// taken from Hebi
import PromptModalBox from './PromptModalBox.vue'

export default {
  mounted: function () {
    fetch('flask/k8s/session_info')
      .then(resp => {
        return resp.json()
      })
      .then(resp => {
        this.isSessionRunning = resp.is_session_currently_running
        this.user = resp.username
        this.fetchingUserSessionStatus = false
      })
  },

  components: {
    'prompt-modal-box': PromptModalBox
  },

  data: function () {
    return {
      hebiHost: 'https://hebi.diamond.ac.uk/',
      user: '',
      isSessionRunning: false,
      additionalMessage: '',
      fetchingUserSessionStatus: true,
      isSessionLaunching: false,
      promptModalBoxes: []
    }
  },

  computed: {
    hebiSessionUrl: function () {
      return this.hebiHost + this.user + '/'
    },

    userSessionStatus: function () {
      if (this.fetchingUserSessionStatus) {
        return 'fetching...'
      } else {
        if (this.isSessionRunning) {
          return this.user + ' has a Hebi session currently running'
        } else {
          return this.user + ' has no Hebi session running'
        }
      }
    }
  },

  methods: {
    ensureHebiSessionIsLive: function () {
      // start polling the user's Hebi Session to watch for when the Ingress'
      // nginx routing has taken effect
      return new Promise((resolve, reject) => {
        this.checkHebiUrlStatus(resolve)
      })
    },

    redirectToHebiSession: function () {
      // redirect to user's Hebi session
      this.isSessionLaunching = false
      this.isSessionRunning = true
      setTimeout(() => {
        window.location.href = this.hebiSessionUrl
      }, 2000)
    
    },

    checkHebiUrlStatus: function (resolve) {
      // function for polling the user's Hebi session
      fetch(this.hebiSessionUrl)
      .then(resp => {
        if (resp.status === 502 || resp.status === 404) {
          console.log('keep polling, bad gateway 502 or 404 still due to Ingress nginx config not taking effect yet')
          setTimeout(this.checkHebiUrlStatus.bind(this, resolve), 2000)
        } else {
          this.additionalMessage = 'Thank you for waiting ' + this.user +
            ', you will shortly be redirected to your Hebi session'
          resolve()
        }
      })
    },

    startNewSessionButtonClickListener: function () {
      if (this.isSessionRunning) {
        // prompt the user that starting a new session will first stop their
        // currently running one
        var promptText = 'Starting a new Hebi session will stop the current ' +
          'running one and create a new one, are you sure you would like to ' +
          'do this?'
        this.promptModalBoxes.push({
          promptText: promptText,
          yesResponseListener: this.startNewSessionYesResponse,
          noResponseListener: this.startNewSessionNoResponse
        })
      } else {
        // no session currently exists, so can go ahead with starting a new one
        this.startSession()
      }
    },

    startNewSessionYesResponse: function () {
      this.promptModalBoxes.pop()
      // stop the old session first
      this.additionalMessage = 'Stopping the old Hebi session...'
      fetch('flask/k8s/stop_hebi')
        .then(resp => {
          return resp.json()
        })
        .then(resp => {
          if (resp.was_session_stopped) {
            // stopping the old session was successful, can start the new
            // session
            this.startSession()
          } else {
            // session failed to be stopped for some reaosn, an error in k8s
            // maybe, shoudl handle this in some manner
            console.log('An error occurred when starting a new session, when in the middle of stopping an old session')
            this.additionalMessage = ''
          }
        })
    },

    startNewSessionNoResponse: function () {
      this.promptModalBoxes.pop()
    },

    startSession: function () {
      this.additionalMessage = 'Starting a new Hebi session, please wait'
      this.isSessionLaunching = true
      fetch('flask/k8s/start_hebi')
        .then(resp => {
          return resp.json()
        })
        .then(resp => {
          if (resp.was_session_launched) {
            // the session was launched and the Pod is running, but the Ingress
            // nginx config hasn't necessarily taken effect yet, it usually
            // takes 5 - 10 seconds after the Ingress config has been set for
            // the routing for the user's Hebi session to start working;
            // watching the Ingress via the k8s API doesn't seem to offer
            // anything to check if the nginx config has started to work, so
            // just poll the URL of the user's Hebi session until it doesn't
            // give a HTTP error anymore! Polling via promises was taken from
            // the following thread:
            // https://stackoverflow.com/questions/30505960/use-promise-to-wait-until-polled-condition-is-satisfied
            this.user = resp.username

            this.ensureHebiSessionIsLive()
              .then(() => {
                this.redirectToHebiSession()
              })

          } else {
            // catch and feedback to the UI potential failures in launching a
            // session
          }
        })
    },

    continueSessionButtonClickListener: function () {
      this.additionalMessage = 'Redirecting to existing session...'
      this.redirectToHebiSession()      
    }
  }
}
</script>
