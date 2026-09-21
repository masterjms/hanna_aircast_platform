# 프론트는 정적 빌드만 만든다. 서빙은 nginx 컨테이너가 한다.
FROM node:22-alpine

WORKDIR /app

COPY package.json ./
RUN npm install

COPY tsconfig*.json vite.config.ts index.html ./
COPY src ./src
# public/ 은 vite 가 dist 로 그대로 옮기는 정적 파일(favicon 등)이다. 빠뜨리면 운영에서만
# /favicon.svg 가 없어 nginx 가 index.html 을 대신 돌려주고, 탭 아이콘이 안 뜬다(2026-09-21).
COPY public ./public

# docker-compose 가 `npm run build && cp -r dist/. /out/` 로 덮어쓴다.
CMD ["npm", "run", "build"]
