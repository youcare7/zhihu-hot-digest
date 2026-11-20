FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY demo ./demo
COPY data ./data

# 缓存与配额计数会写入 /app/data；显式建目录并放宽写权限，
# 避免以后切换非 root 运行用户时因目录属主问题导致落盘失败。
RUN mkdir -p /app/data && chmod 0777 /app/data

ENV PORT=5000
EXPOSE 5000

# 必须保持 -w 1：进程内锁（刷新状态、生成锁、配额计数）在单进程多线程模型下才有效。
# exec 不可省：让 gunicorn 顶替 sh 成为 PID 1，滚动发布发 SIGTERM 时才能收到并优雅停机。
CMD ["sh", "-c", "exec gunicorn -w 1 --threads 4 -b 0.0.0.0:${PORT:-5000} --timeout 60 --graceful-timeout 30 app:app"]
