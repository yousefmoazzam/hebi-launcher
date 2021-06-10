<template>
  <div id="root">
    <h1 class="flex justify-center text-4xl">Login with CAS</h1>
    <div class="flex justify-center">
      <button v-if="!inProcessOfAuthenticating && !isAuthenticated && !isUnauthorisedUser"
        class="rounded bg-blue-400 hover:bg-blue-700 text-white m-1 p-1"
        v-on:click="loginButtonClickListener">
        Login
      </button>
    </div>
    <p v-if="inProcessOfAuthenticating && !isAuthenticated && !isUnauthorisedUser"
      class="flex justify-center">Authenticating, please wait...</p>
    <div v-if="isAuthenticated">
      <p class="flex justify-center">
        {{ 'Authenticated, welcome ' + this.authenticatedUserName + '!' }}
      </p>
      <p class="flex justify-center">
        You will shortly be redirected to the launcher
      </p>
    </div>
  </div>
</template>

<script>
var qs = require('qs');

export default {
  mounted: function () {
    this.location = window.location.href

    this.checkForCookie()
      .then(resp => {
        if (!resp) {
          // no cookie is present, so check if there is a ticket in the URL
          var hasTicketInUrl = this.checkUrlForTicket()

          if (hasTicketInUrl) {
            var ticket = qs.parse(this.location)[this.service + '?ticket'];
            this.validateTicket(ticket)
          } else {
            // no cookie in the browser, nor a ticket in the  url, so the user
            // needs to log in
            console.log('need regular log in')
          }

        } else {
          // cookie is present, so having a ticket in the url doesn't make a
          // difference:
          // - having a ticket when already authenticated doesn't require
          //   re-authentication
          // - not having ticket when already authenticated isn't a problem
          //   either
          // so can just redirect to launcher
          this.isAuthenticated = true
          this.authenticatedUserName = resp.username
          window.location.href = this.homepage
        }
      })
  },

  data: function () {
    return {
      casServer: 'https://auth.diamond.ac.uk/cas',
      service: 'https://hebi.diamond.ac.uk/launcher/',
      inProcessOfAuthenticating: false,
      isAuthenticated: false,
      location: '',
      authenticatedUserName: '',
      unauthorisedUserDesc: 'unauthorised user access',
      isUnauthorisedUser: false,
      unauthorisedUser: '',
      homepage: 'index.html'
    }
  },

  computed: {
    casLoginUrl: function () {
      return this.casServer + '/login?service=' +
        encodeURIComponent(this.service)
    }
  },

  methods: {
    
    loginButtonClickListener: function () {
      // redirect to the URL that will grab a ticket from the CAS server
      window.location.href = this.casLoginUrl
    },

    checkForCookie: function () {
      return fetch('auth')
        .then(resp => {
          // check if it's successful or gets a 403 error
          if (resp.status === 403) {
            return false
          } else {
            return resp.json()
          }
        })
    },

    checkUrlForTicket: function () {
      var parsedUrlObj = qs.parse(this.location)
      var hasTicketInUrl = parsedUrlObj.hasOwnProperty(this.service + '?ticket')
      return hasTicketInUrl 
    },

    validateTicket: function (ticket) {
      this.inProcessOfAuthenticating = true

      fetch('auth/validate_ticket?ticket=' + ticket)
        .then(resp => {
          return resp.json()
        })
        .then(authResp => {
          if (authResp.validated) {
            this.isAuthenticated = true
            this.authenticatedUserName = authResp.user
            setTimeout(() => {
              // redirect to the launcher page
              window.location.href = this.homepage
            }, 1000)
          } else {
            // check why ticket validation failed, and act accordingly
            this.checkFailedTicketValidation(authResp)
          }
        })
    },

    checkFailedTicketValidation: function (authResp) {
      if ('code' in authResp && authResp.code === 'INVALID_TICKET') {
        // the ticket has expired, so redirect to get a new ticket
        window.location.href = this.casLoginUrl
      } else if (authResp.desc === this.unauthorisedUserDesc) {
        // a user that doesn't have authorisation from the CAS server has been
        // denied access to the launcher
        // NOTE: the user should be given feedback if this occurs, something to
        // add in the future
        this.isUnauthorisedUser = true
        this.unauthorisedUser = authResp.unauthorised_user
      }
    }

  }
}
</script>
