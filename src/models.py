
import secrets

from datetime import datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def _local_str(dt):
    """created_at is stored in UTC. Convert it to the computer's own local
    timezone before showing it, so a scan made at 1:21 PM IST shows as
    1:21 PM instead of 07:51 AM."""
    if dt is None:
        return ""
    return dt.replace(tzinfo=timezone.utc).astimezone().strftime("%d %b %Y, %I:%M %p")


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Used by the browser extension (see /extension) to authenticate as this
    # user without a session cookie — a content script running on
    # linkedin.com can't share this app's cookie jar (different origin,
    # and LinkedIn's own CSP blocks the request unless it's made from the
    # extension's privileged background worker instead), so the extension
    # sends this as `Authorization: Bearer <token>` and login_manager's
    # request_loader (app.py) resolves it to this user for that request only.
    api_token = db.Column(db.String(64), unique=True, nullable=True, index=True)

    scans = db.relationship("Scan", backref="user", lazy="dynamic", cascade="all, delete-orphan")

    def set_password(self, raw_password: str):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    def ensure_api_token(self) -> str:
        if not self.api_token:
            self.api_token = secrets.token_urlsafe(32)
        return self.api_token


class Scan(db.Model):
    __tablename__ = "scans"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    title = db.Column(db.String(300), default="")
    label = db.Column(db.String(20), default="")       # "FRAUDULENT" | "LEGITIMATE" — denormalized for fast list rendering
    confidence = db.Column(db.Float, default=0.0)

    record = db.Column(db.JSON, nullable=False)         # submitted job-posting fields
    result = db.Column(db.JSON, nullable=False)         # /api/predict response
    meta = db.Column(db.JSON, nullable=True)             # {source_url, extraction_method, ...}
    verification = db.Column(db.JSON, nullable=True)     # /api/verify response, attached after the fact

    # The user's "was this prediction correct?" answer (one per scan).
    feedback = db.relationship("Feedback", backref="scan", uselist=False,
                               cascade="all, delete-orphan", passive_deletes=True)

    def to_summary(self):
        return {
            "id": self.id,
            "time": _local_str(self.created_at),
            "title": self.title,
            "label": self.label,
            "confidence": self.confidence,
        }

    def to_full(self):
        return {
            **self.to_summary(),
            "record": self.record,
            "result": self.result,
            "meta": self.meta,
            "verification": self.verification,
        }


class Feedback(db.Model):
    """One row per scan: did the user say the model's verdict was right?
    Stored in its own table (not inside Scan) so db.create_all() can add it
    to an existing database without altering the scans table. Kept so the
    model can be retrained later on real corrections: if is_correct is
    False, the true label is the opposite of the scan's predicted label."""
    __tablename__ = "feedback"

    id = db.Column(db.Integer, primary_key=True)
    scan_id = db.Column(db.Integer, db.ForeignKey("scans.id", ondelete="CASCADE"),
                        nullable=False, unique=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    is_correct = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))