<script setup lang="ts">
import { linePath, makeScale } from '~/lib/chart'
import type { PricePoint } from '~/types/api'

const props = withDefaults(defineProps<{ points: PricePoint[]; width?: number; height?: number }>(), {
  width: 88,
  height: 24,
})

const path = computed(() => {
  if (props.points.length < 2) return ''
  const scale = makeScale(props.points, { width: props.width, height: props.height, top: 2, bottom: 2 })
  return scale ? linePath(props.points, scale) : ''
})

const tone = computed(() => {
  const first = props.points[0]
  const last = props.points[props.points.length - 1]
  if (!first || !last || last.price === first.price) return 'text-muted'
  return last.price > first.price ? 'text-up' : 'text-down'
})
</script>

<template>
  <svg
    :width="width"
    :height="height"
    :viewBox="`0 0 ${width} ${height}`"
    class="inline-block align-middle"
    :class="tone"
    aria-hidden="true"
  >
    <path v-if="path" :d="path" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linejoin="round" />
  </svg>
</template>
