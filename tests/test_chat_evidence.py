"""Nguồn AI phải bền vững, thuộc người hỏi và kiểm tra lại quyền khi mở."""
from datetime import date
import unittest
from flask import g

from app import create_app, db
from app.evidence_models import ChatEvidence
from app.models import ChatConversation, ChatMessage, Role, User
from app.services.agent import respond
from app.services.chat_conversations import append_exchange, begin_turn, finish_turn
from app.services.chat_evidence import evidence_for
from app.services.common import DomainError


class ChatEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": "evidence-test-only",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SCHEDULER_ENABLED": False,
            "MAIL_ENABLED": False, "MAIL_OUTBOX": False, "AI_PROVIDER": "rules"})
        self.context = self.app.app_context()
        self.context.push()
        for key, role in ((1, "CN"), (2, "THANH_VIEN"), (3, "THANH_VIEN")):
            user = User(id=key, mssv=f"SE20000{key}", ho_ten=f"Member {key}",
                ngay_sinh=date(2000, 1, 1), sdt=f"+8490000000{key}",
                email=f"member{key}@example.test", status="approved", chuc_vu=role)
            user.set_password("Evidence-fixture-2026!")
            db.session.add(user)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def _save(self, user_id=1, question="tổng đã thu"):
        actor = db.session.get(User, user_id)
        conversation = ChatConversation(user_id=actor.id)
        db.session.add(conversation)
        db.session.commit()
        with self.app.test_request_context():
            human = begin_turn(actor, conversation, question)
            result = respond(actor, question, conversation=conversation)
            return actor, finish_turn(actor, conversation, result, human.id, "test-run")

    def test_fixed_query_creates_durable_evidence(self):
        actor, message = self._save()
        entry = db.session.get(ChatEvidence, message.id)
        self.assertIsNotNone(entry)
        with self.app.test_request_context():
            record, saved, items, hidden = evidence_for(actor, message.id)
        self.assertEqual(record.intent, "collected_total")
        self.assertIn("confirmed", record.query_labels[0])
        self.assertEqual(saved.id, message.id)
        self.assertEqual(items, [])

    def test_sources_endpoint_is_owner_only(self):
        actor, message = self._save()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = actor.get_id()
            session['_fresh'] = True
        response = client.get(f"/ai/messages/{message.id}/sources")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"SHA-256", response.data)
        other = db.session.get(User, 2)
        with client.session_transaction() as session:
            session['_user_id'] = other.get_id()
        g.pop('_login_user', None)
        self.assertEqual(client.get(f"/ai/messages/{message.id}/sources").status_code, 404)

    def test_permission_downgrade_revokes_evidence(self):
        actor, message = self._save()
        actor.chuc_vu = "THANH_VIEN"
        db.session.commit()
        with self.assertRaises(DomainError) as denied:
            evidence_for(actor, message.id)
        self.assertEqual(denied.exception.status, 403)

    def test_role_bit_change_revokes_evidence(self):
        actor, message = self._save()
        Role.query.filter_by(name='CN').first().permissions = 0
        db.session.commit()
        with self.assertRaises(DomainError):
            evidence_for(actor, message.id)

    def test_tampered_answer_is_not_verified(self):
        actor, message = self._save()
        message.content = "tampered 999"
        db.session.commit()
        with self.assertRaises(DomainError) as denied:
            evidence_for(actor, message.id)
        self.assertEqual(denied.exception.status, 409)

    def test_greeting_has_no_fake_citation(self):
        actor, message = self._save(question="hello")
        self.assertIsNone(db.session.get(ChatEvidence, message.id))

    def test_old_append_exchange_remains_compatible(self):
        actor = db.session.get(User, 2)
        conversation = ChatConversation(user_id=actor.id)
        db.session.add(conversation)
        db.session.flush()
        with self.app.test_request_context():
            append_exchange(actor, conversation, "hello", "hi")
        db.session.commit()
        self.assertEqual(ChatMessage.query.count(), 2)
        self.assertEqual(ChatEvidence.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
