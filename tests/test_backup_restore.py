"""Tests for private SQLite backups and separate-target restore drills."""
import sqlite3
import json
import stat
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import create_app, db
from app.models import Ban, MemberRecord, User, UserBan
from app.services.backups import BackupError, create_backup, restore_backup, verify_backup


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.database_path = self.root / "source.sqlite3"
        self.avatar_dir = self.root / "avatars"
        self.evidence_dir = self.root / "evidence"
        self.avatar_dir.mkdir()
        self.evidence_dir.mkdir()
        self.avatar_bytes = b"avatar fixture bytes"
        self.evidence_bytes = b"payment evidence fixture bytes"
        (self.avatar_dir / "user_1.webp").write_bytes(self.avatar_bytes)
        (self.evidence_dir / "receipt.jpg").write_bytes(self.evidence_bytes)

        self.app = create_app({
            "TESTING": True,
            "SCHEDULER_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.database_path}",
            "SECRET_KEY": "backup-test-secret",
        })
        self.context = self.app.app_context()
        self.context.push()
        db.session.add(Ban(id=1, ten_ban="Engineering"))
        user = User(
            id=1,
            mssv="SE240001",
            ho_ten="Backup User",
            ngay_sinh=date(2003, 1, 1),
            sdt="+84912345678",
            email="backup@example.test",
            status="approved",
            chuc_vu="THANH_VIEN",
        )
        user.set_password("backup-fixture-password")
        db.session.add(user)
        db.session.add(MemberRecord(user_id=1, ngay_gia_nhap=date(2024, 1, 1)))
        db.session.add(UserBan(user_id=1, ban_id=1))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.directory.cleanup()

    def make_backup(self):
        return create_backup(
            self.database_path,
            self.root / "private-backups",
            avatar_dir=self.avatar_dir,
            evidence_dir=self.evidence_dir,
        )

    def test_backup_rejects_roots_inside_uploads_before_creating_stage(self):
        for source_directory in (self.avatar_dir, self.evidence_dir):
            nested = source_directory / "nested-backups"
            with self.assertRaises(BackupError):
                create_backup(self.database_path, nested,
                              avatar_dir=self.avatar_dir, evidence_dir=self.evidence_dir)
            self.assertFalse(nested.exists())
        with self.assertRaises(BackupError):
            create_backup(self.database_path, self.avatar_dir, avatar_dir=self.avatar_dir)

    def test_backup_is_private_verified_and_restores_to_separate_targets(self):
        backup = self.make_backup()
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((backup / "database.sqlite3").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((backup / "manifest.json").stat().st_mode), 0o600)

        result = verify_backup(backup)
        self.assertTrue(result["valid"])
        self.assertEqual(result["artifacts"], 2)
        self.assertIn("chat_conversation", {row[1] for row in json.loads(
            (backup / "manifest.json").read_text(encoding="utf-8"))["schema"] if row[0] == "table"})

        drill_dir = self.root / "restore-drill"
        drill_dir.mkdir()
        restored_db = restore_backup(
            backup,
            drill_dir / "restored.sqlite3",
            target_artifacts_dir=drill_dir / "uploads",
        )
        self.assertTrue(restored_db.is_file())
        self.assertEqual((drill_dir / "uploads" / "avatars" / "user_1.webp").read_bytes(),
                         self.avatar_bytes)
        self.assertEqual((drill_dir / "uploads" / "payment_evidence" / "receipt.jpg").read_bytes(),
                         self.evidence_bytes)
        with sqlite3.connect(f"file:{restored_db}?mode=ro", uri=True) as connection:
            self.assertEqual(connection.execute('SELECT mssv FROM "user" WHERE id = 1').fetchone(),
                             ("SE240001",))
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_verification_rejects_changed_database_and_restore_refuses_source_or_existing_target(self):
        backup = self.make_backup()
        with (backup / "database.sqlite3").open("ab") as corrupted:
            corrupted.write(b"tampered")
        with self.assertRaises(BackupError):
            verify_backup(backup)

        clean_backup = self.make_backup()
        with self.assertRaises(BackupError):
            restore_backup(clean_backup, self.database_path)
        existing_target = self.root / "already-exists.sqlite3"
        existing_target.write_bytes(b"keep me")
        with self.assertRaises(BackupError):
            restore_backup(clean_backup, existing_target)
        self.assertEqual(existing_target.read_bytes(), b"keep me")

    def test_backup_rejects_symlinked_upload_content(self):
        external = self.root / "external.png"
        external.write_bytes(b"outside file")
        link = self.avatar_dir / "outside.png"
        link.symlink_to(external)
        with self.assertRaises(BackupError):
            self.make_backup()


if __name__ == "__main__":
    unittest.main()
