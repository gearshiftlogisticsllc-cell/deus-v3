from typing import Optional, List
import hashlib
import time
import bcrypt as _bcrypt
from sqlalchemy import select, delete
from app.repositories.base import BaseRepository
from app.models.user import User, Session as UserSession


class UserRepository(BaseRepository[User]):
    def __init__(self, session):
        super().__init__(User, session)

    def get_by_username(self, username: str) -> Optional[User]:
        stmt = select(User).where(User.username == username)
        return self.session.execute(stmt).scalar_one_or_none()

    def authenticate(self, username: str, password: str) -> Optional[User]:
        user = self.get_by_username(username)
        if not user:
            return None
        stored = user.password_hash
        # Try bcrypt first, fall back to legacy SHA-256
        if stored.startswith("$2"):
            try:
                if not _bcrypt.checkpw(password.encode(), stored.encode()):
                    return None
            except Exception:
                return None
        else:
            hashed = hashlib.sha256((password + user.salt).encode()).hexdigest()
            if hashed != stored:
                return None
        return user

    def create_session(self, user_id: int, expires_at: float) -> UserSession:
        import uuid
        token = str(uuid.uuid4())
        now = time.time()
        session = UserSession(token=token, user_id=user_id, created_at=now, expires_at=expires_at)
        self.session.add(session)
        self.session.flush()
        self.session.refresh(session)
        return session

    def get_session(self, token: str) -> Optional[UserSession]:
        stmt = select(UserSession).where(UserSession.token == token)
        return self.session.execute(stmt).scalar_one_or_none()

    def delete_expired_sessions(self):
        now = time.time()
        stmt = delete(UserSession).where(UserSession.expires_at < now)
        self.session.execute(stmt)
        self.session.flush()

    def cleanup_sessions(self):
        now = time.time()
        cutoff = now - 86400 * 7
        stmt = delete(UserSession).where(
            (UserSession.expires_at < now) | (UserSession.created_at < cutoff)
        )
        self.session.execute(stmt)
        self.session.flush()
