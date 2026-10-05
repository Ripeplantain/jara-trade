import { createApi, type Api } from '~/lib/api'

let api: Api | null = null

/** The shared API client, rooted at `runtimeConfig.public.apiBase` (empty = same origin). */
export function useApi(): Api {
  api ??= createApi(String(useRuntimeConfig().public.apiBase ?? ''))
  return api
}
