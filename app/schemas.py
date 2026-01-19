from pydantic import BaseModel, HttpUrl
from datetime import datetime
from typing import Optional, List


# Proxy schemas
class ProxyBase(BaseModel):
    name: str
    host: str
    port: int
    protocol: str = "http"
    username: Optional[str] = None
    password: Optional[str] = None
    geo: Optional[str] = None
    is_active: bool = True


class ProxyCreate(ProxyBase):
    pass


class ProxyUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    protocol: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    geo: Optional[str] = None
    is_active: Optional[bool] = None


class ProxyResponse(ProxyBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


# MonitoredURL schemas
class MonitoredURLBase(BaseModel):
    url: str
    referral_url: Optional[str] = None  # URL где размещена рефка
    name: Optional[str] = None
    proxy_id: Optional[int] = None
    check_interval: int = 60
    is_active: bool = True


class MonitoredURLCreate(MonitoredURLBase):
    pass


class MonitoredURLUpdate(BaseModel):
    url: Optional[str] = None
    referral_url: Optional[str] = None
    name: Optional[str] = None
    proxy_id: Optional[int] = None
    check_interval: Optional[int] = None
    is_active: Optional[bool] = None


class MonitoredURLResponse(MonitoredURLBase):
    id: int
    referral_url: Optional[str] = None
    created_at: datetime
    last_check: Optional[datetime] = None
    last_status_code: Optional[int] = None
    last_response_time: Optional[int] = None
    last_error: Optional[str] = None
    last_final_url: Optional[str] = None
    last_redirect_count: Optional[int] = 0
    last_redirect_code: Optional[int] = None
    proxy: Optional[ProxyResponse] = None

    class Config:
        from_attributes = True


# URLCheck schemas
class URLCheckResponse(BaseModel):
    id: int
    monitored_url_id: int
    status_code: Optional[int] = None
    response_time: Optional[int] = None
    error_message: Optional[str] = None
    checked_at: datetime

    class Config:
        from_attributes = True


class CheckResult(BaseModel):
    url_id: int
    url: str
    status_code: Optional[int] = None
    response_time: Optional[int] = None
    error: Optional[str] = None
    proxy_geo: Optional[str] = None


# Notification Settings schemas
class NotificationSettingsBase(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_use_tls: bool = True
    telegram_bot_token: Optional[str] = None
    notify_on_error: bool = True
    notify_on_recovery: bool = True
    notify_on_status_change: bool = False
    notify_on_every_check: bool = False


class NotificationSettingsUpdate(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_use_tls: Optional[bool] = None
    telegram_bot_token: Optional[str] = None
    notify_on_error: Optional[bool] = None
    notify_on_recovery: Optional[bool] = None
    notify_on_status_change: Optional[bool] = None
    notify_on_every_check: Optional[bool] = None


class NotificationSettingsResponse(NotificationSettingsBase):
    id: int
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Notification Recipient schemas
class NotificationRecipientBase(BaseModel):
    channel: str  # "email" or "telegram"
    address: str  # email address or telegram chat_id
    name: Optional[str] = None
    is_active: bool = True


class NotificationRecipientCreate(NotificationRecipientBase):
    pass


class NotificationRecipientUpdate(BaseModel):
    channel: Optional[str] = None
    address: Optional[str] = None
    name: Optional[str] = None
    is_active: Optional[bool] = None


class NotificationRecipientResponse(NotificationRecipientBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


# Test notification schema
class TestNotificationRequest(BaseModel):
    channel: str  # "email" or "telegram"
    address: str  # email address or telegram chat_id


# ==================== Auth schemas ====================

class LoginRequest(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserBase(BaseModel):
    username: str
    email: Optional[str] = None
    role: str = "editor"
    is_active: bool = True


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserPasswordUpdate(BaseModel):
    password: str


class UserResponse(UserBase):
    id: int
    created_at: datetime
    created_by_id: Optional[int] = None

    class Config:
        from_attributes = True

