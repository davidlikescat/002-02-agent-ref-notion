# YouTube Auto-Archive Pipeline

Discord 채널에 YouTube URL을 올리면 자동으로 자막을 추출하고, Claude CLI가 내용을 정리하여 Notion에 저장하는 파이프라인.

## 아키텍처

```
[Discord 채널] → [Discord Bot (daemon)] → [SQLite Queue]
                                                ↓
[Notion 페이지] ← [Claude CLI + MCP] ← [Queue Processor (30분 주기)]
```

## 구성 요소

| 파일 | 역할 |
|------|------|
| `discord_bot.py` | Discord 채널 감시, YouTube URL 감지 → 큐에 추가 |
| `process_queue.py` | 큐에서 pending 항목 가져와 자막 추출 → Claude CLI → Notion 저장 |
| `extract_transcript.py` | youtube-transcript-api로 자막 추출 |
| `db_helper.py` | SQLite 큐 관리 (enqueue, dequeue, 상태 관리) |
| `config.yaml` | Discord 토큰, 채널 ID, 언어 설정 등 |
| `CLAUDE.md` | Claude CLI용 프로젝트 지침 (글쓰기 스타일, Notion 저장 규칙) |
| `process_queue.sh` | launchd용 쉘 래퍼 |
| `setup.sh` | 원클릭 설치 스크립트 |

## 동작 흐름

1. **URL 감지**: Discord `01-share-to-notion` 채널에 YouTube URL 올림 (PC/모바일 어디서든)
2. **큐 등록**: 봇이 URL을 감지하여 SQLite 큐에 추가, 채널에 확인 메시지 전송
3. **자막 추출**: Queue Processor가 30분마다 pending 항목을 처리, 자막 추출
4. **Notion 저장**: Claude CLI가 CLAUDE.md 지침에 따라 내용을 정리하여 ShareContent_DB에 저장
5. **완료 알림**: Discord 채널에 진행 상황 및 완료 알림 (Notion 페이지 링크 포함)

## 핵심 기능

### 자동 시작 (launchd)
- **맥북을 완전 종료했다가 다시 켜도 수동 재시작 필요 없음**
- `RunAtLoad=true`: 로그인 시 자동 시작
- `KeepAlive=true`: 크래시 시 자동 복구
- Discord 봇: 상시 실행 데몬
- Queue Processor: 30분 간격 실행

### 오프라인 복구
- 맥북이 꺼져있는 동안 모바일 등에서 Discord에 URL을 올려도 OK
- 봇 재시작 시 채널 히스토리(최근 200개)를 스캔하여 놓친 URL을 자동으로 큐에 추가
- 중복 URL은 자동 스킵 (SQLite UNIQUE 제약)

### Discord 진행 알림
```
[1/3] 자막 추출 중... `VIDEO_ID`
[2/3] 자막 추출 완료 (12,345자) → 노션 저장 중...
[3/3] 완료! `VIDEO_ID` → Notion 페이지 링크
```

### Notion 저장 스타일
- 본문 첫 블록: YouTube 썸네일 이미지
- 문단 중심 + bullet/callout/quote 혼합으로 가독성 확보
- 영어 콘텐츠는 한글 번역 후 정제
- Cover 이미지 자동 설정

## 설치 및 실행

```bash
# 1. 설치
cd ~/.agent-ref-pipeline
bash setup.sh

# 2. config.yaml에 Discord 봇 토큰/채널 ID 설정

# 3. launchd 서비스 로드 (setup.sh가 자동 처리)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agent-ref-pipeline.discord-bot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agent-ref-pipeline.processor.plist
```

설치 후에는 **아무것도 할 필요 없음**. 맥북 재시작해도 자동 실행.

## 서비스 관리

```bash
# 상태 확인
launchctl list | grep youtube

# 로그 확인
tail -f ~/.agent-ref-pipeline/logs/discord_bot.log
tail -f ~/.agent-ref-pipeline/logs/processor.log

# 큐 상태 확인
sqlite3 ~/.agent-ref-pipeline/queue.db "SELECT status, count(*) FROM queue GROUP BY status;"

# 수동 처리 실행
cd ~/.agent-ref-pipeline && python3 process_queue.py

# 서비스 재시작
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.agent-ref-pipeline.discord-bot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agent-ref-pipeline.discord-bot.plist
```

## 로컬 실행 vs 클라우드 실행

| | 로컬 (현재) | 클라우드 (AWS/GCP 등) |
|---|---|---|
| **비용** | 무료 (Claude CLI + MCP) | 유료 (Claude API 토큰 비용 + 서버 비용) |
| **Notion 연동** | Claude CLI의 MCP로 무료 | Notion API 직접 구현 필요 |
| **가용성** | 맥북 켜져있을 때만 | 24시간 365일 |
| **오프라인 대응** | 히스토리 스캔으로 복구 | 해당 없음 (항상 온라인) |
| **설정 난이도** | launchd 등록만 | 서버 프로비저닝, 환경 구성 |
| **유지보수** | CLAUDE.md 수정으로 간편 | 코드 수정 + 배포 필요 |
| **확장성** | 맥북 1대 한정 | 무제한 스케일링 가능 |

### 결론
- **개인 사용**: 로컬 실행 추천 (비용 0원, 설정 간편)
- **팀/서비스 운영**: 클라우드 이전 필요 (24시간 가용성, API 비용 발생)
- 현재 구조는 맥북만 켜두면 모바일에서도 Discord로 URL 전송 가능하므로 개인 사용에 충분

## 기술 스택

- Python 3.9+
- discord.py (Discord Bot)
- youtube-transcript-api v1.x (자막 추출)
- Claude CLI + `--dangerously-skip-permissions` (Notion MCP 활용)
- SQLite (WAL mode, 큐 관리)
- macOS launchd (서비스 관리)
