# 프론트는 정적 빌드만 만든다. 서빙은 nginx 컨테이너가 한다.
FROM node:22-alpine

WORKDIR /app

COPY package.json ./
RUN npm install

COPY tsconfig*.json vite.config.ts index.html ./
COPY src ./src

# docker-compose 가 `npm run build && cp -r dist/. /out/` 로 덮어쓴다.
CMD ["npm", "run", "build"]
