<template>
  <div class="prompt-modal-bg flex fixed inset-0 z-20 items-center justify-center">
    <div class="z-20 bg-gray-300 w-3/4 px-2 py-4 rounded-sm">
        <p class="text-center">
          {{ promptText }}
        </p>
        <div class="flex justify-center">
          <button type="button"
            class="bg-red-200 hover:bg-red-300 p-2 m-2 rounded"
            v-on:click="noResponseButtonClickListener">
            No
          </button>
          <button type="button"
            class="bg-green-200 hover:bg-green-300 p-2 m-2 rounded"
            v-on:click="yesResponseButtonClickListener">
            Yes
          </button>
        </div>
      </div>
  </div>
</template>

<script>
// NOTE: this component has been copied from Hebi; figuring out how to share
// Vue.js components across different apps should be done sometime in the
// future!

export default {
  props: {
    promptText: String
  },
  methods: {
    yesResponseButtonClickListener: function () {
      this.$emit('prompt-yes-response')
    },

    noResponseButtonClickListener: function () {
      this.$emit('prompt-no-response')
    }
  }
}
</script>

<style>
.prompt-modal-bg {
  // same as tailwind's bg-gray-800, but with a custom opacity
  background: rgba(31, 41, 55, 0.8);
}
</style>
