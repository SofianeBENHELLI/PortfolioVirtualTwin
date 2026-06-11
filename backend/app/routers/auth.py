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


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"user_id": user.id, "email": user.email}
