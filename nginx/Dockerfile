FROM nginxinc/nginx-unprivileged:1.20-alpine

# for installing node
USER root

RUN cd / && \
    apk add --no-cache libstdc++ && \
    wget https://unofficial-builds.nodejs.org/download/release/v12.8.1/node-v12.8.1-linux-x64-musl.tar.xz && \
    apk add --no-cache tar && \
    apk add --no-cache xz && \
    tar xf node-v12.8.1-linux-x64-musl.tar.xz && \
    rm node-v12.8.1-linux-x64-musl.tar.xz

ENV PATH="${PATH}:/node-v12.8.1-linux-x64-musl/bin"

WORKDIR /var/cache/nginx/
COPY ./package*.json ./
RUN npm install

COPY ./nginx-confs/nginx-k8s.conf /etc/nginx/conf.d/default.conf
COPY ./index.html .
COPY ./login.html .
COPY ./webpack.config.js .
COPY ./postcss.config.js .
COPY ./src ./src

RUN npm run build

# switch back to unprivileged user
USER nginx

CMD ["nginx", "-g", "'daemon off;'"]
