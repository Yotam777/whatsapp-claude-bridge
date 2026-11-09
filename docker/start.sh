#!/bin/bash
# Supervisor of the container: copy the code into /data/app (personal files there are kept), then keep the bridge and
# the agent running — each is restarted 10 s after it exits (the agent exits on purpose for /הפעל מחדש).
set -u
APP=/data/app
mkdir -p "$APP" /data/home "$HF_HOME"
cp -a /opt/src/. "$APP"/
mkdir -p "$APP/whatsapp-bridge/store"
# Claude Code keeps its settings in ~/.claude (on /data, so sessions survive a restart)
mkdir -p /data/home/.claude

if [ ! -f "$APP/agent/config.json" ]; then
    echo "!! $APP/agent/config.json is missing — copy it from the PC (see docker/README.he.md). Waiting..."
    while [ ! -f "$APP/agent/config.json" ]; do sleep 30; done
fi

rotate() { [ -f "$1" ] && [ "$(stat -c %s "$1")" -gt 10000000 ] && mv -f "$1" "$1.1"; true; }

bridge_loop() {
    cd "$APP/whatsapp-bridge"
    while true; do
        rotate bridge.log
        echo "[$(date '+%F %T')] starting bridge" | tee -a bridge.log
        /usr/local/bin/whatsapp-bridge 2>&1 | tee -a bridge.log
        sleep 10
    done
}

agent_loop() {
    cd "$APP/agent"
    sleep 5  # let the bridge open its API first
    while true; do
        rotate agent.out
        /opt/agent-venv/bin/python -X utf8 agent.py 2>&1 | tee -a agent.out
        sleep 10
    done
}

bridge_loop &
agent_loop &
trap 'kill 0' TERM INT
wait
