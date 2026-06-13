import os
from youtube_transcript_api import YouTubeTranscriptApi


def extract(video_id, languages=None, temp_dir=None):
    if languages is None:
        languages = ["ko", "en"]
    if temp_dir is None:
        temp_dir = os.path.expanduser("~/.agent-ref-pipeline/temp")

    os.makedirs(temp_dir, exist_ok=True)

    api = YouTubeTranscriptApi()
    fetched = None

    # 지정 언어로 시도
    try:
        fetched = api.fetch(video_id, languages=languages)
    except Exception:
        pass

    # 실패 시 → 사용 가능한 첫 번째 자막
    if fetched is None:
        try:
            transcript_list = api.list(video_id)
            first = next(iter(transcript_list))
            fetched = api.fetch(video_id, languages=[first.language_code])
        except Exception as e:
            return {"success": False, "error": str(e)}

    # 텍스트 조합 (타임스탬프 없이)
    text = "\n".join(snippet.text for snippet in fetched)

    # 파일 저장
    file_path = os.path.join(temp_dir, f"{video_id}.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(text)

    return {"success": True, "text": text, "file_path": file_path}
