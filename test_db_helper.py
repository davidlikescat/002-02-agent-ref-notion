import os
import sqlite3
import tempfile
import unittest

# 테스트용 DB 경로를 임시 디렉토리에 설정
TEST_DB = os.path.join(tempfile.gettempdir(), "test_queue.db")


class TestDbHelper(unittest.TestCase):
    def setUp(self):
        """각 테스트 전에 깨끗한 DB 생성"""
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        import db_helper
        db_helper.DB_PATH = TEST_DB
        db_helper.init_db()

    def tearDown(self):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)

    def test_init_db_creates_table(self):
        conn = sqlite3.connect(TEST_DB)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='queue'"
        )
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_enqueue_inserts_record(self):
        import db_helper
        result = db_helper.enqueue(
            url="https://youtube.com/watch?v=abc123",
            video_id="abc123",
            message_id="msg001",
            user="testuser"
        )
        self.assertTrue(result)
        pending = db_helper.get_pending(limit=5)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["video_id"], "abc123")

    def test_is_duplicate_returns_true_for_existing(self):
        import db_helper
        db_helper.enqueue("https://youtube.com/watch?v=abc123", "abc123", "msg001", "user1")
        self.assertTrue(db_helper.is_duplicate("abc123"))
        self.assertFalse(db_helper.is_duplicate("xyz999"))

    def test_enqueue_skips_duplicate(self):
        import db_helper
        result1 = db_helper.enqueue("https://youtube.com/watch?v=abc123", "abc123", "msg001", "user1")
        result2 = db_helper.enqueue("https://youtube.com/watch?v=abc123", "abc123", "msg002", "user2")
        self.assertTrue(result1)
        self.assertFalse(result2)
        pending = db_helper.get_pending(limit=5)
        self.assertEqual(len(pending), 1)

    def test_set_processing_updates_status(self):
        import db_helper
        db_helper.enqueue("https://youtube.com/watch?v=abc123", "abc123", "msg001", "user1")
        pending = db_helper.get_pending(limit=5)
        db_helper.set_processing(pending[0]["id"])
        # pending으로 다시 조회하면 비어야 함
        self.assertEqual(len(db_helper.get_pending(limit=5)), 0)

    def test_set_completed_updates_status_and_timestamp(self):
        import db_helper
        db_helper.enqueue("https://youtube.com/watch?v=abc123", "abc123", "msg001", "user1")
        pending = db_helper.get_pending(limit=5)
        item_id = pending[0]["id"]
        db_helper.set_processing(item_id)
        db_helper.set_completed(item_id)
        # DB에서 직접 확인
        conn = sqlite3.connect(TEST_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (item_id,)).fetchone()
        self.assertEqual(row["status"], "completed")
        self.assertIsNotNone(row["processed_at"])
        conn.close()

    def test_set_failed_increments_retry_and_requeues(self):
        import db_helper
        db_helper.enqueue("https://youtube.com/watch?v=abc123", "abc123", "msg001", "user1")
        pending = db_helper.get_pending(limit=5)
        item_id = pending[0]["id"]
        db_helper.set_processing(item_id)
        db_helper.set_failed(item_id, "some error", max_retries=3)
        # retry_count=1, status='pending'으로 복귀
        conn = sqlite3.connect(TEST_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (item_id,)).fetchone()
        self.assertEqual(row["retry_count"], 1)
        self.assertEqual(row["status"], "pending")
        conn.close()

    def test_set_failed_stays_failed_after_max_retries(self):
        import db_helper
        db_helper.enqueue("https://youtube.com/watch?v=abc123", "abc123", "msg001", "user1")
        pending = db_helper.get_pending(limit=5)
        item_id = pending[0]["id"]
        # 3번 실패
        for i in range(3):
            db_helper.set_processing(item_id)
            db_helper.set_failed(item_id, f"error {i+1}", max_retries=3)
        conn = sqlite3.connect(TEST_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (item_id,)).fetchone()
        self.assertEqual(row["retry_count"], 3)
        self.assertEqual(row["status"], "failed")
        conn.close()

    def test_get_pending_respects_limit_and_order(self):
        import db_helper
        for i in range(10):
            db_helper.enqueue(f"https://youtube.com/watch?v=vid{i}", f"vid{i}", f"msg{i}", "user")
        pending = db_helper.get_pending(limit=5)
        self.assertEqual(len(pending), 5)
        # created_at 오름차순 확인 (먼저 넣은 것이 먼저)
        self.assertEqual(pending[0]["video_id"], "vid0")
        self.assertEqual(pending[4]["video_id"], "vid4")


if __name__ == "__main__":
    unittest.main()
