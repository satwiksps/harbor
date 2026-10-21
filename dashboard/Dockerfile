FROM node:24-alpine AS build
WORKDIR /app
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build && chmod -R a+rX /app/dist

FROM nginxinc/nginx-unprivileged:1.28-alpine
COPY --chmod=644 dashboard/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
