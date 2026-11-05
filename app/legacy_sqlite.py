"""Nâng cấp database SQLite cũ (tạo trước khi dự án dùng Flask-Migrate), có sao lưu trước.

Chỉ dùng cho database đã tồn tại từ các bản cũ. Sau khi chạy các lệnh
`flask migrate-chats`, `migrate-money`, `migrate-email-recipients`, đánh dấu
schema bằng `flask db stamp 0001` rồi `flask db upgrade` để vào luồng Alembic.
"""

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


_CHAT_CONVERSATION_INDEX = (
    "ix_chat_conversation_owner_archived_updated",
    ("user_id", "archived", "updated_at", "id"),
)
_CHAT_MESSAGE_INDEX = (
    "ix_chat_message_conversation_id_id",
    ("conversation_id", "id"),
)


def _sqlite_table_exists(connection, table_name):
    """True nếu bảng tồn tại trong file SQLite."""
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _sqlite_columns(connection, table_name):
    """Danh sách cột (PRAGMA table_info) của một bảng SQLite."""
    return {row[1]: row for row in connection.execute(
        f'PRAGMA table_info("{table_name}")'
    ).fetchall()}


def _sqlite_index_columns(connection, table_name):
    """Ánh xạ tên index → các cột của index trên một bảng SQLite."""
    indexes = {}
    for row in connection.execute(f'PRAGMA index_list("{table_name}")').fetchall():
        name = row[1]
        columns = tuple(item[2] for item in connection.execute(
            f'PRAGMA index_info("{name}")'
        ).fetchall())
        indexes[name] = (bool(row[2]), columns)
    return indexes


def _sqlite_foreign_keys(connection, table_name):
    """Danh sách khóa ngoại của một bảng SQLite."""
    return {
        (row[3], row[2], row[4])
        for row in connection.execute(f'PRAGMA foreign_key_list("{table_name}")').fetchall()
    }


def _validate_chat_schema(connection):
    """Kiểm tra bảng chat_conversation/chat_message đã đúng cấu trúc mới chưa; sai thì báo lỗi."""
    required = {
        "chat_conversation": {
            "id", "user_id", "title", "created_at", "updated_at", "archived", "legacy_marker"
        },
        "conversation_pending": {
            "conversation_id", "user_id", "kind", "text", "updated_at"
        },
    }
    for table_name, names in required.items():
        columns = _sqlite_columns(connection, table_name)
        if not names.issubset(columns):
            raise RuntimeError(f"Schema {table_name} thiếu cột: {', '.join(sorted(names - columns.keys()))}.")

    conversations = _sqlite_columns(connection, "chat_conversation")
    if conversations["id"][5] != 1 or conversations["legacy_marker"][3] != 0:
        raise RuntimeError("Schema chat_conversation không đúng khóa chính hoặc legacy_marker nullable.")
    pending = _sqlite_columns(connection, "conversation_pending")
    if pending["conversation_id"][5] != 1:
        raise RuntimeError("Schema conversation_pending.conversation_id phải là khóa chính.")

    expected_fks = {
        "chat_conversation": {("user_id", "user", "id")},
        "conversation_pending": {
            ("conversation_id", "chat_conversation", "id"),
            ("user_id", "user", "id"),
        },
    }
    for table_name, expected in expected_fks.items():
        if not expected.issubset(_sqlite_foreign_keys(connection, table_name)):
            raise RuntimeError(f"Schema {table_name} thiếu khóa ngoại bắt buộc.")


def _ensure_chat_index(connection, table_name, index):
    """Tạo index cho bảng chat nếu chưa có."""
    name, expected_columns = index
    existing = _sqlite_index_columns(connection, table_name)
    if name in existing:
        if existing[name][1] != expected_columns:
            raise RuntimeError(f"Index {name} đã tồn tại nhưng có cấu trúc khác.")
        return
    connection.execute(
        f'CREATE INDEX "{name}" ON "{table_name}" ({", ".join(expected_columns)})'
    )


def _create_chat_tables(connection):
    """Tạo bảng chat_conversation và cột conversation_id cho database cũ."""
    if not _sqlite_table_exists(connection, "chat_conversation"):
        connection.execute("""
            CREATE TABLE chat_conversation (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES user (id),
                title VARCHAR(100) DEFAULT 'Cuộc trò chuyện mới' NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                archived BOOLEAN DEFAULT 0 NOT NULL,
                legacy_marker VARCHAR(16),
                UNIQUE (user_id, legacy_marker)
            )
        """)
    if not _sqlite_table_exists(connection, "conversation_pending"):
        connection.execute("""
            CREATE TABLE conversation_pending (
                conversation_id INTEGER NOT NULL PRIMARY KEY
                    REFERENCES chat_conversation (id),
                user_id INTEGER NOT NULL REFERENCES user (id),
                kind VARCHAR(10) NOT NULL,
                text TEXT NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """)


def _make_sqlite_backup(connection, database_path):
    """Sao lưu file SQLite (cùng thư mục, tên có thời gian) trước khi sửa cấu trúc; trả về đường
    dẫn bản sao.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = database_path.with_name(database_path.name + ".bak-chat-" + stamp)
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
    descriptor = os.open(str(backup), flags, 0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(str(backup), timeout=30)) as backup_connection:
            connection.backup(backup_connection)
        backup.chmod(0o600)
    except Exception:
        backup.unlink(missing_ok=True)
        raise
    return backup


def _chat_migration_needed(connection):
    """True nếu database cũ chưa có cấu trúc hội thoại AI."""
    tables = {
        name for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "user" not in tables:
        return False
    if "chat_conversation" not in tables or "conversation_pending" not in tables:
        return True
    if "chat_message" in tables:
        columns = _sqlite_columns(connection, "chat_message")
        if "conversation_id" not in columns:
            return True
        if connection.execute(
            "SELECT 1 FROM chat_message WHERE conversation_id IS NULL LIMIT 1"
        ).fetchone():
            return True
    for table_name, index in (
        ("chat_conversation", _CHAT_CONVERSATION_INDEX),
        ("chat_message", _CHAT_MESSAGE_INDEX),
    ):
        if table_name in tables:
            name, columns = index
            if _sqlite_index_columns(connection, table_name).get(name, (False, ()))[1] != columns:
                return True
    if "chat_conversation" in tables:
        unique_legacy = any(
            unique and columns == ("user_id", "legacy_marker")
            for unique, columns in _sqlite_index_columns(connection, "chat_conversation").values()
        )
        if not unique_legacy:
            return True
    if "assistant_pending" in tables:
        for (user_id,) in connection.execute("SELECT user_id FROM assistant_pending").fetchall():
            conversation = connection.execute(
                "SELECT id FROM chat_conversation WHERE user_id=? AND legacy_marker='legacy'",
                (user_id,),
            ).fetchone()
            if conversation is None:
                return True
    return False


def _timestamp_bounds(connection, user_id, include_pending=True):
    """Thời điểm sớm/muộn nhất của tin nhắn (và nháp đang chờ) của một user, dùng khi gom lịch sử
    cũ.
    """
    values = []
    if _sqlite_table_exists(connection, "chat_message"):
        row = connection.execute(
            "SELECT MIN(thoi_gian), MAX(thoi_gian) FROM chat_message "
            "WHERE user_id=? AND conversation_id IS NULL",
            (user_id,),
        ).fetchone()
        values.extend(value for value in row if value)
    if include_pending and _sqlite_table_exists(connection, "assistant_pending"):
        row = connection.execute(
            "SELECT updated_at FROM assistant_pending WHERE user_id=?", (user_id,)
        ).fetchone()
        if row and row[0]:
            values.append(row[0])
    if not values and not include_pending:
        return None, None
    if not values:
        # Database cũ (trước Alembic) lưu UTC; migration 0009 sẽ đổi sang giờ Việt Nam.
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
        return now, now
    # Existing values are SQLite datetime strings in the same sortable format.
    return min(values), max(values)


def _backfill_legacy_chat(connection):
    """Gom tin nhắn cũ chưa thuộc cuộc trò chuyện nào vào một cuộc trò chuyện "legacy" cho từng
    user.
    """
    users = set()
    if _sqlite_table_exists(connection, "chat_message"):
        users.update(row[0] for row in connection.execute(
            "SELECT DISTINCT user_id FROM chat_message WHERE conversation_id IS NULL"
        ).fetchall())
    if _sqlite_table_exists(connection, "assistant_pending"):
        users.update(row[0] for row in connection.execute(
            "SELECT p.user_id FROM assistant_pending AS p "
            "WHERE NOT EXISTS (SELECT 1 FROM chat_conversation AS c "
            "WHERE c.user_id=p.user_id AND c.legacy_marker='legacy')"
        ).fetchall())

    for user_id in sorted(users):
        conversation = connection.execute(
            "SELECT id, created_at, updated_at FROM chat_conversation "
            "WHERE user_id=? AND legacy_marker='legacy'",
            (user_id,),
        ).fetchone()
        created_conversation = conversation is None
        first_seen, last_seen = _timestamp_bounds(
            connection, user_id, include_pending=created_conversation
        )
        if conversation is None:
            cursor = connection.execute(
                "INSERT INTO chat_conversation "
                "(user_id, title, created_at, updated_at, archived, legacy_marker) "
                "VALUES (?, ?, ?, ?, 0, 'legacy')",
                (user_id, "Lịch sử trước nâng cấp", first_seen, last_seen),
            )
            conversation_id = cursor.lastrowid
        else:
            conversation_id = conversation[0]
            updated_at = max(conversation[2] or "", last_seen or "")
            if last_seen and updated_at != conversation[2]:
                connection.execute(
                    "UPDATE chat_conversation SET updated_at=? WHERE id=?",
                    (updated_at, conversation_id),
                )

        if _sqlite_table_exists(connection, "chat_message"):
            connection.execute(
                "UPDATE chat_message SET conversation_id=? "
                "WHERE user_id=? AND conversation_id IS NULL",
                (conversation_id, user_id),
            )
        if created_conversation and _sqlite_table_exists(connection, "assistant_pending"):
            old_pending = connection.execute(
                "SELECT kind, text, updated_at FROM assistant_pending WHERE user_id=?",
                (user_id,),
            ).fetchone()
            scoped_pending = connection.execute(
                "SELECT 1 FROM conversation_pending WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            if old_pending is not None and scoped_pending is None:
                connection.execute(
                    "INSERT INTO conversation_pending "
                    "(conversation_id, user_id, kind, text, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (conversation_id, user_id, old_pending[0], old_pending[1], old_pending[2]),
                )


def migrate_chat_conversations(database_path):
    """Add conversation tabs to an existing SQLite DB without dropping chat data.

    A missing on-disk file is a fresh application database; ``db.create_all``
    creates its schema. ``:memory:`` is accepted for migration checks and tests.
    Returns the protected backup path when an on-disk upgrade was needed.
    """
    raw_path = os.fspath(database_path)
    in_memory = raw_path == ":memory:"
    path = None if in_memory else Path(raw_path).resolve()
    if path is not None and not path.is_file():
        return None

    connection_path = ":memory:" if in_memory else str(path)
    with closing(sqlite3.connect(connection_path, timeout=30)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        if not _sqlite_table_exists(connection, "user"):
            return None

        migration_needed = _chat_migration_needed(connection)
        if not migration_needed:
            _validate_chat_schema(connection)
            if _sqlite_table_exists(connection, "chat_message"):
                message_columns = _sqlite_columns(connection, "chat_message")
                if "conversation_id" not in message_columns or (
                    "conversation_id", "chat_conversation", "id"
                ) not in _sqlite_foreign_keys(connection, "chat_message"):
                    raise RuntimeError("Schema chat_message thiếu khóa ngoại hội thoại hợp lệ.")
            return None

        backup = None
        if not in_memory:
            backup = _make_sqlite_backup(connection, path)

        try:
            connection.execute("BEGIN IMMEDIATE")
            if not _sqlite_table_exists(connection, "user"):
                connection.rollback()
                return backup

            _create_chat_tables(connection)
            _validate_chat_schema(connection)

            if _sqlite_table_exists(connection, "chat_message"):
                message_columns = _sqlite_columns(connection, "chat_message")
                required_message_columns = {"id", "user_id", "role", "content", "thoi_gian"}
                if not required_message_columns.issubset(message_columns):
                    missing = required_message_columns - message_columns.keys()
                    raise RuntimeError(
                        "Schema chat_message thiếu cột: " + ", ".join(sorted(missing)) + "."
                    )
                if "conversation_id" not in message_columns:
                    connection.execute(
                        "ALTER TABLE chat_message ADD COLUMN conversation_id INTEGER "
                        "REFERENCES chat_conversation (id)"
                    )
                elif ("conversation_id", "chat_conversation", "id") not in _sqlite_foreign_keys(
                    connection, "chat_message"
                ):
                    raise RuntimeError("chat_message.conversation_id thiếu khóa ngoại bắt buộc.")
                _ensure_chat_index(connection, "chat_message", _CHAT_MESSAGE_INDEX)

            _ensure_chat_index(connection, "chat_conversation", _CHAT_CONVERSATION_INDEX)
            if not any(
                unique and columns == ("user_id", "legacy_marker")
                for unique, columns in _sqlite_index_columns(
                    connection, "chat_conversation"
                ).values()
            ):
                connection.execute(
                    "CREATE UNIQUE INDEX uq_chat_conversation_legacy "
                    "ON chat_conversation (user_id, legacy_marker)"
                )

            message_count = (
                connection.execute("SELECT COUNT(*) FROM chat_message").fetchone()[0]
                if _sqlite_table_exists(connection, "chat_message") else 0
            )
            pending_count = (
                connection.execute("SELECT COUNT(*) FROM assistant_pending").fetchone()[0]
                if _sqlite_table_exists(connection, "assistant_pending") else 0
            )
            _backfill_legacy_chat(connection)

            if _sqlite_table_exists(connection, "chat_message"):
                after_count = connection.execute("SELECT COUNT(*) FROM chat_message").fetchone()[0]
                unassigned = connection.execute(
                    "SELECT COUNT(*) FROM chat_message WHERE conversation_id IS NULL"
                ).fetchone()[0]
                if after_count != message_count or unassigned:
                    raise RuntimeError("Không bảo toàn được tin nhắn cũ khi tạo conversation.")
            if _sqlite_table_exists(connection, "assistant_pending"):
                after_pending_count = connection.execute(
                    "SELECT COUNT(*) FROM assistant_pending"
                ).fetchone()[0]
                if after_pending_count != pending_count:
                    raise RuntimeError("Dữ liệu assistant_pending cũ đã thay đổi.")

            if _sqlite_table_exists(connection, "chat_message"):
                if ("conversation_id", "chat_conversation", "id") not in _sqlite_foreign_keys(
                    connection, "chat_message"
                ):
                    raise RuntimeError("Không xác minh được khóa ngoại chat_message.conversation_id.")
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"Phát hiện {len(violations)} lỗi khóa ngoại sau migration chat.")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return backup


def migrate_legacy_money(database_path):
    """Sao lưu rồi đổi cột fund_transaction.so_tien từ FLOAT sang NUMERIC(14,2); trả về đường dẫn
    bản sao hoặc None nếu đã đúng kiểu.
    """
    path = Path(database_path).resolve()
    if not path.is_file():
        raise ValueError(f"Không tìm thấy SQLite database: {path}")

    with closing(sqlite3.connect(path, timeout=30)) as connection:
        columns = connection.execute("PRAGMA table_info(fund_transaction)").fetchall()
        amount_column = next((row for row in columns if row[1] == "so_tien"), None)
        if amount_column is None:
            raise ValueError("Không tìm thấy cột fund_transaction.so_tien.")
        if amount_column[2].upper().startswith("NUMERIC"):
            return None
        if amount_column[2].upper() != "FLOAT":
            raise ValueError(f"Kiểu cột không được hỗ trợ: {amount_column[2]}")
        amounts = connection.execute("SELECT so_tien FROM fund_transaction").fetchall()
        if any(Decimal(str(row[0])).as_tuple().exponent < -2 for row in amounts):
            raise ValueError("Có giao dịch cũ nhiều hơn 2 chữ số thập phân; cần đối soát trước khi chuyển.")
        backup = path.with_name(path.name + ".bak-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
        with closing(sqlite3.connect(backup)) as backup_connection:
            connection.backup(backup_connection)

        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            before = connection.execute("SELECT COUNT(*) FROM fund_transaction").fetchone()[0]
            connection.execute("""
                CREATE TABLE fund_transaction_new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    period_id INTEGER NOT NULL REFERENCES fund_period (id),
                    danh_muc VARCHAR(100) NOT NULL,
                    noi_dung VARCHAR(255),
                    ngay DATE NOT NULL,
                    so_tien NUMERIC(14, 2) NOT NULL,
                    tao_boi_id INTEGER REFERENCES user (id)
                )
            """)
            connection.execute("""
                INSERT INTO fund_transaction_new
                    (id, period_id, danh_muc, noi_dung, ngay, so_tien, tao_boi_id)
                SELECT id, period_id, danh_muc, noi_dung, ngay, so_tien, tao_boi_id
                FROM fund_transaction
            """)
            after = connection.execute("SELECT COUNT(*) FROM fund_transaction_new").fetchone()[0]
            if before != after:
                raise RuntimeError("Số giao dịch thay đổi trong quá trình chuyển.")
            connection.execute("DROP TABLE fund_transaction")
            connection.execute("ALTER TABLE fund_transaction_new RENAME TO fund_transaction")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Lỗi ràng buộc khóa ngoại sau khi chuyển.")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
        return backup


def migrate_email_recipients(database_path):
    """Allow external email drafts while preserving existing member drafts."""
    path = Path(database_path).resolve()
    if not path.is_file():
        raise ValueError(f"Không tìm thấy SQLite database: {path}")

    with closing(sqlite3.connect(path, timeout=30)) as connection:
        columns = connection.execute("PRAGMA table_info(email_proposal)").fetchall()
        if not columns:
            return None  # Fresh databases get the new schema from db.create_all().
        recipient = next((row for row in columns if row[1] == "recipient_id"), None)
        if recipient is None:
            raise ValueError("Không tìm thấy cột email_proposal.recipient_id.")
        if recipient[3] == 0:
            return None
        if recipient[3] != 1:
            raise ValueError("Kiểu cột email_proposal.recipient_id không được hỗ trợ.")

        backup = path.with_name(path.name + ".bak-email-" +
                                datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
        with closing(sqlite3.connect(backup)) as backup_connection:
            connection.backup(backup_connection)
        backup.chmod(0o600)

        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            before = connection.execute("SELECT COUNT(*) FROM email_proposal").fetchone()[0]
            connection.execute("""
                CREATE TABLE email_proposal_new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    recipient_id INTEGER REFERENCES user (id),
                    recipient_email VARCHAR(120) NOT NULL,
                    request_text TEXT NOT NULL,
                    subject VARCHAR(180) NOT NULL,
                    body TEXT NOT NULL,
                    status VARCHAR(12) NOT NULL,
                    created_by_id INTEGER NOT NULL REFERENCES user (id),
                    reviewed_by_id INTEGER REFERENCES user (id),
                    created_at DATETIME NOT NULL,
                    reviewed_at DATETIME,
                    sent_at DATETIME,
                    CONSTRAINT ck_email_proposal_status
                        CHECK (status IN ('pending','sending','sent','failed','rejected'))
                )
            """)
            connection.execute("""
                INSERT INTO email_proposal_new
                    (id, recipient_id, recipient_email, request_text, subject, body,
                     status, created_by_id, reviewed_by_id, created_at, reviewed_at, sent_at)
                SELECT id, recipient_id, recipient_email, request_text, subject, body,
                       status, created_by_id, reviewed_by_id, created_at, reviewed_at, sent_at
                FROM email_proposal
            """)
            after = connection.execute("SELECT COUNT(*) FROM email_proposal_new").fetchone()[0]
            if before != after:
                raise RuntimeError("Số bản nháp email thay đổi trong quá trình chuyển.")
            connection.execute("DROP TABLE email_proposal")
            connection.execute("ALTER TABLE email_proposal_new RENAME TO email_proposal")
            connection.execute("CREATE INDEX ix_email_proposal_status ON email_proposal (status)")
            connection.execute("CREATE INDEX ix_email_proposal_creator_status ON email_proposal (created_by_id, status)")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Lỗi ràng buộc khóa ngoại sau khi chuyển nháp email.")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
        return backup
