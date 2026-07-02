import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import threading
import time

import requests
import yaml

import db_helper
import extract_transcript

# 설정 로드
CONFIG_PATH = os.path.expanduser("~/.agent-ref-pipeline/config.yaml")

with open(CONFIG_PATH, "r") as f:
    config = yaml.safe_load(f)

# 로깅 설정
log_dir = os.path.expanduser(config["logging"]["log_dir"])
os.makedirs(log_dir, exist_ok=True)

logger = logging.getLogger("processor")
logger.setLevel(getattr(logging, config["logging"]["log_level"]))
file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(log_dir, "processor.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(file_handler)
logger.addHandler(logging.StreamHandler())

# DB 초기화
db_helper.init_db()

def _log_stream_event(event):
    """stream-json 이벤트를 읽기 쉬운 로그로 출력"""
    etype = event.get("type", "")
    if etype == "system":
        logger.info("  [Claude] 시스템 초기화...")
    elif etype == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                logger.info(f"  [Claude] 🔧 도구 호출: {block.get('name', 'unknown')}")
            elif block.get("type") == "text" and block.get("text", "").strip():
                logger.info(f"  [Claude] 💬 {block['text'][:150]}")
    elif etype == "content_block_start":
        cb = event.get("content_block", {})
        if cb.get("type") == "tool_use":
            logger.info(f"  [Claude] 🔧 도구 호출 시작: {cb.get('name', 'unknown')}")
    elif etype == "result":
        logger.info(
            f"  [Claude] ✅ 완료 | "
            f"턴: {event.get('num_turns', 0)} | "
            f"소요: {event.get('duration_ms', 0)/1000:.0f}초 | "
            f"비용: ${event.get('cost_usd', 0):.4f}"
        )


TEMP_DIR = os.path.expanduser("~/.agent-ref-pipeline/temp")
MAX_BATCH = config["queue"]["max_batch_size"]
MAX_RETRIES = config["queue"]["max_retries"]
LANGUAGES = config["transcript"]["languages"]
PIPELINE_DIR = os.path.expanduser("~/.agent-ref-pipeline")

# Discord 알림 설정
BOT_TOKEN = config["discord"]["bot_token"]
CHANNEL_ID = config["discord"]["notification_channel_id"]
DISCORD_API = "https://discord.com/api/v10"


def discord_msg(content, reprocess_video_id=None):
    """디스코드 채널에 메시지 전송.
    reprocess_video_id가 주어지면 '재처리(중복 무시)' 버튼을 함께 첨부한다.
    버튼 클릭은 discord_bot.py의 on_interaction(custom_id=reprocess:{vid})이 처리."""
    payload = {"content": content[:2000]}
    if reprocess_video_id:
        payload["components"] = [{
            "type": 1,  # action row
            "components": [{
                "type": 2,            # button
                "style": 1,           # primary
                "label": "재처리 (중복 무시)",
                "custom_id": f"reprocess:{reprocess_video_id}",
            }],
        }]
    try:
        requests.post(
            f"{DISCORD_API}/channels/{CHANNEL_ID}/messages",
            headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Discord message failed: {e}")


def process_item(item, idx, total):
    """단일 큐 항목 처리: 자막 추출 → Claude CLI → 결과 저장"""
    item_id = item["id"]
    video_id = item["video_id"]
    url = item["youtube_url"]
    item_start = time.time()

    logger.info(f"Processing: {video_id} ({url})")
    discord_msg(
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"**[{idx}/{total}] 처리 시작** `{video_id}`\n"
        f"{url}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

    # Step 1: 자막 추출
    discord_msg(f"`[Step 1/3]` 자막 추출 중... (언어: {', '.join(LANGUAGES)})")
    t0 = time.time()

    result = extract_transcript.extract(
        video_id=video_id,
        languages=LANGUAGES,
        temp_dir=TEMP_DIR,
    )
    elapsed = time.time() - t0

    if not result["success"]:
        error = result["error"]
        logger.error(f"Transcript extraction failed for {video_id}: {error}")
        db_helper.set_failed(item_id, error, max_retries=MAX_RETRIES)
        discord_msg(
            f"`[Step 1/3]` **자막 추출 실패** ({elapsed:.1f}초)\n"
            f"```\n{error[:500]}\n```\n"
            f"retry: {item.get('retry_count', 0)+1}/{MAX_RETRIES}",
            reprocess_video_id=video_id,
        )
        return False

    transcript_text = result["text"]
    file_path = result["file_path"]
    line_count = transcript_text.count("\n") + 1
    discord_msg(
        f"`[Step 1/3]` **자막 추출 완료** ({elapsed:.1f}초)\n"
        f"- 글자수: {len(transcript_text):,}자\n"
        f"- 줄수: {line_count:,}줄\n"
        f"- 파일: `{os.path.basename(file_path)}`"
    )

    # Step 2: Claude CLI 호출
    discord_msg(f"`[Step 2/3]` Claude CLI 호출 중... (timeout: 1200초)")
    t0 = time.time()

    prompt = (
        f"다음 YouTube 영상의 자막을 CLAUDE.md 지침에 따라 정제하여 노션 Agent References DB에 저장해줘.\n"
        f"영상 URL: {url}\n"
        f"Video ID: {video_id}\n\n"
        f"중요:\n"
        f"- 제목은 영상 내용의 핵심 메시지를 담아 한글로 작성 (video_id를 제목으로 쓰지 말 것)\n"
        f"- 영상 성격에 맞는 스타일(스토리텔링형/분석형) 선택\n"
        f"- 원본의 구체적 수치, 금액, 사례명은 반드시 보존\n\n"
        f"[자막 텍스트]\n{transcript_text}"
    )

    try:
        proc = subprocess.Popen(
            [os.path.expanduser("~/.local/bin/claude"), "-p",
             "--dangerously-skip-permissions", prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=PIPELINE_DIR,
        )

        # 타임아웃 타이머
        timed_out = False
        def _kill():
            nonlocal timed_out
            timed_out = True
            proc.kill()
        timer = threading.Timer(1200, _kill)
        timer.start()

        try:
            stdout_output, stderr_output = proc.communicate()
        except:
            proc.kill()
            stdout_output, stderr_output = proc.communicate()
        finally:
            timer.cancel()

        if timed_out:
            raise subprocess.TimeoutExpired("claude", 1200)

        stdout = stdout_output
        elapsed = time.time() - t0

        if proc.returncode != 0:
            error_msg = stderr_output or stdout[:300] or f"exit code: {proc.returncode}"
            logger.error(f"Claude CLI failed for {video_id}: {error_msg}")
            db_helper.set_failed(item_id, error_msg, max_retries=MAX_RETRIES)
            discord_msg(
                f"`[Step 2/3]` **Claude CLI 실패** ({elapsed:.1f}초)\n"
                f"- exit code: {proc.returncode}\n"
                f"```\n{error_msg[:500]}\n```",
                reprocess_video_id=video_id,
            )
            return False

        # Notion 실패 감지 (exit code 0이지만 실제로 저장 못한 경우)
        fail_keywords = ["토큰이 설정되어 있지 않", "API 키", "접근 권한", "연결할 수 없", "토큰을 제공해"]
        if any(kw in stdout for kw in fail_keywords):
            error_msg = f"Notion 저장 실패 감지: {stdout[:300]}"
            logger.error(f"Claude CLI returned 0 but Notion failed for {video_id}: {error_msg}")
            db_helper.set_failed(item_id, error_msg, max_retries=MAX_RETRIES)
            discord_msg(
                f"`[Step 2/3]` **Notion 저장 실패** ({elapsed:.1f}초)\n"
                f"- Claude 응답은 성공이지만 Notion 연동 실패\n"
                f"```\n{stdout[:500]}\n```",
                reprocess_video_id=video_id,
            )
            return False

        logger.info(f"Claude CLI success for {video_id}")
        if stderr_output:
            logger.warning(f"Claude CLI stderr: {stderr_output[:300]}")

        discord_msg(
            f"`[Step 2/3]` **Claude CLI 완료** ({elapsed:.1f}초)\n"
            f"- 응답 길이: {len(stdout):,}자"
        )

    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        logger.error(f"Claude CLI timeout for {video_id}")
        db_helper.set_failed(item_id, "Claude CLI timeout (1200s)", max_retries=MAX_RETRIES)
        discord_msg(f"`[Step 2/3]` **Claude CLI 타임아웃** ({elapsed:.1f}초)", reprocess_video_id=video_id)
        return False
    except Exception as e:
        elapsed = time.time() - t0
        logger.error(f"Claude CLI error for {video_id}: {e}")
        db_helper.set_failed(item_id, str(e), max_retries=MAX_RETRIES)
        discord_msg(f"`[Step 2/3]` **시스템 에러** ({elapsed:.1f}초)\n```\n{str(e)[:500]}\n```", reprocess_video_id=video_id)
        return False

    # Step 3: 완료 처리
    db_helper.set_completed(item_id)

    # Claude CLI 출력에서 노션 URL 추출
    notion_url = ""
    if stdout:
        match = re.search(r'https://www\.notion\.so/[^\s\)]+', stdout)
        if match:
            notion_url = match.group(0)

    # temp 자막 파일 삭제
    if os.path.exists(file_path):
        os.remove(file_path)

    total_elapsed = time.time() - item_start
    if notion_url:
        discord_msg(
            f"`[Step 3/3]` **처리 완료** (총 {total_elapsed:.0f}초)\n"
            f"- Notion: {notion_url}"
        )
    else:
        discord_msg(
            f"`[Step 3/3]` **처리 완료** (총 {total_elapsed:.0f}초)\n"
            f"- 노션에 저장됨 (URL 미확인)"
        )

    logger.info(f"Completed: {video_id} ({total_elapsed:.0f}s)")
    return True


def main():
    pending = db_helper.get_pending(limit=MAX_BATCH)

    if not pending:
        logger.info("No pending items. Exiting.")
        return

    logger.info(f"Processing {len(pending)} items")
    discord_msg(
        f"**Queue Processor 시작**\n"
        f"- 대기 항목: {len(pending)}건\n"
        f"- 파이프라인: `agent-ref-pipeline`"
    )

    completed = 0
    failed = 0

    for i, item in enumerate(pending, 1):
        db_helper.set_processing(item["id"])
        if process_item(item, i, len(pending)):
            completed += 1
        else:
            failed += 1

    discord_msg(
        f"**Queue Processor 종료**\n"
        f"- 성공: {completed}건 / 실패: {failed}건"
    )
    logger.info(f"Done. Completed: {completed}, Failed: {failed}")


if __name__ == "__main__":
    main()
