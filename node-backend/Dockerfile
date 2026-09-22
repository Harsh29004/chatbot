# The Node API: accounts, billing, credits, admin, support, assistant.
#
# Two stages so the runtime image carries no TypeScript compiler and no dev
# dependencies — the build tooling is several times the size of what it emits.

FROM node:20-slim AS build

WORKDIR /build

# Copied before the source so this layer is cached against package.json rather
# than against every edit to a route handler.
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json ./
COPY src/ ./src/
RUN npm run build

# Drop to production dependencies only, in place, so the next stage can copy
# node_modules across without re-resolving anything.
RUN npm prune --omit=dev

# ---------------------------------------------------------------------------

FROM node:20-slim

WORKDIR /app
ENV NODE_ENV=production

COPY --from=build /build/node_modules ./node_modules
COPY --from=build /build/dist ./dist
COPY package.json ./

# Not root. The image has no reason to write anywhere, and this is the service
# facing the internet.
USER node

EXPOSE 8000

# `node dist/server.js` rather than `npm start`: npm sits between the signal
# and the process, so a SIGTERM from compose reaches the wrapper and the server
# is killed rather than shut down. server.ts handles SIGTERM itself — closing
# the listener, stopping the sweep, closing the Mongo client — and that only
# happens if the signal actually arrives.
CMD ["node", "dist/server.js"]
