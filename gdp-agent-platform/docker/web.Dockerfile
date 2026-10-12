# Web image (Next.js 14). Build context: gdp-agent-platform/.
# AIP_AUTH is passed at build time as well as at run time: the sign-in middleware is compiled during the build.
FROM node:20-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

COPY apps/web/ ./
ARG AIP_AUTH=pat
ARG NEXT_PUBLIC_BUILD_SHA=unknown
ARG NEXT_PUBLIC_CATALOG_DISPLAY=""
ENV AIP_AUTH=${AIP_AUTH} \
    NEXT_PUBLIC_BUILD_SHA=${NEXT_PUBLIC_BUILD_SHA} \
    NEXT_PUBLIC_CATALOG_DISPLAY=${NEXT_PUBLIC_CATALOG_DISPLAY}
RUN npm run build

FROM node:20-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=build /app/package.json /app/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/next.config.js ./next.config.js
COPY --from=build /app/.next ./.next

ARG AIP_AUTH=pat
ENV AIP_AUTH=${AIP_AUTH}

USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/bff/version').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["npx", "next", "start", "-p", "3000"]
