#!/bin/sh
echo "Docker entrypoint started"
echo "Command: $@"
echo "Working directory: $(pwd)"

# Start minimal health check server on port 80 in background
python -c "
import http.server
import socketserver
import os

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, format, *args):
        pass

PORT = 80
with socketserver.TCPServer(('', PORT), HealthHandler) as httpd:
    httpd.serve_forever()
" &
HEALTH_PID=$!
echo "Health check server started on port 80, pid=$HEALTH_PID"

# If no command is provided, run the bot
if [ $# -eq 0 ]; then
    echo "No command provided, running: python -m app.manage bot"
    exec python -m app.manage bot
fi

# If the command contains app.web.main, override it
if echo "$@" | grep -q "app.web.main"; then
    echo "WARNING: app.web.main is not supported, overriding with: python -m app.manage bot"
    exec python -m app.manage bot
fi

# If the command contains uvicorn, override it
if echo "$@" | grep -q "uvicorn"; then
    echo "WARNING: uvicorn is not supported, overriding with: python -m app.manage bot"
    exec python -m app.manage bot
fi

echo "Running command: $@"
exec "$@"
