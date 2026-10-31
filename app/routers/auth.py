"""Registration and login.

The login path deliberately does the same amount of work whether or not the email
exists -- a bcrypt verify against a dummy hash -- so response timing does not
reveal which accounts are real.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import current_user
from ..models import User
from ..ratelimit import SlidingWindowLimiter, client_key
from ..schemas import Token, UserCreate, UserLogin, UserOut
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

login_limiter = SlidingWindowLimiter(settings.rate_limit_auth_per_minute)
register_limiter = SlidingWindowLimiter(settings.rate_limit_auth_per_minute)

# A real bcrypt hash of a throwaway value. Verifying against this when the user
# does not exist keeps the timing of a failed login roughly constant.
_DUMMY_HASH = hash_password("markaz-timing-equaliser-not-a-real-password")


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, request: Request, db: Session = Depends(get_db)) -> Token:
    register_limiter.check(client_key(request))

    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Two concurrent registrations can both pass the SELECT above. The unique
        # constraint is the real guard; this just turns the race into a clean 409.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        ) from None

    db.refresh(user)
    token, expires_in = create_access_token(user.id)
    return Token(access_token=token, expires_in=expires_in, user=UserOut.model_validate(user))


@router.post("/login", response_model=Token)
def login(payload: UserLogin, request: Request, db: Session = Depends(get_db)) -> Token:
    login_limiter.check(client_key(request))

    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None:
        verify_password(payload.password, _DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect.",
        )

    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect.",
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled.")

    token, expires_in = create_access_token(user.id)
    return Token(access_token=token, expires_in=expires_in, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut.model_validate(user)
