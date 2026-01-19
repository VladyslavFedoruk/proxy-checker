import os
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import User, UserRole

# JWT Configuration (читаем SECRET_KEY из переменной окружения)
SECRET_KEY = os.getenv("SECRET_KEY", "proxy-checker-secret-key-change-in-production-2024")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Security scheme
security = HTTPBearer(auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    # Truncate to 72 bytes for bcrypt compatibility
    truncated = plain_password.encode('utf-8')[:72].decode('utf-8', errors='ignore')
    return pwd_context.verify(truncated, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password (bcrypt truncates to 72 bytes)"""
    # Truncate to 72 bytes for bcrypt compatibility
    return pwd_context.hash(password.encode('utf-8')[:72].decode('utf-8', errors='ignore'))


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    """Get user by username"""
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def authenticate_user(db: AsyncSession, username: str, password: str) -> Optional[User]:
    """Authenticate user with username and password"""
    user = await get_user_by_username(db, username)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        return None
    return user


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Get current authenticated user from JWT token"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Check for token in Authorization header
    token = None
    if credentials:
        token = credentials.credentials
    
    # Also check for token in cookies (for browser requests)
    if not token:
        token = request.cookies.get("access_token")
    
    if not token:
        raise credentials_exception
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = await get_user_by_username(db, username)
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise credentials_exception
    
    return user


async def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """Get current user if authenticated, None otherwise"""
    try:
        return await get_current_user(request, credentials, db)
    except HTTPException:
        return None


async def require_superadmin(current_user: User = Depends(get_current_user)) -> User:
    """Require superadmin role"""
    if current_user.role != UserRole.SUPERADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required"
        )
    return current_user


async def require_editor_or_above(current_user: User = Depends(get_current_user)) -> User:
    """Require editor or superadmin role"""
    if current_user.role not in [UserRole.SUPERADMIN.value, UserRole.EDITOR.value]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Editor access required"
        )
    return current_user


# Superadmin credentials
SUPERADMIN_USERNAME = "main-admin"
SUPERADMIN_PASSWORD = "£W\"'71tvg\\4mZS1ohX"


async def create_superadmin_if_not_exists(db: AsyncSession) -> None:
    """Create superadmin user if not exists"""
    result = await db.execute(
        select(User).where(User.role == UserRole.SUPERADMIN.value)
    )
    superadmin = result.scalar_one_or_none()
    
    if not superadmin:
        hashed_password = get_password_hash(SUPERADMIN_PASSWORD)
        superadmin = User(
            username=SUPERADMIN_USERNAME,
            hashed_password=hashed_password,
            role=UserRole.SUPERADMIN.value,
            is_active=True
        )
        db.add(superadmin)
        await db.commit()
        print(f"✅ Superadmin created: {SUPERADMIN_USERNAME}")

