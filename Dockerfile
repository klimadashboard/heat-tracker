FROM node:22 AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# Prune dev dependencies for smaller production image
RUN npm prune --production

# --- Production image ---
FROM node:22-slim

WORKDIR /app

COPY --from=build /app/build ./build
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./

# Population grid goes to a staging location so it can be copied into
# the persistent volume on first boot (the volume mounts over /app/data).
COPY --from=build /app/data/population-grid.json /app/data-seed/population-grid.json

ENV NODE_ENV=production
ENV PORT=3000
EXPOSE 3000

# On start: ensure the population grid exists in /app/data (which is the
# persistent volume). If the volume is fresh/empty, copy from the seed.
CMD ["sh", "-c", "mkdir -p /app/data && [ -f /app/data/population-grid.json ] || cp /app/data-seed/population-grid.json /app/data/population-grid.json && node build"]
