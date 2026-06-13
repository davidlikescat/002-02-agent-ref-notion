import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

TEMP_DIR = os.path.join(tempfile.gettempdir(), "yt_test_temp")


class TestExtractTranscript(unittest.TestCase):
    def setUp(self):
        os.makedirs(TEMP_DIR, exist_ok=True)

    def tearDown(self):
        import shutil
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)

    @patch("extract_transcript.YouTubeTranscriptApi")
    def test_extract_korean_transcript(self, mock_api):
        mock_api.get_transcript.return_value = [
            {"text": "안녕하세요", "start": 0.0, "duration": 2.0},
            {"text": "오늘 주제는", "start": 2.0, "duration": 1.5},
        ]
        import extract_transcript
        result = extract_transcript.extract(
            video_id="test123",
            languages=["ko", "en"],
            temp_dir=TEMP_DIR,
        )
        self.assertTrue(result["success"])
        self.assertIn("안녕하세요", result["text"])
        self.assertIn("오늘 주제는", result["text"])
        self.assertTrue(os.path.exists(result["file_path"]))

    @patch("extract_transcript.YouTubeTranscriptApi")
    def test_extract_falls_back_to_english(self, mock_api):
        call_count = 0
        def side_effect(video_id, languages=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # ko 시도
                raise Exception("No transcript in ko")
            return [{"text": "Hello", "start": 0.0, "duration": 1.0}]
        mock_api.get_transcript.side_effect = side_effect

        import extract_transcript
        result = extract_transcript.extract(
            video_id="test456",
            languages=["ko", "en"],
            temp_dir=TEMP_DIR,
        )
        self.assertTrue(result["success"])
        self.assertIn("Hello", result["text"])

    @patch("extract_transcript.YouTubeTranscriptApi")
    def test_extract_failure_returns_error(self, mock_api):
        mock_api.get_transcript.side_effect = Exception("No transcript available")
        mock_api.list_transcripts.side_effect = Exception("No transcripts")

        import extract_transcript
        result = extract_transcript.extract(
            video_id="no_subs",
            languages=["ko", "en"],
            temp_dir=TEMP_DIR,
        )
        self.assertFalse(result["success"])
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
