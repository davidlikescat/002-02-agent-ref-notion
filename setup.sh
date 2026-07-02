#!/bin/bash
set -e

PIPELINE_DIR="$HOME/.agent-ref-pipeline"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"

echo "=== Agent Ref Pipeline Setup ==="

# 1. 디렉토리 생성
echo "[1/7] Creating directories..."
mkdir -p "$PIPELINE_DIR"/{temp,logs}

# 2. Python 패키지 확인
echo "[2/7] Checking Python packages..."
python3 -c "import discord; import youtube_transcript_api; import yaml" 2>/dev/null || {
    echo "Installing required packages..."
    pip3 install discord.py youtube-transcript-api pyyaml
}
echo "  All packages OK"

# 3. config.yaml 생성 (없는 경우만)
echo "[3/7] Setting up config..."
if [ ! -f "$PIPELINE_DIR/config.yaml" ]; then
    cat > "$PIPELINE_DIR/config.yaml" << 'YAML'
discord:
  bot_token: "YOUR_BOT_TOKEN"
  channel_ids:
    - "YOUR_CHANNEL_ID"
  notification_channel_id: "YOUR_NOTIFICATION_CHANNEL_ID"

queue:
  db_path: "~/.agent-ref-pipeline/queue.db"
  max_batch_size: 5
  max_retries: 3

transcript:
  languages: ["ko", "en"]
  include_timestamps: false

claude:
  project_name: "agent ref"

logging:
  log_dir: "~/.agent-ref-pipeline/logs"
  log_level: "INFO"
YAML
    echo "  config.yaml created (edit with your tokens!)"
else
    echo "  config.yaml already exists, skipping"
fi

# 4. SQLite DB 초기화
echo "[4/7] Initializing database..."
cd "$PIPELINE_DIR"
python3 -c "import db_helper; db_helper.init_db()"
echo "  queue.db initialized"

# 5. 실행 권한
echo "[5/7] Setting permissions..."
chmod +x "$PIPELINE_DIR/process_queue.sh"

# 6. launchd plist — Discord Bot
echo "[6/7] Creating launchd services..."
mkdir -p "$LAUNCH_AGENTS"

cat > "$LAUNCH_AGENTS/com.agent-ref-pipeline.discord-bot.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agent-ref-pipeline.discord-bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>${PIPELINE_DIR}/discord_bot.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PIPELINE_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${PIPELINE_DIR}/logs/discord_bot.log</string>
    <key>StandardErrorPath</key>
    <string>${PIPELINE_DIR}/logs/discord_bot.log</string>
</dict>
</plist>
PLIST

# 7. launchd plist — Queue Processor
cat > "$LAUNCH_AGENTS/com.agent-ref-pipeline.processor.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agent-ref-pipeline.processor</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PIPELINE_DIR}/process_queue.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PIPELINE_DIR}</string>
    <key>StartInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>${PIPELINE_DIR}/logs/processor.log</string>
    <key>StandardErrorPath</key>
    <string>${PIPELINE_DIR}/logs/processor.log</string>
</dict>
</plist>
PLIST

echo "[7/7] Done!"
echo ""
echo "=== Next Steps ==="
echo "1. Edit config:  nano $PIPELINE_DIR/config.yaml"
echo "   - Set bot_token, channel_ids, notification_channel_id"
echo ""
echo "2. Start services:"
echo "   launchctl load $LAUNCH_AGENTS/com.agent-ref-pipeline.discord-bot.plist"
echo "   launchctl load $LAUNCH_AGENTS/com.agent-ref-pipeline.processor.plist"
echo ""
echo "3. Check logs:"
echo "   tail -f $PIPELINE_DIR/logs/discord_bot.log"
echo "   tail -f $PIPELINE_DIR/logs/processor.log"
echo ""
echo "4. Stop services:"
echo "   launchctl unload $LAUNCH_AGENTS/com.agent-ref-pipeline.discord-bot.plist"
echo "   launchctl unload $LAUNCH_AGENTS/com.agent-ref-pipeline.processor.plist"
