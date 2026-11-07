# syntax=docker/dockerfile:1

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app/backend

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements-docker.txt ./

# CPU 版 torch（避免把 2GB+ 的 CUDA 依赖打进镜像）
RUN pip install --upgrade pip \
    && pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 \
    && pip install -r requirements-docker.txt

COPY backend/ ./
COPY rules/ /app/rules/

EXPOSE 8000

ENTRYPOINT ["python", "scripts/docker_entrypoint.py"]
# P1-2：信任 compose 内网反代头——nginx 已传 X-Forwarded-For，开启后 uvicorn 会把
# request.client 替换为真实来源 IP，匿名限流不再全体共用 nginx 容器 IP。
# "*" 在 appnet 内可接受：8000 端口只有 compose 网络成员可达。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
     "--proxy-headers", "--forwarded-allow-ips", "*"]
