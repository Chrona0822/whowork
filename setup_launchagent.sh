#!/bin/bash
# Manages two background services: the Discord bot and the web UI.
#
# Usage:
#   bash setup_launchagent.sh            — first-time install (run once)
#   bash setup_launchagent.sh restart    — restart both
#   bash setup_launchagent.sh stop       — stop both
#   bash setup_launchagent.sh logs       — tail live logs (both)

BOT_PLIST="$HOME/Library/LaunchAgents/com.whowork.jobbot.plist"
WEB_PLIST="$HOME/Library/LaunchAgents/com.whowork.webui.plist"

_restart_all() {
    launchctl unload "$BOT_PLIST" 2>/dev/null
    launchctl unload "$WEB_PLIST" 2>/dev/null
    launchctl load   "$BOT_PLIST"
    launchctl load   "$WEB_PLIST"
}

_stop_all() {
    launchctl unload "$BOT_PLIST" 2>/dev/null
    launchctl unload "$WEB_PLIST" 2>/dev/null
}

case "${1:-install}" in

  restart)
    _restart_all
    echo "Bot and web UI restarted."
    echo "Web UI: http://localhost:8080"
    ;;

  stop)
    _stop_all
    echo "Bot and web UI stopped."
    ;;

  logs)
    tail -f /tmp/whowork_bot.log /tmp/whowork_web.log
    ;;

  install)
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    PYTHON="$(which python3)"

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
    <key>StandardOutPath</key>
    <string>/tmp/whowork_bot.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/whowork_bot.log</string>
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
    <key>StandardOutPath</key>
    <string>/tmp/whowork_web.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/whowork_web.log</string>
</dict>
</plist>
EOF

    _stop_all
    _restart_all

    echo "Installed and started:"
    echo "  Discord bot → background (check Discord)"
    echo "  Web UI      → http://localhost:8080"
    echo ""
    echo "Commands:"
    echo "  bash setup_launchagent.sh restart  — restart both"
    echo "  bash setup_launchagent.sh stop     — stop both"
    echo "  bash setup_launchagent.sh logs     — tail live logs"
    ;;

  *)
    echo "Usage: bash setup_launchagent.sh [install|restart|stop|logs]"
    ;;

esac
