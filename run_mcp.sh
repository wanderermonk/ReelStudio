#!/usr/bin/env bash
# Starts the Reel Studio MCP server (stdio). Point your AI app at this script.
# Reel Studio itself must be running too (./run.sh).
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Run ./run.sh once first to set up Reel Studio." >&2; exit 1
fi
if [ ! -f .venv/.mcp-installed ] || [ requirements-mcp.txt -nt .venv/.mcp-installed ]; then
  PIP_CONFIG_FILE=/dev/null .venv/bin/python -m pip install --quiet --disable-pip-version-check \
    --index-url https://pypi.org/simple -r requirements-mcp.txt >&2
  touch .venv/.mcp-installed
fi
exec .venv/bin/python mcp_server.py
