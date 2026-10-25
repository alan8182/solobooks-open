# ---- Stage 1: Build Vue frontend ----
FROM node:20-alpine AS build

WORKDIR /app

ARG VITE_API_BASE=
ENV VITE_API_BASE=$VITE_API_BASE

# 默认 npmmirror：服务器在国内，npmjs.org 直连极慢。海外构建可 --build-arg 覆盖。
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm config set registry $NPM_REGISTRY
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# ---- Stage 2: nginx production server ----
FROM nginx:alpine

RUN rm -f /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
