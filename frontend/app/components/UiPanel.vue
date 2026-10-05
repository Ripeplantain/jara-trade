<script setup lang="ts">
defineProps<{ title: string }>()
</script>

<template>
  <section class="flex min-w-0 flex-col overflow-hidden rounded-md border border-line bg-panel">
    <header class="flex min-h-9 items-center justify-between gap-3 border-b border-line px-3 py-1.5">
      <h2 class="label shrink-0">{{ title }}</h2>
      <div class="flex min-w-0 items-center gap-2">
        <slot name="actions" />
      </div>
    </header>
    <div class="flex min-h-0 flex-1 flex-col">
      <slot />
    </div>
  </section>
</template>
