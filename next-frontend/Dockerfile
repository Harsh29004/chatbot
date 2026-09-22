# The Next.js web app.
#
# Server-rendered, so this ships a running Node server rather than a folder of
# static files. That is what makes the landing page arrive with prices in it and
# lets `/dashboard` redirect a signed-out visitor before any markup is sent.

FROM node:20-slim AS build

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci

# NEXT_PUBLIC_* is inlined into the browser bundle at BUILD time, not read at
# run time. Putting these under `environment:` in compose would set them long
# after the bundle was compiled, and the app would ship with sign-in silently
# disabled and nothing in any log to say why.
#
# These are public values that end up in the JavaScript every visitor
# downloads. The Firebase service account private key is not among them and
# never should be — that is read at run time by the Node API, from its own
# environment.
ARG NEXT_PUBLIC_FIREBASE_API_KEY=""
ARG NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=""
ARG NEXT_PUBLIC_FIREBASE_PROJECT_ID=""
ARG NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=""
ARG NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=""
ARG NEXT_PUBLIC_FIREBASE_APP_ID=""
ARG NEXT_PUBLIC_FIREBASE_MEASUREMENT_ID=""
ARG NEXT_PUBLIC_PUBLIC_API_ORIGIN=""
ARG NEXT_PUBLIC_APP_RELEASE="docker"

ENV NEXT_PUBLIC_FIREBASE_API_KEY=$NEXT_PUBLIC_FIREBASE_API_KEY \
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=$NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN \
    NEXT_PUBLIC_FIREBASE_PROJECT_ID=$NEXT_PUBLIC_FIREBASE_PROJECT_ID \
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=$NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET \
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=$NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID \
    NEXT_PUBLIC_FIREBASE_APP_ID=$NEXT_PUBLIC_FIREBASE_APP_ID \
    NEXT_PUBLIC_FIREBASE_MEASUREMENT_ID=$NEXT_PUBLIC_FIREBASE_MEASUREMENT_ID \
    NEXT_PUBLIC_PUBLIC_API_ORIGIN=$NEXT_PUBLIC_PUBLIC_API_ORIGIN \
    NEXT_PUBLIC_APP_RELEASE=$NEXT_PUBLIC_APP_RELEASE \
    NEXT_TELEMETRY_DISABLED=1

COPY next.config.mjs tsconfig.json ./
COPY app/ ./app/
COPY components/ ./components/
COPY lib/ ./lib/
COPY screens/ ./screens/
COPY three/ ./three/
COPY public/ ./public/

RUN npm run build
RUN npm prune --omit=dev

# ---------------------------------------------------------------------------

FROM node:20-slim

WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=build /build/node_modules ./node_modules
COPY --from=build /build/.next ./.next
COPY --from=build /build/public ./public
COPY --from=build /build/next.config.mjs ./
COPY package.json ./

USER node

EXPOSE 3000

# API_ORIGIN and INTERNAL_API_ORIGIN are read at RUN time — the first by the
# rewrite that proxies /api for the browser, the second by Server Components
# calling the API directly. Both are set in compose.
CMD ["npx", "next", "start", "-p", "3000"]
