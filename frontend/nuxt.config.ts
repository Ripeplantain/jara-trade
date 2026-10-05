import tailwindcss from '@tailwindcss/vite'

// Where the FastAPI backend listens. The Nuxt server proxies /api to it so the
// browser only ever talks to its own origin.
const backend = process.env.NUXT_BACKEND_URL || 'http://localhost:8000'

export default defineNuxtConfig({
  compatibilityDate: '2026-07-01',
  ssr: false,
  devtools: { enabled: false },
  css: ['~/assets/css/main.css'],
  vite: { plugins: [tailwindcss()] },

  runtimeConfig: {
    public: {
      // Empty = same-origin relative URLs (through the proxy below).
      // Set NUXT_PUBLIC_API_BASE=http://localhost:8000 to bypass the proxy.
      apiBase: '',
    },
  },

  routeRules: {
    '/api/**': { proxy: `${backend}/api/**` },
  },

  app: {
    head: {
      title: 'Jara Trade',
      htmlAttrs: { lang: 'en' },
      meta: [
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
        { name: 'description', content: 'Jara Trade: a simulated (paper) trading terminal.' },
      ],
      script: [
        {
          // Dark is the default; apply a stored light choice before first paint.
          // (Kept out of htmlAttrs so the head manager never resets it.)
          innerHTML:
            "try{var t=localStorage.getItem('jara-theme');if(t==='light')document.documentElement.setAttribute('data-theme',t)}catch(e){}",
        },
      ],
    },
  },
})
