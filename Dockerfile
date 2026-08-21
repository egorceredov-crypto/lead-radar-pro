FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
RUN chmod +x /app/docker-entrypoint.sh
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "-m", "app.manage", "bot"]
