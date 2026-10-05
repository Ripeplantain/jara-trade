import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { defineConfig } from 'vitest/config'

const app = fileURLToPath(new URL('./app', import.meta.url))

// No Nuxt runtime: lib tests are plain TS in node. Composable and component
// tests get Nuxt's auto-imports (Vue APIs, app/composables, app/components)
// from the two unplugins below, and opt in to a DOM per file with
// `// @vitest-environment happy-dom`.
export default defineConfig({
  plugins: [
    vue(),
    AutoImport({ imports: ['vue'], dirs: [`${app}/composables`], dts: false }),
    Components({ dirs: [`${app}/components`], dts: false }),
  ],
  define: {
    'import.meta.client': 'true',
  },
  resolve: {
    alias: {
      '~': app,
    },
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
})
