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
# 预渲染新鲜度门禁：产物引用的资产与本次构建对不上直接失败（见 web/scripts/check-prerender.mjs）
RUN node scripts/check-prerender.mjs

# ---- Stage 2: nginx production server ----
FROM nginx:alpine

RUN rm -f /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html
# 预渲染静态 HTML 覆盖同名文件（index.html 等）并补齐目录式路由（/ai-connect/index.html …）
COPY web/prerender/ /usr/share/nginx/html/
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
