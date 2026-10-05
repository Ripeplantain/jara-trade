# syntax=docker/dockerfile:1
# Build context is the repo root: the pnpm workspace and lockfile live there.
FROM node:24-slim AS build
RUN npm install -g pnpm@10.11.0
WORKDIR /repo
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json frontend/
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile --ignore-scripts
COPY frontend frontend
# The /api proxy target is compiled into the server, so it is a build argument.
ARG NUXT_BACKEND_URL=http://backend:8000
ENV NUXT_BACKEND_URL=$NUXT_BACKEND_URL
RUN pnpm rebuild && pnpm --filter jara-trade-frontend build

FROM node:24-slim
ENV NODE_ENV=production HOST=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=build --chown=node:node /repo/frontend/.output ./.output
USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"
CMD ["node", ".output/server/index.mjs"]
