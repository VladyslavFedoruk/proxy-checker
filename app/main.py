from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
import logging
import csv
import io

from app.database import get_db, init_db, async_session
from app.models import Proxy, MonitoredURL, URLCheck, NotificationSettings, NotificationRecipient, User, UserRole
from app.schemas import (
    ProxyCreate, ProxyUpdate, ProxyResponse,
    MonitoredURLCreate, MonitoredURLUpdate, MonitoredURLResponse,
    URLCheckResponse, CheckResult,
    NotificationSettingsUpdate, NotificationSettingsResponse,
    NotificationRecipientCreate, NotificationRecipientUpdate, NotificationRecipientResponse,
    TestNotificationRequest,
    LoginRequest, Token, UserCreate, UserUpdate, UserPasswordUpdate, UserResponse
)
from app.checker import check_monitored_url, check_all_active_urls
from app.notifier import send_test_notification
from app.auth import (
    authenticate_user, create_access_token, get_current_user, 
    get_current_user_optional, require_superadmin, require_editor_or_above,
    get_password_hash, ACCESS_TOKEN_EXPIRE_MINUTES
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def scheduled_check():
    """Background task to check URLs"""
    async with async_session() as db:
        now = datetime.utcnow()
        query = select(MonitoredURL).where(MonitoredURL.is_active == True)
        result = await db.execute(query)
        urls = result.scalars().all()
        
        for url in urls:
            should_check = False
            if url.last_check is None:
                should_check = True
            elif (now - url.last_check).total_seconds() >= url.check_interval:
                should_check = True
            
            if should_check:
                try:
                    await check_monitored_url(db, url)
                    logger.info(f"Checked URL: {url.url} - Status: {url.last_status_code}")
                except Exception as e:
                    logger.error(f"Error checking URL {url.url}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler.add_job(scheduled_check, 'interval', seconds=10, id='url_checker')
    scheduler.start()
    logger.info("Scheduler started")
    yield
    scheduler.shutdown()


app = FastAPI(title="URL Monitor Admin", lifespan=lifespan)

templates = Jinja2Templates(directory="app/templates")


# ==================== Pages ====================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ==================== Auth API ====================

@app.post("/api/auth/login", response_model=Token)
async def login(login_data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login and get access token"""
    user = await authenticate_user(db, login_data.username, login_data.password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Неверный логин или пароль"
        )
    
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return Token(access_token=access_token)


@app.get("/api/auth/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current authenticated user info"""
    return current_user


# ==================== User Management API (Superadmin only) ====================

@app.get("/api/users", response_model=list[UserResponse])
async def get_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Get all users (superadmin only)"""
    result = await db.execute(select(User).order_by(desc(User.created_at)))
    return result.scalars().all()


@app.post("/api/users", response_model=UserResponse)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Create new user (superadmin only)"""
    # Check if username exists
    existing = await db.execute(select(User).where(User.username == user_data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Пользователь с таким логином уже существует")
    
    # Check if email exists (if provided)
    if user_data.email:
        existing_email = await db.execute(select(User).where(User.email == user_data.email))
        if existing_email.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Пользователь с таким email уже существует")
    
    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hashed_password,
        role=user_data.role,
        is_active=user_data.is_active,
        created_by_id=current_user.id
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user


@app.put("/api/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Update user (superadmin only)"""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    # Prevent editing yourself to non-superadmin
    if user.id == current_user.id and user_data.role and user_data.role != UserRole.SUPERADMIN.value:
        raise HTTPException(status_code=400, detail="Нельзя понизить свою роль")
    
    update_data = user_data.model_dump(exclude_unset=True)
    
    # Check username uniqueness
    if "username" in update_data:
        existing = await db.execute(
            select(User).where(User.username == update_data["username"], User.id != user_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Пользователь с таким логином уже существует")
    
    for field, value in update_data.items():
        setattr(user, field, value)
    
    await db.commit()
    await db.refresh(user)
    return user


@app.put("/api/users/{user_id}/password")
async def update_user_password(
    user_id: int,
    password_data: UserPasswordUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Update user password (superadmin only)"""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    user.hashed_password = get_password_hash(password_data.password)
    await db.commit()
    return {"message": "Пароль успешно изменён"}


@app.delete("/api/users/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Delete user (superadmin only)"""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    await db.delete(user)
    await db.commit()
    return {"message": "Пользователь удалён"}


# ==================== Proxy API ====================

@app.get("/api/proxies", response_model=list[ProxyResponse])
async def get_proxies(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(select(Proxy).order_by(desc(Proxy.created_at)))
    return result.scalars().all()


@app.post("/api/proxies", response_model=ProxyResponse)
async def create_proxy(
    proxy: ProxyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor_or_above)
):
    db_proxy = Proxy(**proxy.model_dump())
    db.add(db_proxy)
    await db.commit()
    await db.refresh(db_proxy)
    return db_proxy


@app.get("/api/proxies/{proxy_id}", response_model=ProxyResponse)
async def get_proxy(
    proxy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    proxy = await db.get(Proxy, proxy_id)
    if not proxy:
        raise HTTPException(status_code=404, detail="Proxy not found")
    return proxy


@app.put("/api/proxies/{proxy_id}", response_model=ProxyResponse)
async def update_proxy(
    proxy_id: int,
    proxy: ProxyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor_or_above)
):
    db_proxy = await db.get(Proxy, proxy_id)
    if not db_proxy:
        raise HTTPException(status_code=404, detail="Proxy not found")
    
    update_data = proxy.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_proxy, field, value)
    
    await db.commit()
    await db.refresh(db_proxy)
    return db_proxy


@app.delete("/api/proxies/{proxy_id}")
async def delete_proxy(
    proxy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor_or_above)
):
    proxy = await db.get(Proxy, proxy_id)
    if not proxy:
        raise HTTPException(status_code=404, detail="Proxy not found")
    await db.delete(proxy)
    await db.commit()
    return {"message": "Proxy deleted"}


# ==================== Monitored URL API ====================

@app.get("/api/urls", response_model=list[MonitoredURLResponse])
async def get_urls(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(
        select(MonitoredURL)
        .options(selectinload(MonitoredURL.proxy))
        .order_by(desc(MonitoredURL.created_at))
    )
    return result.scalars().all()


@app.post("/api/urls", response_model=MonitoredURLResponse)
async def create_url(
    url: MonitoredURLCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor_or_above)
):
    db_url = MonitoredURL(**url.model_dump())
    db.add(db_url)
    await db.commit()
    await db.refresh(db_url)
    
    # Load proxy relationship
    result = await db.execute(
        select(MonitoredURL)
        .options(selectinload(MonitoredURL.proxy))
        .where(MonitoredURL.id == db_url.id)
    )
    return result.scalar_one()


@app.post("/api/urls/import-csv")
async def import_urls_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor_or_above)
):
    """
    Import URLs from CSV file.
    Expected columns: url, referral_url (optional: name, proxy_id, check_interval)
    """
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Файл должен быть в формате CSV")
    
    try:
        content = await file.read()
        # Try to decode with different encodings
        try:
            decoded = content.decode('utf-8')
        except UnicodeDecodeError:
            try:
                decoded = content.decode('utf-8-sig')  # UTF-8 with BOM
            except UnicodeDecodeError:
                decoded = content.decode('cp1251')  # Windows Cyrillic
        
        # Parse CSV
        reader = csv.DictReader(io.StringIO(decoded))
        
        imported = 0
        skipped = 0
        errors = []
        
        for row_num, row in enumerate(reader, start=2):  # Start from 2 (header is row 1)
            # Get URL - required field
            url = row.get('url', '').strip()
            if not url:
                skipped += 1
                errors.append(f"Строка {row_num}: пустой URL")
                continue
            
            # Check if URL already exists
            existing = await db.execute(
                select(MonitoredURL).where(MonitoredURL.url == url)
            )
            if existing.scalar_one_or_none():
                skipped += 1
                errors.append(f"Строка {row_num}: URL уже существует")
                continue
            
            # Get optional fields
            referral_url = row.get('referral_url', '').strip() or None
            name = row.get('name', '').strip() or None
            
            # Parse proxy_id
            proxy_id = None
            proxy_id_str = row.get('proxy_id', '').strip()
            if proxy_id_str:
                try:
                    proxy_id = int(proxy_id_str)
                    # Verify proxy exists
                    proxy = await db.get(Proxy, proxy_id)
                    if not proxy:
                        proxy_id = None
                except ValueError:
                    pass
            
            # Parse check_interval (default 60 seconds)
            check_interval = 60
            interval_str = row.get('check_interval', '').strip()
            if interval_str:
                try:
                    check_interval = max(10, int(interval_str))  # Minimum 10 seconds
                except ValueError:
                    pass
            
            # Create URL entry
            db_url = MonitoredURL(
                url=url,
                referral_url=referral_url,
                name=name,
                proxy_id=proxy_id,
                check_interval=check_interval,
                is_active=True
            )
            db.add(db_url)
            imported += 1
        
        await db.commit()
        
        return {
            "message": f"Импортировано {imported} URL, пропущено {skipped}",
            "imported": imported,
            "skipped": skipped,
            "errors": errors[:10]  # Return first 10 errors
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка обработки CSV: {str(e)}")


@app.get("/api/urls/{url_id}", response_model=MonitoredURLResponse)
async def get_url(
    url_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(
        select(MonitoredURL)
        .options(selectinload(MonitoredURL.proxy))
        .where(MonitoredURL.id == url_id)
    )
    url = result.scalar_one_or_none()
    if not url:
        raise HTTPException(status_code=404, detail="URL not found")
    return url


@app.put("/api/urls/{url_id}", response_model=MonitoredURLResponse)
async def update_url(
    url_id: int,
    url: MonitoredURLUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor_or_above)
):
    db_url = await db.get(MonitoredURL, url_id)
    if not db_url:
        raise HTTPException(status_code=404, detail="URL not found")
    
    update_data = url.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_url, field, value)
    
    await db.commit()
    
    # Load proxy relationship
    result = await db.execute(
        select(MonitoredURL)
        .options(selectinload(MonitoredURL.proxy))
        .where(MonitoredURL.id == db_url.id)
    )
    return result.scalar_one()


@app.delete("/api/urls/{url_id}")
async def delete_url(
    url_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor_or_above)
):
    url = await db.get(MonitoredURL, url_id)
    if not url:
        raise HTTPException(status_code=404, detail="URL not found")
    
    # First delete all related URL checks
    await db.execute(
        URLCheck.__table__.delete().where(URLCheck.monitored_url_id == url_id)
    )
    
    await db.delete(url)
    await db.commit()
    return {"message": "URL deleted"}


@app.post("/api/urls/{url_id}/check", response_model=CheckResult)
async def check_url_now(
    url_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Manually trigger a check for a specific URL"""
    result = await db.execute(
        select(MonitoredURL)
        .options(selectinload(MonitoredURL.proxy))
        .where(MonitoredURL.id == url_id)
    )
    url = result.scalar_one_or_none()
    if not url:
        raise HTTPException(status_code=404, detail="URL not found")
    
    check = await check_monitored_url(db, url)
    
    return CheckResult(
        url_id=url.id,
        url=url.url,
        status_code=check.status_code,
        response_time=check.response_time,
        error=check.error_message,
        proxy_geo=url.proxy.geo if url.proxy else None
    )


@app.get("/api/urls/{url_id}/history", response_model=list[URLCheckResponse])
async def get_url_history(
    url_id: int,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get check history for a specific URL"""
    result = await db.execute(
        select(URLCheck)
        .where(URLCheck.monitored_url_id == url_id)
        .order_by(desc(URLCheck.checked_at))
        .limit(limit)
    )
    return result.scalars().all()


@app.post("/api/urls/check-all")
async def check_all_urls_now(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Manually trigger a check for all active URLs"""
    result = await db.execute(
        select(MonitoredURL)
        .options(selectinload(MonitoredURL.proxy))
        .where(MonitoredURL.is_active == True)
    )
    urls = result.scalars().all()
    
    checked = 0
    for url in urls:
        try:
            await check_monitored_url(db, url)
            checked += 1
        except Exception as e:
            logger.error(f"Error checking URL {url.url}: {e}")
    
    return {"message": f"Проверено {checked} URL-ов", "checked": checked}


# ==================== Stats API ====================

@app.get("/api/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get overall statistics"""
    urls_result = await db.execute(select(MonitoredURL))
    urls = urls_result.scalars().all()
    
    total_urls = len(urls)
    active_urls = sum(1 for u in urls if u.is_active)
    urls_200 = sum(1 for u in urls if u.last_status_code == 200)
    urls_error = sum(1 for u in urls if u.last_status_code and u.last_status_code >= 400)
    
    proxies_result = await db.execute(select(Proxy))
    total_proxies = len(proxies_result.scalars().all())
    
    return {
        "total_urls": total_urls,
        "active_urls": active_urls,
        "urls_200": urls_200,
        "urls_error": urls_error,
        "total_proxies": total_proxies
    }


# ==================== Notification Settings API ====================

@app.get("/api/notifications/settings", response_model=NotificationSettingsResponse)
async def get_notification_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Get notification settings (superadmin only)"""
    result = await db.execute(select(NotificationSettings).limit(1))
    settings = result.scalar_one_or_none()
    
    if not settings:
        # Create default settings
        settings = NotificationSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    
    return settings


@app.put("/api/notifications/settings", response_model=NotificationSettingsResponse)
async def update_notification_settings(
    settings_update: NotificationSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Update notification settings (superadmin only)"""
    result = await db.execute(select(NotificationSettings).limit(1))
    settings = result.scalar_one_or_none()
    
    if not settings:
        settings = NotificationSettings()
        db.add(settings)
    
    update_data = settings_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(settings, field, value)
    
    await db.commit()
    await db.refresh(settings)
    return settings


# ==================== Notification Recipients API ====================

@app.get("/api/notifications/recipients", response_model=list[NotificationRecipientResponse])
async def get_notification_recipients(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Get all notification recipients (superadmin only)"""
    result = await db.execute(
        select(NotificationRecipient).order_by(desc(NotificationRecipient.created_at))
    )
    return result.scalars().all()


@app.post("/api/notifications/recipients", response_model=NotificationRecipientResponse)
async def create_notification_recipient(
    recipient: NotificationRecipientCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Create a new notification recipient (superadmin only)"""
    db_recipient = NotificationRecipient(**recipient.model_dump())
    db.add(db_recipient)
    await db.commit()
    await db.refresh(db_recipient)
    return db_recipient


@app.put("/api/notifications/recipients/{recipient_id}", response_model=NotificationRecipientResponse)
async def update_notification_recipient(
    recipient_id: int,
    recipient: NotificationRecipientUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Update a notification recipient (superadmin only)"""
    db_recipient = await db.get(NotificationRecipient, recipient_id)
    if not db_recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    
    update_data = recipient.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_recipient, field, value)
    
    await db.commit()
    await db.refresh(db_recipient)
    return db_recipient


@app.delete("/api/notifications/recipients/{recipient_id}")
async def delete_notification_recipient(
    recipient_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Delete a notification recipient (superadmin only)"""
    recipient = await db.get(NotificationRecipient, recipient_id)
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    await db.delete(recipient)
    await db.commit()
    return {"message": "Recipient deleted"}


@app.post("/api/notifications/test")
async def test_notification(
    request: TestNotificationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin)
):
    """Send a test notification (superadmin only)"""
    result = await send_test_notification(db, request.channel, request.address)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to send notification"))
    return {"message": "Test notification sent successfully"}

