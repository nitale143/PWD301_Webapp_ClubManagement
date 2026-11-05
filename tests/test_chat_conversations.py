"""Persistent assistant conversation API, browser, and migration checks."""

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from flask import g

from tests.support import csrf_for
from app import create_app, db
from app.legacy_sqlite import migrate_chat_conversations
from app.models import (
    Ban,
    ChatConversation,
    ChatMessage,
    MemberRecord,
    User,
    UserBan,
)


class ChatConversationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        database_path = Path(self.directory.name) / "chat-test.db"
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
            "SECRET_KEY": "test-only-key",
            "AI_PROVIDER": "rules",
            "AI_STRICT_FACTS": True,
            "AI_CHAT_MESSAGES_PER_MINUTE": 200,
        })
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()

        db.session.add(Ban(id=1, ten_ban="Engineering"))
        for user_id in (1, 2):
            user = User(
                id=user_id,
                mssv=f"SV{user_id:03d}",
                ho_ten=f"Member {user_id}",
                ngay_sinh=date(2003, 1, 1),
                sdt=f"09000000{user_id:02d}",
                email=f"member{user_id}@example.test",
                status="approved",
                chuc_vu="THANH_VIEN",
            )
            user.set_password("test-password")
            db.session.add(user)
            db.session.add(MemberRecord(user_id=user_id, ngay_gia_nhap=date(2024, 1, 1)))
            db.session.add(UserBan(user_id=user_id, ban_id=1))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.context.pop()
        self.directory.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        g.pop("_login_user", None)

    def csrf(self):
        self.client.get("/ai")
        return csrf_for(self.client)

    def api(self, method, path, *, json=None, csrf=True):
        headers = {"X-CSRF-Token": self.csrf()} if csrf else None
        return self.client.open(path, method=method, json=json, headers=headers)

    def create_conversation(self, title):
        response = self.api("POST", "/api/v1/assistant/conversations", json={"title": title})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json["id"]

    def send(self, question, conversation_id=None):
        payload = {"question": question}
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        return self.api("POST", "/api/v1/assistant", json=payload)

    def test_api_conversations_are_owned_selectable_and_archivable(self):
        self.login(1)
        first_id = self.create_conversation("Planning")
        second_id = self.create_conversation("Follow-up")

        implicit = self.send("Hướng dẫn sử dụng CLB")
        self.assertEqual(implicit.status_code, 200, implicit.json)
        self.assertEqual(implicit.json["conversation_id"], second_id)

        explicit = self.send("Câu hỏi ở cuộc trò chuyện đầu", first_id)
        self.assertEqual(explicit.status_code, 200, explicit.json)
        self.assertEqual(explicit.json["conversation_id"], first_id)
        self.assertEqual(
            [row.content for row in ChatMessage.query.filter_by(conversation_id=first_id)
             .order_by(ChatMessage.id).all()],
            ["Câu hỏi ở cuộc trò chuyện đầu", explicit.json["answer"]],
        )
        self.assertEqual(
            [row.content for row in ChatMessage.query.filter_by(conversation_id=second_id)
             .order_by(ChatMessage.id).all()],
            ["Hướng dẫn sử dụng CLB", implicit.json["answer"]],
        )

        self.login(2)
        other_id = self.create_conversation("Private")
        self.assertNotEqual(first_id, other_id)
        own_list = self.api("GET", "/api/v1/assistant/conversations")
        self.assertEqual(own_list.status_code, 200, own_list.json)
        visible_ids = {row["id"] for row in own_list.json["items"]}
        self.assertEqual(visible_ids, {other_id})
        hidden = self.send("Attempt another user's chat", first_id)
        self.assertEqual(hidden.status_code, 404, hidden.json)
        hidden_history = self.api(
            "GET", f"/api/v1/assistant/conversations/{first_id}/messages",
        )
        self.assertEqual(hidden_history.status_code, 404, hidden_history.json)

        renamed = self.api("PATCH", f"/api/v1/assistant/conversations/{other_id}",
                           json={"title": "Renamed"})
        self.assertEqual(renamed.status_code, 200, renamed.json)
        self.assertEqual(renamed.json["title"], "Renamed")
        archived = self.api("POST", f"/api/v1/assistant/conversations/{other_id}/archive",
                            json={"archived": True})
        self.assertEqual(archived.status_code, 200, archived.json)
        blocked = self.send("This must not enter an archived chat", other_id)
        self.assertEqual(blocked.status_code, 409, blocked.json)
        restored = self.api("POST", f"/api/v1/assistant/conversations/{other_id}/archive",
                            json={"archived": False})
        self.assertEqual(restored.status_code, 200, restored.json)
        allowed = self.send("The restored chat accepts messages", other_id)
        self.assertEqual(allowed.status_code, 200, allowed.json)

    def test_csrf_protects_conversation_management_and_proposal_writes(self):
        self.login(1)
        no_token = self.client.post("/api/v1/assistant/conversations", json={"title": "Blocked"})
        self.assertEqual(no_token.status_code, 400)
        created = self.create_conversation("Protected")

        for method, path, payload in (
            ("PATCH", f"/api/v1/assistant/conversations/{created}", {"title": "No token"}),
            ("POST", f"/api/v1/assistant/conversations/{created}/archive", {"archived": True}),
            ("POST", "/api/v1/assistant", {"question": "Tạo sự kiện",
                                          "conversation_id": created}),
        ):
            with self.subTest(method=method, path=path):
                response = self.client.open(path, method=method, json=payload)
                self.assertEqual(response.status_code, 400, response.json)

        html_create = self.client.post("/ai/conversations/new", data={"title": "No token"})
        self.assertEqual(html_create.status_code, 400)
        html_rename = self.client.post(f"/ai/conversations/{created}/rename",
                                       data={"title": "No token"})
        self.assertEqual(html_rename.status_code, 400)
        html_archive = self.client.post(f"/ai/conversations/{created}/archive")
        self.assertEqual(html_archive.status_code, 400)
        html_restore = self.client.post(f"/ai/conversations/{created}/restore")
        self.assertEqual(html_restore.status_code, 400)

        # Ordinary API chat also requires the global CSRF token.
        ordinary_chat = self.api("POST", "/api/v1/assistant", json={
            "question": "Hướng dẫn sử dụng CLB", "conversation_id": created,
        })
        self.assertEqual(ordinary_chat.status_code, 200, ordinary_chat.json)

    def test_browser_selects_a_conversation_and_keeps_send_form_scoped(self):
        self.login(1)
        conversation_id = self.create_conversation("Browser tab")
        token = self.csrf()
        page = self.client.get(f"/ai?chat={conversation_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Browser tab", page.data)
        self.assertIn(f'name="conversation_id" value="{conversation_id}"'.encode(), page.data)
        # Progress is a closed disclosure, not a permanently expanded panel.
        self.assertIn(b'<details id="chat-stream-status"', page.data)
        self.assertNotIn(b'<section id="chat-stream-status"', page.data)
        self.assertIn(b'data-chat-stream-duration', page.data)
        self.assertIn(b'<summary class="chat-stream-status-heading">', page.data)

        with patch("app.routes.ai.respond", return_value={
            "answer": "Scoped browser answer", "intent": "help", "provider": "rules",
        }):
            sent = self.client.post("/ai", data={
                "message": "Browser-only question",
                "conversation_id": str(conversation_id),
                "csrf_token": token,
            }, follow_redirects=True)
        self.assertEqual(sent.status_code, 200)
        self.assertIn(b"Scoped browser answer", sent.data)
        rows = ChatMessage.query.filter_by(conversation_id=conversation_id).order_by(ChatMessage.id).all()
        self.assertEqual([row.content for row in rows], ["Browser-only question", "Scoped browser answer"])

        new_chat = self.client.post("/ai/conversations/new", data={
            "title": "Created in browser", "csrf_token": token,
        })
        self.assertEqual(new_chat.status_code, 302)
        created = ChatConversation.query.filter_by(user_id=1, title="Created in browser").one()
        self.assertIn(f"chat={created.id}", new_chat.location)

        renamed = self.client.post(f"/ai/conversations/{created.id}/rename", data={
            "title": "Browser renamed", "csrf_token": token,
        })
        self.assertEqual(renamed.status_code, 302)
        self.assertEqual(created.title, "Browser renamed")
        archived = self.client.post(f"/ai/conversations/{created.id}/archive", data={
            "csrf_token": token,
        })
        self.assertEqual(archived.status_code, 302)
        self.assertTrue(created.archived)

        archived_page = self.client.get("/ai?archived=1&chats_page=1")
        self.assertEqual(archived_page.status_code, 200)
        self.assertIn("Đã lưu trữ".encode(), archived_page.data)
        self.assertIn(b"Browser renamed", archived_page.data)
        restored = self.client.post(f"/ai/conversations/{created.id}/restore", data={
            "csrf_token": token,
        })
        self.assertEqual(restored.status_code, 302)
        self.assertFalse(created.archived)

    def test_message_pages_use_conversation_scoped_keyset_cursors(self):
        self.login(1)
        conversation_id = self.create_conversation("Many messages")
        db.session.add_all(
            ChatMessage(user_id=1, conversation_id=conversation_id, role="user",
                        content=f"message {index}")
            for index in range(85)
        )
        db.session.commit()

        latest = self.api("GET", f"/api/v1/assistant/conversations/{conversation_id}/messages")
        self.assertEqual(latest.status_code, 200, latest.json)
        self.assertEqual(len(latest.json["items"]), 40)
        latest_ids = [row["id"] for row in latest.json["items"]]
        self.assertEqual(latest_ids, list(range(latest_ids[0], latest_ids[0] + 40)))
        older_id = latest.json["older_before"]
        self.assertIsNotNone(older_id)

        older = self.api("GET", f"/api/v1/assistant/conversations/{conversation_id}/messages?before={older_id}")
        self.assertEqual(older.status_code, 200, older.json)
        older_ids = [row["id"] for row in older.json["items"]]
        self.assertEqual(len(older_ids), 40)
        self.assertLess(older_ids[-1], latest_ids[0])

        after = self.api("GET", f"/api/v1/assistant/conversations/{conversation_id}/messages?after={older_ids[4]}")
        self.assertEqual(after.status_code, 200, after.json)
        after_ids = [row["id"] for row in after.json["items"]]
        self.assertEqual(after_ids[0], older_ids[5])
        self.assertEqual(len(after_ids), 40)

        other_chat = self.create_conversation("Cursor must belong to this chat")
        other_message = ChatMessage(user_id=1, conversation_id=other_chat, role="user",
                                    content="other chat cursor")
        db.session.add(other_message)
        db.session.commit()
        foreign_cursor = self.api(
            "GET", f"/api/v1/assistant/conversations/{conversation_id}/messages?before={other_message.id}",
        )
        self.assertEqual(foreign_cursor.status_code, 404, foreign_cursor.json)
        both_cursors = self.api(
            "GET", f"/api/v1/assistant/conversations/{conversation_id}/messages?before={latest_ids[0]}&after={latest_ids[1]}",
        )
        self.assertEqual(both_cursors.status_code, 400, both_cursors.json)

    def test_browser_before_and_after_queries_render_the_expected_message_page(self):
        self.login(1)
        conversation_id = self.create_conversation("Browser history")
        messages = [
            ChatMessage(user_id=1, conversation_id=conversation_id, role="user",
                        content=f"browser message {index}")
            for index in range(45)
        ]
        db.session.add_all(messages)
        db.session.commit()

        latest = self.client.get(f"/ai?chat={conversation_id}")
        self.assertEqual(latest.status_code, 200)
        self.assertIn(b"browser message 5", latest.data)
        self.assertIn(b"browser message 44", latest.data)
        self.assertNotIn(b"browser message 4</p>", latest.data)
        self.assertIn(f"before={messages[5].id}".encode(), latest.data)

        older = self.client.get(f"/ai?chat={conversation_id}&before={messages[5].id}")
        self.assertEqual(older.status_code, 200)
        self.assertIn(b"browser message 0", older.data)
        self.assertIn(b"browser message 4", older.data)
        self.assertNotIn(b"browser message 5</p>", older.data)
        self.assertIn(b"after=5", older.data)

    def test_provider_context_is_limited_to_six_messages_from_the_selected_chat(self):
        self.login(1)
        first_id = self.create_conversation("Context source")
        second_id = self.create_conversation("Other context")
        db.session.add_all([
            ChatMessage(user_id=1, conversation_id=first_id, role="user", content=f"first {index}")
            for index in range(8)
        ])
        db.session.add(ChatMessage(user_id=1, conversation_id=second_id, role="user",
                                   content="private to another chat"))
        db.session.commit()

        class CaptureProvider:
            provider_name = "capture"

            def __init__(self):
                self.history = None

            def answer(self, actor, question, history=None):
                self.history = list(history or [])
                return {"answer": "Captured", "intent": "help", "provider": self.provider_name}

        provider = CaptureProvider()
        with patch("app.services.agent.get_provider", return_value=provider):
            response = self.send("Hướng dẫn sử dụng CLB", first_id)

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([row.content for row in provider.history],
                         [f"first {index}" for index in range(2, 8)])
        self.assertNotIn("private to another chat", [row.content for row in provider.history])

    def test_rate_limit_still_counts_messages_across_conversations(self):
        self.login(1)
        first_id = self.create_conversation("First rate bucket")
        second_id = self.create_conversation("Second rate bucket")
        self.app.config["AI_CHAT_MESSAGES_PER_MINUTE"] = 1

        first = self.send("Hướng dẫn sử dụng CLB", first_id)
        self.assertEqual(first.status_code, 200, first.json)
        limited = self.send("Câu hỏi thứ hai", second_id)
        self.assertEqual(limited.status_code, 429, limited.json)
        self.assertEqual(ChatMessage.query.filter_by(conversation_id=second_id).count(), 0)

    def test_legacy_sqlite_chat_migrates_once_without_losing_messages_or_pending_data(self):
        legacy_path = Path(self.directory.name) / "legacy-chat.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.executescript("""
                CREATE TABLE user (id INTEGER PRIMARY KEY);
                INSERT INTO user (id) VALUES (7);
                CREATE TABLE chat_message (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES user(id),
                    role VARCHAR(10) NOT NULL,
                    content TEXT NOT NULL,
                    thoi_gian DATETIME NOT NULL
                );
                INSERT INTO chat_message VALUES
                    (31, 7, 'user', 'Old question', '2024-01-01 10:00:00'),
                    (32, 7, 'ai', 'Old answer', '2024-01-01 10:01:00');
                CREATE TABLE assistant_pending (
                    user_id INTEGER PRIMARY KEY REFERENCES user(id),
                    kind VARCHAR(10) NOT NULL,
                    text TEXT NOT NULL,
                    updated_at DATETIME NOT NULL
                );
                INSERT INTO assistant_pending VALUES
                    (7, 'event', 'Old clarification', '2024-01-01 10:02:00');
            """)

        backup = migrate_chat_conversations(legacy_path)
        self.assertIsNotNone(backup)
        self.assertTrue(Path(backup).is_file())
        self.assertIsNone(migrate_chat_conversations(legacy_path))
        with sqlite3.connect(legacy_path) as connection:
            messages = connection.execute(
                "SELECT id, content, conversation_id FROM chat_message ORDER BY id"
            ).fetchall()
            self.assertEqual([(row[0], row[1]) for row in messages],
                             [(31, "Old question"), (32, "Old answer")])
            self.assertEqual(messages[0][2], messages[1][2])
            conversation_id = messages[0][2]
            conversation = connection.execute(
                "SELECT user_id, title, legacy_marker FROM chat_conversation WHERE id=?",
                (conversation_id,),
            ).fetchone()
            self.assertEqual(conversation, (7, "Lịch sử trước nâng cấp", "legacy"))
            pending = connection.execute(
                "SELECT user_id, kind, text FROM conversation_pending WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            self.assertEqual(pending, (7, "event", "Old clarification"))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM assistant_pending").fetchone()[0], 1)

            timestamp = connection.execute(
                "SELECT updated_at FROM chat_conversation WHERE id=?", (conversation_id,),
            ).fetchone()[0]
            connection.execute(
                "DELETE FROM conversation_pending WHERE conversation_id=?", (conversation_id,),
            )
            connection.commit()

        # A deliberately cleared clarification must not be resurrected from the retained legacy row.
        self.assertIsNone(migrate_chat_conversations(legacy_path))
        self.assertIsNone(migrate_chat_conversations(legacy_path))
        with sqlite3.connect(legacy_path) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM conversation_pending WHERE conversation_id=?", (conversation_id,),
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT updated_at FROM chat_conversation WHERE id=?", (conversation_id,),
            ).fetchone()[0], timestamp)

    def test_initial_blank_browser_tab_does_not_switch_to_another_tabs_chat(self):
        self.login(1)
        initial_tab = self.client.get("/ai")
        self.assertEqual(initial_tab.status_code, 200)
        self.assertIn(b'name="new_conversation" value="1"', initial_tab.data)
        token = self.csrf()

        # Simulate another tab creating and selecting a conversation before the blank tab sends.
        other_tab_chat = ChatConversation(user_id=1, title="Other tab")
        db.session.add(other_tab_chat)
        db.session.commit()
        other_tab_id = other_tab_chat.id
        with patch("app.routes.ai.respond", return_value={
            "answer": "First tab answer", "intent": "help", "provider": "rules",
        }):
            response = self.client.post("/ai", data={
                "message": "Question from the initial blank tab",
                "new_conversation": "1",
                "csrf_token": token,
            }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        first_tab_chat = ChatConversation.query.filter_by(
            user_id=1, title="Question from the initial blank tab",
        ).one()
        self.assertNotEqual(first_tab_chat.id, other_tab_id)
        self.assertEqual(ChatMessage.query.filter_by(conversation_id=other_tab_id).count(), 0)
        self.assertEqual(ChatMessage.query.filter_by(conversation_id=first_tab_chat.id).count(), 2)

    def test_conversation_sidebar_pages_at_twenty_items(self):
        self.login(1)
        db.session.add_all(ChatConversation(user_id=1, title=f"Chat {index}")
                           for index in range(21))
        db.session.add_all(ChatConversation(user_id=1, title=f"Archived Chat {index}", archived=True)
                           for index in range(21))
        db.session.commit()

        first = self.api("GET", "/api/v1/assistant/conversations?page=1")
        second = self.api("GET", "/api/v1/assistant/conversations?page=2")
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(second.status_code, 200, second.json)
        self.assertEqual(len(first.json["items"]), 20)
        self.assertTrue(first.json["has_next"])
        self.assertEqual(len(second.json["items"]), 1)
        self.assertFalse(second.json["has_next"])

        archived_first = self.client.get("/ai?archived=1&chats_page=1")
        archived_second = self.client.get("/ai?archived=1&chats_page=2")
        self.assertEqual(archived_first.status_code, 200)
        self.assertEqual(archived_second.status_code, 200)
        self.assertIn(b"Archived Chat 20", archived_first.data)
        self.assertIn(b"chats_page=2", archived_first.data)
        self.assertNotIn(b"Archived Chat 0", archived_first.data)
        self.assertIn(b"Archived Chat 0", archived_second.data)
        self.assertNotIn(b"Archived Chat 20", archived_second.data)


if __name__ == "__main__":
    unittest.main()
