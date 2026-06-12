from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import audit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import create_token, get_current_user, hash_password, verify_password
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str


@router.post("/register")
def register(creds: Credentials, db: Session = Depends(get_db)):
    n_users = db.scalar(select(func.count(User.id))) or 0
    if n_users >= get_settings().max_users:
        raise HTTPException(403, "User limit reached (10)")
    if db.scalar(select(User).where(User.email == creds.email)):
        raise HTTPException(409, "Email already registered")
    if len(creds.password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters")
    user = User(email=creds.email, password_hash=hash_password(creds.password))
    db.add(user)
    db.flush()
    audit(db, "user.registered", user_id=user.id, payload={"email": creds.email})
    db.commit()
    return {"token": create_token(user.id), "user_id": user.id, "email": user.email}


@router.post("/login")
def login(creds: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == creds.email))
    if user is None or not verify_password(creds.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    audit(db, "user.login", user_id=user.id)
    db.commit()
    return {"token": create_token(user.id), "user_id": user.id, "email": user.email}


class OpenAIKeyUpdate(BaseModel):
    api_key: str  # empty string clears the key


def _key_status(user: User, db: Session) -> dict:
    from app.agents.llm import resolve_openai_key
    from app.core.crypto import decrypt_secret

    personal = decrypt_secret(user.openai_api_key_enc) if user.openai_api_key_enc else None
    key, source = resolve_openai_key(db, user.id)
    return {
        "has_personal_key": bool(personal),
        "personal_key_hint": f"…{personal[-4:]}" if personal else None,
        "llm_available": bool(key),
        "key_source": source,  # personal | shared | ''
    }


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"user_id": user.id, "email": user.email, **_key_status(user, db)}


@router.post("/logout")
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """JWTs are stateless — the client discards the token; this records the event."""
    audit(db, "user.logout", user_id=user.id)
    db.commit()
    return {"ok": True}


@router.put("/me/openai-key")
def set_openai_key(payload: OpenAIKeyUpdate, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Store (or clear, with empty string) the user's personal OpenAI key — encrypted at
    rest. Agents triggered by this user run and bill on this key."""
    from app.core.crypto import encrypt_secret

    key = payload.api_key.strip()
    if key:
        if len(key) < 20 or " " in key:
            raise HTTPException(422, "That doesn't look like an OpenAI API key")
        user.openai_api_key_enc = encrypt_secret(key)
        audit(db, "user.openai_key_set", user_id=user.id, payload={"hint": f"…{key[-4:]}"})
    else:
        user.openai_api_key_enc = ""
        audit(db, "user.openai_key_cleared", user_id=user.id)
    db.commit()
    return _key_status(user, db)
