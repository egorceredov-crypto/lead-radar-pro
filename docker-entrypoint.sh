#!/bin/sh
set -e

echo "Docker entrypoint started"
echo "Command: $@"
echo "Working directory: $(pwd)"

# If no command is provided, run the bot
if [ $# -eq 0 ]; then
    echo "No command provided, running: python -m app.manage bot"
    exec python -m app.manage bot
fi

# If the command is app.web.main, override it
if echo "$@" | grep -q "app.web.main"; then
    echo "WARNING: app.web.main is not supported, overriding with: python -m app.manage bot"
    exec python -m app.manage bot
fi

# If the command is uvicorn, override it
if echo "$@" | grep -q "uvicorn"; then
    echo "WARNING: uvicorn is not supported, overriding with: python -m app.manage bot"
    exec python -m app.manage bot
fi

echo "Running command: $@"
exec "$@"
