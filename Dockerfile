FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --shell /bin/bash appuser

COPY --chown=appuser:appuser app.py .
COPY --chown=appuser:appuser demo ./demo
COPY --chown=appuser:appuser data ./data

# 缓存与配额计数会写入 /app/data；显式建目录并收紧权限为 0750（仅属主可写），
# 以非 root 用户运行，避免容器内进程以 root 身份落盘。
RUN mkdir -p /app/data && chown -R appuser:appuser /app/data && chmod 0750 /app/data

USER appuser

ENV PORT=5000
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"5000\")}/healthz', timeout=3)"

# 必须保持 -w 1：进程内锁（刷新状态、生成锁、配额计数）在单进程多线程模型下才有效。
# exec 不可省：让 gunicorn 顶替 sh 成为 PID 1，滚动发布发 SIGTERM 时才能收到并优雅停机。
CMD ["sh", "-c", "exec gunicorn -w 1 --threads 4 -b 0.0.0.0:${PORT:-5000} --timeout 60 --graceful-timeout 30 app:app"]
