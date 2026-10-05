<script setup lang="ts">
/** Centred empty / loading / unavailable message inside a panel. */
defineProps<{ title: string; detail?: string; tone?: 'muted' | 'warn' }>()
</script>

<template>
  <div class="flex flex-1 flex-col items-center justify-center gap-1 px-4 py-8 text-center">
    <p class="font-medium" :class="tone === 'warn' ? 'text-warn' : 'text-muted'">{{ title }}</p>
    <p v-if="detail" class="max-w-xs text-xs text-faint">{{ detail }}</p>
  </div>
</template>
