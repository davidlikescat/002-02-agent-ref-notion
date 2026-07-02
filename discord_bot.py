import asyncio
import glob
import json
import logging
import logging.handlers
import os
import re
import sys
import time

import discord
import yaml

import db_helper

# 설정 로드
CONFIG_PATH = os.path.expanduser("~/.agent-ref-pipeline/config.yaml")

with open(CONFIG_PATH, "r") as f:
    config = yaml.safe_load(f)

# 로깅 설정
log_dir = os.path.expanduser(config["logging"]["log_dir"])
os.makedirs(log_dir, exist_ok=True)

logger = logging.getLogger("discord_bot")
logger.setLevel(getattr(logging, config["logging"]["log_level"]))
file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(log_dir, "discord_bot.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(file_handler)
logger.addHandler(logging.StreamHandler())

# YouTube URL 정규식
YOUTUBE_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?(?:'
    r'youtube\.com/watch\?[^\s]*v=([a-zA-Z0-9_-]{11})'
    r'|youtu\.be/([a-zA-Z0-9_-]{11})'
    r'|youtube\.com/shorts/([a-zA-Z0-9_-]{11})'
    r'|youtube\.com/live/([a-zA-Z0-9_-]{11})'
    r')'
)

CHANNEL_IDS = [int(cid) for cid in config["discord"]["channel_ids"]]
NOTIFICATION_CHANNEL_ID = int(config["discord"].get("notification_channel_id", 0))
TEMP_DIR = os.path.expanduser("~/.agent-ref-pipeline/temp")

# DB 초기화
db_helper.init_db()

# Discord Client
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
_notification_task_started = False
_last_heartbeat = time.time()  # 연결 상태 추적
HEARTBEAT_TIMEOUT = 300  # 5분간 연결 없으면 프로세스 종료 → launchd 재시작


def extract_video_ids(text):
    """텍스트에서 YouTube video_id들을 추출"""
    matches = YOUTUBE_PATTERN.findall(text)
    video_ids = []
    for match in matches:
        # 정규식 그룹 중 비어있지 않은 것이 video_id
        vid = next((g for g in match if g), None)
        if vid:
            video_ids.append(vid)
    return video_ids


def reconstruct_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


def make_reprocess_view(video_ids):
    """재처리 버튼이 담긴 View 생성. 버튼 클릭은 글로벌 on_interaction이
    custom_id(reprocess:{vid})로 처리하므로 콜백/영속 등록이 필요 없다.
    (Discord 제약: action row당 버튼 최대 5개)"""
    view = discord.ui.View(timeout=None)
    multi = len(video_ids) > 1
    for vid in video_ids[:5]:
        label = f"재처리 {vid}" if multi else "강제 재처리 (중복 무시)"
        view.add_item(
            discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"reprocess:{vid}",
            )
        )
    return view


@client.event
async def on_interaction(interaction):
    """재처리 버튼 클릭 처리. 실패/중복 영상을 중복 무시하고 pending으로 리셋."""
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = (interaction.data or {}).get("custom_id", "")
    if not custom_id.startswith("reprocess:"):
        return

    video_id = custom_id.split(":", 1)[1]
    try:
        reset = db_helper.reset_for_reprocess(video_id)
        if not reset:
            # 큐에 행이 없으면 신규로 추가
            db_helper.enqueue(
                url=reconstruct_url(video_id),
                video_id=video_id,
                message_id="reprocess-button",
                user=str(interaction.user),
            )
        await interaction.response.send_message(
            f"**재처리 큐에 추가됨** `{video_id}`\n"
            f"다음 처리 주기(최대 10분)에 자동 실행됩니다. (중복 무시 · retry 초기화)"
        )
        logger.info(f"[Reprocess] {video_id} → pending (by {interaction.user})")
    except Exception as e:
        logger.error(f"[Reprocess] failed for {video_id}: {e}")
        try:
            await interaction.response.send_message(
                f"재처리 실패: `{video_id}`\n```\n{str(e)[:300]}\n```"
            )
        except Exception:
            pass


async def scan_missed_messages():
    """봇이 오프라인이었던 동안 놓친 메시지를 스캔하여 큐에 추가"""
    await client.wait_until_ready()

    for channel_id in CHANNEL_IDS:
        channel = client.get_channel(channel_id)
        if not channel:
            continue

        enqueued = 0
        try:
            # 최근 200개 메시지 스캔 (봇 메시지 제외, YouTube URL만)
            async for message in channel.history(limit=200):
                if message.author == client.user:
                    continue
                if not message.content:
                    continue

                video_ids = extract_video_ids(message.content)
                for vid in video_ids:
                    if db_helper.is_duplicate(vid):
                        continue
                    url = reconstruct_url(vid)
                    if db_helper.enqueue(url=url, video_id=vid, message_id=str(message.id), user=str(message.author)):
                        enqueued += 1
                        logger.info(f"[Scan] Enqueued missed: {vid} from {message.author}")

            if enqueued > 0:
                await channel.send(f"**오프라인 동안 놓친 URL {enqueued}건을 큐에 추가했습니다.**")
                logger.info(f"[Scan] Total enqueued from history: {enqueued}")
            else:
                logger.info("[Scan] No missed URLs found")
        except Exception as e:
            logger.error(f"[Scan] History scan error: {e}")


@client.event
async def on_ready():
    global _notification_task_started, _last_heartbeat
    _last_heartbeat = time.time()
    logger.info(f"Bot logged in as {client.user}")
    if not _notification_task_started:
        _notification_task_started = True
        client.loop.create_task(check_notifications())
        client.loop.create_task(watchdog())
    # 재연결 시에도 놓친 메시지 스캔 (슬립 복구 대응)
    client.loop.create_task(scan_missed_messages())


@client.event
async def on_disconnect():
    logger.warning("Discord 연결 끊김 - 재연결 대기 중...")


@client.event
async def on_resumed():
    global _last_heartbeat
    _last_heartbeat = time.time()
    logger.info("Discord 연결 복구됨")


async def watchdog():
    """연결 상태 감시 - 5분간 연결 없으면 프로세스 종료 (launchd가 재시작)"""
    global _last_heartbeat
    await client.wait_until_ready()

    while not client.is_closed():
        await asyncio.sleep(60)
        _last_heartbeat = time.time() if not client.is_closed() and client.ws else _last_heartbeat
        elapsed = time.time() - _last_heartbeat
        if elapsed > HEARTBEAT_TIMEOUT:
            logger.error(f"Watchdog: {elapsed:.0f}초간 연결 없음 → 프로세스 종료 (launchd 재시작)")
            os._exit(1)


@client.event
async def on_message(message):
    # 봇 자신의 메시지 무시
    if message.author == client.user:
        return
    # 지정 채널만
    if message.channel.id not in CHANNEL_IDS:
        return

    logger.info(f"Message from {message.author} in #{message.channel.name}: {message.content[:100]!r}")

    if not message.content:
        logger.warning("Empty message content - Message Content Intent가 Discord Developer Portal에서 활성화되어 있는지 확인하세요")
        return

    video_ids = extract_video_ids(message.content)
    if not video_ids:
        logger.info(f"No YouTube URLs found in message")
        return

    enqueued = 0
    skipped = 0
    skipped_vids = []

    for vid in video_ids:
        if db_helper.is_duplicate(vid):
            skipped += 1
            skipped_vids.append(vid)
            continue

        url = reconstruct_url(vid)
        success = db_helper.enqueue(
            url=url,
            video_id=vid,
            message_id=str(message.id),
            user=str(message.author),
        )
        if success:
            enqueued += 1
            logger.info(f"Enqueued: {vid} from {message.author}")
        else:
            skipped += 1
            skipped_vids.append(vid)

    try:
        if enqueued > 0:
            await message.add_reaction("\u2705")
            await message.channel.send(
                f"**큐에 추가됨** ({enqueued}건) - 다음 처리 주기에 자동 실행됩니다."
            )
        elif skipped > 0:
            await message.add_reaction("\u23ed\ufe0f")
            await message.channel.send(
                f"이미 처리된 영상입니다. ({skipped}건 스킵)\n"
                f"실패했던 영상이면 아래 버튼으로 **중복 무시 재처리**하세요.",
                view=make_reprocess_view(skipped_vids),
            )
    except Exception as e:
        logger.error(f"Failed to send status: {e}")


async def check_notifications():
    """60초마다 완료 알림 파일 확인 → 디스코드 알림 전송"""
    await client.wait_until_ready()

    if not NOTIFICATION_CHANNEL_ID:
        return

    while not client.is_closed():
        try:
            channel = client.get_channel(NOTIFICATION_CHANNEL_ID)
            if not channel:
                logger.warning(f"Notification channel {NOTIFICATION_CHANNEL_ID} not found, retrying later")
                await asyncio.sleep(60)
                continue

            pattern = os.path.join(TEMP_DIR, "notify_*.json")
            for filepath in glob.glob(pattern):
                try:
                    with open(filepath, "r") as f:
                        data = json.load(f)
                    status = data.get("status", "unknown")
                    title = data.get("title", "Unknown")
                    url = data.get("url", "")

                    if status == "completed":
                        msg = f"\u2705 **처리 완료:** [{title}]({url})\n노션에 저장되었습니다."
                    else:
                        msg = f"\u274c **처리 실패:** [{title}]({url})"

                    await channel.send(msg)
                    os.remove(filepath)
                    logger.info(f"Notification sent for {data.get('video_id')}")
                except discord.Forbidden:
                    logger.error(f"No permission to send to channel {NOTIFICATION_CHANNEL_ID}. 봇에 Send Messages 권한이 필요합니다.")
                    # 권한 없으면 파일 삭제하고 다음으로
                    os.remove(filepath)
                except Exception as e:
                    logger.error(f"Notification error for {filepath}: {e}")
        except Exception as e:
            logger.error(f"Notification loop error: {e}")

        await asyncio.sleep(60)


if __name__ == "__main__":
    token = config["discord"]["bot_token"]
    if token == "YOUR_BOT_TOKEN":
        print("ERROR: config.yaml에서 bot_token을 설정하세요.")
        sys.exit(1)
    client.run(token)
