#!/bin/bash
# Manages two background services: the Discord bot and the web UI.
#
# Usage:
#   bash setup_launchagent.sh            — first-time install (run once)
#   bash setup_launchagent.sh restart    — restart both
#   bash setup_launchagent.sh stop       — stop both
#   bash setup_launchagent.sh status     — show running status
#   bash setup_launchagent.sh logs       — tail live logs (both)

BOT_PLIST="$HOME/Library/LaunchAgents/com.whowork.jobbot.plist"
WEB_PLIST="$HOME/Library/LaunchAgents/com.whowork.webui.plist"
LOG_DIR="/Users/a1-6/Documents/GitHub/logs"
PYTHON="/Users/a1-6/miniconda3/envs/whowork/bin/python3"

_bootout() {
    launchctl bootout gui/$(id -u)/com.whowork.jobbot 2>/dev/null || true
    launchctl bootout gui/$(id -u)/com.whowork.webui  2>/dev/null || true
    sleep 1
}

_bootstrap() {
    launchctl bootstrap gui/$(id -u) "$BOT_PLIST"
    launchctl bootstrap gui/$(id -u) "$WEB_PLIST"
}

_restart_all() {
    _bootout
    _bootstrap
    sleep 2
    echo "Bot and web UI restarted."
    echo "Web UI: http://localhost:8080"
    _status
}

_stop_all() {
    _bootout
    echo "Bot and web UI stopped."
}

_status() {
    echo ""
    echo "Service status:"
    launchctl list | grep whowork | awk '{
        pid=$1; exit_code=$2; label=$3
        status = (pid != "-") ? "RUNNING (PID " pid ")" : "STOPPED (last exit: " exit_code ")"
        printf "  %-30s %s\n", label, status
    }'
}

case "${1:-install}" in

  restart)
    _restart_all
    ;;

  stop)
    _stop_all
    ;;

  status)
    _status
    ;;

  logs)
    tail -f "$LOG_DIR/whowork_bot.log" "$LOG_DIR/whowork_web.log"
    ;;

  install)
    SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

    mkdir -p "$LOG_DIR"

    # ── Discord bot ────────────────────────────────────────────────────────────
    cat > "$BOT_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.whowork.jobbot</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT_DIR/bot.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/whowork_bot.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/whowork_bot.log</string>
</dict>
</plist>
EOF

    # ── Web UI ─────────────────────────────────────────────────────────────────
    cat > "$WEB_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.whowork.webui</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT_DIR/web.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/whowork_web.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/whowork_web.log</string>
</dict>
</plist>
EOF

    _bootout
    _bootstrap
    sleep 2

    echo "Installed and started:"
    echo "  Discord bot → background (check Discord)"
    echo "  Web UI      → http://localhost:8080"
    echo "  Logs        → $LOG_DIR/"
    echo ""
    echo "Commands:"
    echo "  bash scripts/setup_launchagent.sh restart  — restart both"
    echo "  bash scripts/setup_launchagent.sh stop     — stop both"
    echo "  bash scripts/setup_launchagent.sh status   — show status"
    echo "  bash scripts/setup_launchagent.sh logs     — tail live logs"
    _status
    ;;

  *)
    echo "Usage: bash scripts/setup_launchagent.sh [install|restart|stop|status|logs]"
    ;;

esac
