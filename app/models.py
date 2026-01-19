from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, Text, Enum as SQLEnum
from sqlalchemy.orm import relationship
from datetime import datetime
from enum import Enum
from app.database import Base


class NotificationChannel(str, Enum):
    EMAIL = "email"
    TELEGRAM = "telegram"


class UserRole(str, Enum):
    SUPERADMIN = "superadmin"
    EDITOR = "editor"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), default=UserRole.EDITOR.value)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    created_by = relationship("User", remote_side=[id], backref="created_users")


class Proxy(Base):
    __tablename__ = "proxies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    protocol = Column(String(10), default="http")  # http, https, socks5
    username = Column(String(100), nullable=True)
    password = Column(String(100), nullable=True)
    geo = Column(String(50), nullable=True)  # Country code: US, DE, RU, etc.
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def get_proxy_url(self):
        if self.username and self.password:
            return f"{self.protocol}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.protocol}://{self.host}:{self.port}"


class MonitoredURL(Base):
    __tablename__ = "monitored_urls"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String(2048), nullable=False)
    referral_url = Column(String(2048), nullable=True)  # URL где размещена рефка
    name = Column(String(200), nullable=True)
    proxy_id = Column(Integer, ForeignKey("proxies.id"), nullable=True)
    check_interval = Column(Integer, default=60)  # seconds
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_check = Column(DateTime, nullable=True)
    last_status_code = Column(Integer, nullable=True)
    last_response_time = Column(Integer, nullable=True)  # milliseconds
    last_error = Column(Text, nullable=True)
    last_final_url = Column(String(2048), nullable=True)  # URL after redirects
    last_redirect_count = Column(Integer, default=0)
    last_redirect_code = Column(Integer, nullable=True)  # Код первого редиректа (301, 302, 307, 308)

    proxy = relationship("Proxy", backref="monitored_urls")
    checks = relationship("URLCheck", back_populates="monitored_url", order_by="desc(URLCheck.checked_at)", cascade="all, delete-orphan")


class URLCheck(Base):
    __tablename__ = "url_checks"

    id = Column(Integer, primary_key=True, index=True)
    monitored_url_id = Column(Integer, ForeignKey("monitored_urls.id"), nullable=False)
    status_code = Column(Integer, nullable=True)
    response_time = Column(Integer, nullable=True)  # milliseconds
    error_message = Column(Text, nullable=True)
    checked_at = Column(DateTime, default=datetime.utcnow)

    monitored_url = relationship("MonitoredURL", back_populates="checks")


class NotificationSettings(Base):
    """Глобальные настройки уведомлений (SMTP, Telegram bot)"""
    __tablename__ = "notification_settings"

    id = Column(Integer, primary_key=True, index=True)
    
    # SMTP настройки для email
    smtp_host = Column(String(255), nullable=True)
    smtp_port = Column(Integer, default=587)
    smtp_username = Column(String(255), nullable=True)
    smtp_password = Column(String(255), nullable=True)
    smtp_from_email = Column(String(255), nullable=True)
    smtp_use_tls = Column(Boolean, default=True)
    
    # Telegram настройки
    telegram_bot_token = Column(String(255), nullable=True)
    
    # Общие настройки
    notify_on_error = Column(Boolean, default=True)  # Уведомлять при ошибках
    notify_on_recovery = Column(Boolean, default=True)  # Уведомлять при восстановлении
    notify_on_status_change = Column(Boolean, default=False)  # Уведомлять при любом изменении статуса
    notify_on_every_check = Column(Boolean, default=False)  # Уведомлять при каждой проверке
    
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class NotificationRecipient(Base):
    """Получатели уведомлений"""
    __tablename__ = "notification_recipients"

    id = Column(Integer, primary_key=True, index=True)
    channel = Column(String(20), nullable=False)  # email или telegram
    address = Column(String(255), nullable=False)  # email адрес или telegram chat_id
    name = Column(String(100), nullable=True)  # Имя для отображения
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

