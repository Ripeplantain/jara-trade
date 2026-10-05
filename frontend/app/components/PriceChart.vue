<script setup lang="ts">
import { areaPath, linePath, makeScale, nearestIndex, niceTicks, timeTicks } from '~/lib/chart'
import { formatPercent, formatPrice, formatSignedPrice, formatTime, trend } from '~/lib/format'

/**
 * Live line chart for the selected ticker. The series comes from the shared
 * price store: history is fetched once per ticker there and every SSE message
 * appends to it, so this component never fetches.
 */
const props = defineProps<{ ticker: string | null }>()

const { prices, histories } = usePrices()

const HEIGHT = 300
const PAD = { top: 12, right: 58, bottom: 22, left: 8 }

const frame = ref<HTMLElement | null>(null)
const width = ref(640)
let observer: ResizeObserver | null = null
onMounted(() => {
  if (!frame.value) return
  observer = new ResizeObserver(([entry]) => {
    if (entry) width.value = Math.max(Math.floor(entry.contentRect.width), 200)
  })
  observer.observe(frame.value)
})
onBeforeUnmount(() => observer?.disconnect())

const points = computed(() => (props.ticker ? (histories.value[props.ticker] ?? []) : []))
const update = computed(() => (props.ticker ? prices.value[props.ticker] : undefined))

const geometry = computed(() => {
  const pts = points.value
  const scale = makeScale(pts, { width: width.value, height: HEIGHT, ...PAD })
  if (!scale || pts.length < 2) return null
  const last = pts[pts.length - 1]!
  const narrow = width.value < 480
  return {
    scale,
    line: linePath(pts, scale),
    area: areaPath(pts, scale, HEIGHT - PAD.bottom),
    last: { x: scale.x(last.timestamp), y: scale.y(last.price), price: last.price },
    // A tick label that would sit under the latest-price tag is left out.
    yTicks: niceTicks(scale.minPrice, scale.maxPrice, 4).map((value) => ({
      value,
      y: scale.y(value),
      labelled: Math.abs(scale.y(value) - scale.y(last.price)) > 12,
    })),
    xTicks: timeTicks(scale.minTime, scale.maxTime, narrow ? 3 : 6).map((value) => ({
      value,
      x: scale.x(value),
      label: formatTime(value, scale.maxTime - scale.minTime < 600),
    })),
  }
})

/** Change across the visible window, which also sets the line colour. */
const windowChange = computed(() => {
  const pts = points.value
  const first = pts[0]
  const last = pts[pts.length - 1]
  if (!first || !last) return null
  const change = last.price - first.price
  return { change, percent: first.price ? (change / first.price) * 100 : 0 }
})
const TONE = { up: 'text-up', down: 'text-down', flat: 'text-muted' } as const
const lineTone = computed(() => TONE[trend(windowChange.value?.change)])
const dayTone = computed(() => TONE[trend(update.value?.day_change)])

const hoverIndex = ref<number | null>(null)
function onPointerMove(event: PointerEvent) {
  const g = geometry.value
  const el = frame.value
  if (!g || !el) return
  const x = event.clientX - el.getBoundingClientRect().left
  const innerW = width.value - PAD.left - PAD.right
  const ratio = Math.min(Math.max((x - PAD.left) / innerW, 0), 1)
  hoverIndex.value = nearestIndex(points.value, g.scale.minTime + ratio * (g.scale.maxTime - g.scale.minTime))
}
const hover = computed(() => {
  const g = geometry.value
  const point = hoverIndex.value === null ? undefined : points.value[hoverIndex.value]
  if (!g || !point) return null
  return { point, x: g.scale.x(point.timestamp), y: g.scale.y(point.price) }
})
watch(() => props.ticker, () => (hoverIndex.value = null))

const gradientId = useId()
</script>

<template>
  <UiPanel title="Chart">
    <template #actions>
      <span v-if="hover" class="num text-xs text-muted">
        {{ formatTime(hover.point.timestamp) }}
        <span class="ml-1 font-medium text-fg">{{ formatPrice(hover.point.price) }}</span>
      </span>
      <span v-else-if="windowChange && geometry" class="num text-xs" :class="lineTone">
        <span class="text-faint">Window</span>
        {{ formatSignedPrice(windowChange.change) }} ({{ formatPercent(windowChange.percent) }})
      </span>
    </template>

    <div v-if="ticker" class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 pt-2.5">
      <span class="text-base font-semibold">{{ ticker }}</span>
      <span class="num text-xl font-semibold">{{ formatPrice(update?.price) }}</span>
      <span class="num text-xs font-medium" :class="dayTone">
        {{ formatSignedPrice(update?.day_change) }} ({{ formatPercent(update?.day_change_percent) }})
        <span class="font-normal text-faint">today</span>
      </span>
    </div>

    <div ref="frame" class="relative w-full" :style="{ height: `${HEIGHT}px` }">
      <svg
        v-if="geometry"
        :width="width"
        :height="HEIGHT"
        :viewBox="`0 0 ${width} ${HEIGHT}`"
        class="block touch-pan-y select-none"
        :class="lineTone"
        role="img"
        :aria-label="`${ticker} price over the last hour, latest ${formatPrice(geometry.last.price)}`"
        @pointermove="onPointerMove"
        @pointerleave="hoverIndex = null"
      >
        <defs>
          <linearGradient :id="gradientId" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="currentColor" stop-opacity="0.22" />
            <stop offset="100%" stop-color="currentColor" stop-opacity="0" />
          </linearGradient>
        </defs>

        <g class="num text-faint" font-size="10">
          <g v-for="tick in geometry.yTicks" :key="`y${tick.value}`">
            <line :x1="PAD.left" :x2="width - PAD.right" :y1="tick.y" :y2="tick.y" stroke="var(--line)" stroke-width="1" />
            <text v-if="tick.labelled" :x="width - PAD.right + 6" :y="tick.y + 3" fill="currentColor">{{ formatPrice(tick.value) }}</text>
          </g>
          <text
            v-for="tick in geometry.xTicks"
            :key="`x${tick.value}`"
            :x="tick.x"
            :y="HEIGHT - 6"
            text-anchor="middle"
            fill="currentColor"
          >
            {{ tick.label }}
          </text>
        </g>

        <path :d="geometry.area" :fill="`url(#${gradientId})`" />
        <path :d="geometry.line" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" />

        <!-- Latest price: marker on the line and a tag on the price axis. -->
        <line
          :x1="PAD.left"
          :x2="width - PAD.right"
          :y1="geometry.last.y"
          :y2="geometry.last.y"
          stroke="currentColor"
          stroke-width="1"
          stroke-dasharray="2 3"
          opacity="0.6"
        />
        <circle :cx="geometry.last.x" :cy="geometry.last.y" r="3" fill="currentColor" stroke="var(--panel)" stroke-width="1.5" />
        <g :transform="`translate(${width - PAD.right + 2}, ${geometry.last.y - 8})`">
          <rect width="54" height="16" rx="2" fill="currentColor" />
          <text x="4" y="11.5" font-size="10" font-weight="600" fill="var(--panel)" class="num">
            {{ formatPrice(geometry.last.price) }}
          </text>
        </g>

        <g v-if="hover" pointer-events="none">
          <line :x1="hover.x" :x2="hover.x" :y1="PAD.top" :y2="HEIGHT - PAD.bottom" stroke="var(--muted)" stroke-width="1" stroke-dasharray="3 3" />
          <circle :cx="hover.x" :cy="hover.y" r="3.5" fill="var(--panel)" stroke="var(--fg)" stroke-width="1.5" />
        </g>
      </svg>
      <PanelNotice
        v-else
        class="absolute inset-0"
        :title="ticker ? `Waiting for ${ticker} prices…` : 'Select a ticker'"
        :detail="ticker ? 'The chart fills in as prices stream.' : 'Pick a row in the watchlist to chart it.'"
      />
    </div>
  </UiPanel>
</template>
