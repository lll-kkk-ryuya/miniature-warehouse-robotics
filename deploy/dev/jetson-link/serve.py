#!/usr/bin/env python3
"""Dev-machine (Mac) side of the Jetson link.

Serves a pull-based command bus for the Jetson when the router blocks
Mac -> Jetson connections. The Jetson polls this server, runs the command in
cmd.txt, and POSTs the output back to uploads/last.txt.

Canonical design: docs/jetson/02-remote-access-and-dev-link.md

Usage:
    python3 deploy/dev/jetson-link/serve.py [--port 8000] [--host-ip 192.168.11.11]

Then run the printed one-liner on the Jetson once per boot.
"""

from __future__ import annotations

import argparse
import datetime
import http.server
import os
import socket
import socketserver

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, ".state")
CMD_FILE = os.path.join(STATE, "cmd.txt")
LAST_FILE = os.path.join(STATE, "last.txt")
HISTORY_FILE = os.path.join(STATE, "history.txt")

AGENT_TEMPLATE = """#!/bin/sh
# mwr jetson-link agent. Polls the dev machine for commands and posts output back.
# Remove with: sudo pkill -f mwr-agent; sudo rm -f /usr/local/bin/mwr-agent
cat > /usr/local/bin/mwr-agent <<'INNER'
#!/bin/sh
MAC={host_ip}
PORT={port}
# Adopt whatever command is currently posted WITHOUT running it, so that
# (re)starting the agent never replays the previous command. Only commands
# posted after the agent came up are executed.
LAST=$(curl -s -m 8 "http://{host_ip}:{port}/cmd" 2>/dev/null | head -1)
while true; do
  C=$(curl -s -m 8 "http://$MAC:$PORT/cmd" 2>/dev/null)
  ID=$(printf '%s' "$C" | head -1)
  if [ -n "$ID" ] && [ "$ID" != "$LAST" ]; then
    LAST="$ID"
    OUT=$( {{ echo "### $ID"; printf '%s\\n' "$C" | tail -n +2 | sh 2>&1; }} )
    printf '%s\\n' "$OUT" | curl -s -m 60 -X POST --data-binary @- "http://$MAC:$PORT/up" >/dev/null 2>&1
  fi
  sleep 3
done
INNER
chmod +x /usr/local/bin/mwr-agent
pkill -f mwr-agent 2>/dev/null
nohup /usr/local/bin/mwr-agent >/var/log/mwr-agent.log 2>&1 &
sleep 1
echo "[mwr-agent started] pid=$(pgrep -f mwr-agent | head -1)"
"""


def detect_host_ip() -> str:
    """Best-effort LAN address of this machine (no traffic is actually sent)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def make_handler(host_ip: str, port: int) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def _send_text(self, data: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            if self.path.startswith("/a"):
                self._send_text(AGENT_TEMPLATE.format(host_ip=host_ip, port=port).encode())
                return
            if self.path.startswith("/cmd"):
                data = b""
                if os.path.exists(CMD_FILE):
                    with open(CMD_FILE, "rb") as handle:
                        data = handle.read()
                self._send_text(data)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            length = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(length) if length else self.rfile.read()
            os.makedirs(STATE, exist_ok=True)
            with open(LAST_FILE, "wb") as handle:
                handle.write(data)
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            with open(HISTORY_FILE, "ab") as handle:
                handle.write(f"\n===== {stamp} =====\n".encode() + data)
            print(f"[upload] {stamp} {len(data)} bytes -> {LAST_FILE}", flush=True)
            self._send_text(b"ok\n")

        def log_message(self, *args: object) -> None:
            """Silence per-request logging; uploads are reported explicitly."""

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Jetson link server (dev machine side)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host-ip", default=None, help="LAN IP the Jetson should call back")
    args = parser.parse_args()

    host_ip = args.host_ip or detect_host_ip()
    os.makedirs(STATE, exist_ok=True)
    if not os.path.exists(CMD_FILE):
        with open(CMD_FILE, "w", encoding="utf-8") as handle:
            handle.write('boot-check\necho "AGENT ALIVE: $(whoami)@$(hostname) $(date)"\n')

    print(f"jetson-link serving on {host_ip}:{args.port}  (state: {STATE})")
    print("Run this once on the Jetson (per boot):")
    print(f"    curl -s {host_ip}:{args.port}/a | sudo sh")
    print("Send a command from the dev machine:")
    print(f"    {os.path.join(BASE, 'send.sh')} 'free -h'")

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", args.port), make_handler(host_ip, args.port)) as srv:
        srv.serve_forever()


if __name__ == "__main__":
    main()
