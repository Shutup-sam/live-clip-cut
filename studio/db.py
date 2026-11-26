"""The database: accounts, sign-in sessions, notifications and the record of AI use.

PostgreSQL on a server (DATABASE_URL=postgresql+psycopg://...). With no DATABASE_URL it uses a SQLite file,
which is fine for one machine."""
import time

import sqlalchemy as sa

from . import config

_SQLITE = config.DATABASE_URL.startswith("sqlite")
engine = sa.create_engine(config.DATABASE_URL, pool_pre_ping=not _SQLITE, future=True, pool_size=20, max_overflow=40,
                          **({"connect_args": {"timeout": 30}} if _SQLITE else {}))

if _SQLITE:
    @sa.event.listens_for(engine, "connect")
    def _fast_sqlite(conn, _record):
        # WAL: people reading are never held up by someone writing. NORMAL: safe against crashes of the app,
        # without waiting for the disk on every single write.
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()
meta = sa.MetaData()

creator_branding = sa.Table(
    "creator_branding", meta,
    sa.Column("user_id", sa.String(40), primary_key=True),
    sa.Column("creator_id", sa.String(60), primary_key=True),
    sa.Column("logo_asset_path", sa.String(100), nullable=False, server_default=""),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("position", sa.String(20), nullable=False, server_default="top_right"),
    sa.Column("width", sa.Integer, nullable=False, server_default="20"),
    sa.Column("opacity", sa.Integer, nullable=False, server_default="100"),
    sa.Column("padding", sa.Integer, nullable=False, server_default="24"),
    sa.Column("background_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("display_duration", sa.String(12), nullable=False, server_default="entire"),
    sa.Column("custom_start_time", sa.Float, nullable=False, server_default="0"),
    sa.Column("custom_end_time", sa.Float, nullable=False, server_default="0"),
    sa.Column("second_logo_asset_path", sa.String(100), nullable=False, server_default=""),
    sa.Column("second_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("second_position", sa.String(20), nullable=False, server_default="bottom_right"),
    sa.Column("second_width", sa.Integer, nullable=False, server_default="15"),
    sa.Column("x", sa.Integer, nullable=False, server_default="50"),
    sa.Column("y", sa.Integer, nullable=False, server_default="50"),
    sa.Column("second_x", sa.Integer, nullable=False, server_default="50"),
    sa.Column("second_y", sa.Integer, nullable=False, server_default="50"),
    sa.Column("remove_black", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("second_remove_black", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("updated_at", sa.Float, nullable=False),
)

users = sa.Table(
    "users", meta,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("email", sa.String(254), nullable=False, unique=True),          # always lower case
    sa.Column("name", sa.String(120), nullable=False, server_default=""),
    sa.Column("password_hash", sa.String(300)),                               # empty for Google-only accounts
    sa.Column("google_sub", sa.String(64), unique=True),
    sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("disabled", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("last_seen", sa.Float),
)
sessions = sa.Table(
    "sessions", meta,
    sa.Column("token_hash", sa.String(64), primary_key=True),                 # sha256 of the cookie; the cookie itself is never stored
    sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("expires_at", sa.Float, nullable=False),
    sa.Column("ip", sa.String(64)),
    sa.Column("agent", sa.String(300)),
)
usage = sa.Table(                                                             # one row per AI call
    "usage", meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("t", sa.Float, nullable=False),
    sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),   # text sent to the AI
    sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),  # text it wrote back
    sa.Column("purpose", sa.String(60), nullable=False),
    sa.Column("creator", sa.String(80)),
    sa.Column("place", sa.String(200)),
    sa.Index("usage_user_t", "user_id", "t"),
)


email_ok = sa.Table(                                                          # addresses proven to belong to the account
    "email_ok", meta,
    sa.Column("user_id", sa.String(40), primary_key=True),                    # "_existing" marks that older accounts were counted
    sa.Column("verified_at", sa.Float, nullable=False),
)
email_tokens = sa.Table(                                                      # one-time links: verify an email, reset a password
    "email_tokens", meta,
    sa.Column("token_hash", sa.String(64), primary_key=True),                 # sha256 of the link token; the token itself is never stored
    sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("purpose", sa.String(10), nullable=False),                      # verify | reset
    sa.Column("expires_at", sa.Float, nullable=False),
    sa.Column("used_at", sa.Float),
)


feedback = sa.Table(                                                          # bug reports and ideas, read by admins
    "feedback", meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="SET NULL")),
    sa.Column("t", sa.Float, nullable=False, index=True),
    sa.Column("kind", sa.String(10), nullable=False),                         # bug | idea | other
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("page", sa.String(200), nullable=False, server_default=""),     # where in the app they were
    sa.Column("context", sa.String(1000), nullable=False, server_default=""), # e.g. the error they were looking at
    sa.Column("status", sa.String(10), nullable=False, server_default="new", index=True),  # new | seen | done
    sa.Column("updated_at", sa.Float, nullable=False),
)
notices = sa.Table(                                                           # the bell: things worth telling someone about
    "notices", meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("t", sa.Float, nullable=False),
    sa.Column("kind", sa.String(20), nullable=False),                         # live | clips | post | account | feedback
    sa.Column("title", sa.String(160), nullable=False),
    sa.Column("body", sa.String(400), nullable=False, server_default=""),
    sa.Column("link", sa.String(200), nullable=False, server_default=""),
    sa.Column("ref", sa.String(80)),                                          # set when it must only be sent once
    sa.Column("read_at", sa.Float),
    sa.Index("notices_user_t", "user_id", "t"),
)


admin_log = sa.Table(                                                         # what admins did, never edited
    "admin_log", meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("t", sa.Float, nullable=False, index=True),
    sa.Column("admin_id", sa.String(40), nullable=False),
    sa.Column("action", sa.String(30), nullable=False),                       # disable | enable | delete | role:admin | role:user
    sa.Column("target_id", sa.String(40)),
    sa.Column("detail", sa.String(300), nullable=False, server_default=""),
)


def init():
    meta.create_all(engine)
    columns = {c["name"] for c in sa.inspect(engine).get_columns("creator_branding")}
    additions = {"second_logo_asset_path": "VARCHAR(100) NOT NULL DEFAULT ''",
                 "second_enabled": "BOOLEAN NOT NULL DEFAULT FALSE",
                 "second_position": "VARCHAR(20) NOT NULL DEFAULT 'bottom_right'",
                 "second_width": "INTEGER NOT NULL DEFAULT 15",
                 "x": "INTEGER NOT NULL DEFAULT 50", "y": "INTEGER NOT NULL DEFAULT 50",
                 "second_x": "INTEGER NOT NULL DEFAULT 50", "second_y": "INTEGER NOT NULL DEFAULT 50",
                 "remove_black": "BOOLEAN NOT NULL DEFAULT FALSE", "second_remove_black": "BOOLEAN NOT NULL DEFAULT FALSE"}
    with engine.begin() as conn:
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(sa.text(f"ALTER TABLE creator_branding ADD COLUMN {name} {definition}"))


def now() -> float:
    return time.time()
