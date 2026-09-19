# 408 错题助手 —— 镜像构建
#
# 两阶段：
#   1) node 里把管理后台（Vue3 + Vite）构建成 admin/dist
#   2) python:3.12-slim 里装依赖跑后端（后端同时托管用户端 H5 与管理后台）
#
# 构建（默认走国内镜像源，网络好可去掉 --build-arg）：
#   docker build -t 408-wrong-cropper .
#   docker build -t 408-wrong-cropper --build-arg PIP_INDEX=https://pypi.org/simple .

# ---------------------------------------------------------------- 1) 管理后台
FROM node:20-alpine AS admin-build
WORKDIR /build
ARG NPM_REGISTRY=https://registry.npmmirror.com
COPY admin/package.json admin/package-lock.json* ./
RUN npm install --registry=${NPM_REGISTRY} --no-audit --no-fund
COPY admin/ ./
RUN npm run build

# ---------------------------------------------------------------- 2) 运行环境
FROM python:3.12-slim

# onnxruntime 要 libgomp1；opencv 的 so 里还链着 libGL/libxcb（即使是 headless 版，
# 少了会报 "libxcb.so.1: cannot open shared object file"，切题时才会炸）。
# apt 官方源在国内很慢（索引 9.7MB 拉了 166 秒），换国内镜像实测 41 秒（含装包）；
# 要用官方源：--build-arg APT_MIRROR=deb.debian.org
ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
RUN set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
        [ -f "$f" ] && sed -i "s|deb.debian.org|${APT_MIRROR}|g" "$f" || true; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 libgl1 libxcb1 tzdata; \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    ZC_DATA_DIR=/data \
    ZC_HOST=0.0.0.0 \
    ZC_PORT=18100

WORKDIR /app

ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
COPY requirements.txt ./
RUN pip install --no-cache-dir -i ${PIP_INDEX} --trusted-host pypi.tuna.tsinghua.edu.cn -r requirements.txt

# 只拷运行需要的：后端、用户端 H5、构建好的管理后台
# （backend/.env、data/、android/、tests/ 由 .dockerignore 排除，管理密钥走环境变量）
COPY backend/ ./backend/
COPY web/ ./web/
COPY --from=admin-build /build/dist ./admin/dist

# 非 root 跑；数据目录 /data 记得挂卷，否则重启就没了
# （/app 属于 root、zc 只读，所以应用只能写 /data，正好）
RUN useradd -m -u 10001 zc && mkdir -p /data && chown -R zc:zc /data
USER zc
WORKDIR /app

VOLUME ["/data"]
EXPOSE 18100

# 用 python 当探针，省掉 curl 依赖
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:18100/api/health', timeout=4).status==200 else 1)"

CMD ["python", "-m", "backend"]
