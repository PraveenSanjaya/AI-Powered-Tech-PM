FROM node:22-alpine AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ .
# The API origin is baked into the rewrite rules at build time (internal docker network name).
ARG API_INTERNAL_URL=http://api:8000
ENV API_INTERNAL_URL=$API_INTERNAL_URL
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app /app
RUN addgroup -S app && adduser -S app -G app && chown -R app:app /app/.next
USER app
EXPOSE 3000
CMD ["npm", "run", "start"]
