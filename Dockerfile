# Lead Radar PRO - Telegram Bot + Telethon Parser
# IMPORTANT: This image runs ONLY the Telegram bot and parser.
# DO NOT override with app.web.main, uvicorn, or any web server.
# Correct startup: python -m app.manage bot
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
RUN chmod +x /app/docker-entrypoint.sh
ENV PYTHONUNBUFFERED=1
LABEL description="Lead Radar PRO Bot + Parser"
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "-m", "app.manage", "bot"]
