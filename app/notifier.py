"""
Notification service for sending alerts via email and Telegram
"""
import smtplib
import ssl
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, List
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import NotificationSettings, NotificationRecipient, MonitoredURL

logger = logging.getLogger(__name__)


async def get_notification_settings(db: AsyncSession) -> Optional[NotificationSettings]:
    """Get notification settings from database"""
    result = await db.execute(select(NotificationSettings).limit(1))
    return result.scalar_one_or_none()


async def get_active_recipients(db: AsyncSession, channel: str = None) -> List[NotificationRecipient]:
    """Get active notification recipients, optionally filtered by channel"""
    query = select(NotificationRecipient).where(NotificationRecipient.is_active == True)
    if channel:
        query = query.where(NotificationRecipient.channel == channel)
    result = await db.execute(query)
    return result.scalars().all()


async def send_email(
    settings: NotificationSettings,
    to_email: str,
    subject: str,
    body: str
) -> bool:
    """Send email notification"""
    if not settings.smtp_host or not settings.smtp_from_email:
        logger.warning("SMTP settings not configured")
        return False
    
    try:
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = settings.smtp_from_email
        message["To"] = to_email
        
        # Create HTML version
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px; background-color: #f5f5f5;">
            <div style="max-width: 600px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
                <h2 style="color: #6366f1;">📡 URL Monitor Alert</h2>
                <div style="white-space: pre-line;">{body}</div>
                <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                <p style="color: #888; font-size: 12px;">Sent from URL Monitor System</p>
            </div>
        </body>
        </html>
        """
        
        part1 = MIMEText(body, "plain")
        part2 = MIMEText(html_body, "html")
        message.attach(part1)
        message.attach(part2)
        
        context = ssl.create_default_context()
        
        if settings.smtp_use_tls:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                server.starttls(context=context)
                if settings.smtp_username and settings.smtp_password:
                    server.login(settings.smtp_username, settings.smtp_password)
                server.sendmail(settings.smtp_from_email, to_email, message.as_string())
        else:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context) as server:
                if settings.smtp_username and settings.smtp_password:
                    server.login(settings.smtp_username, settings.smtp_password)
                server.sendmail(settings.smtp_from_email, to_email, message.as_string())
        
        logger.info(f"Email sent to {to_email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False


async def send_telegram(
    settings: NotificationSettings,
    chat_id: str,
    message: str
) -> dict:
    """Send Telegram notification. Returns dict with success status and error if any."""
    if not settings.telegram_bot_token:
        return {"success": False, "error": "Telegram bot token не настроен"}
    
    try:
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            })
            
            if response.status_code == 200:
                logger.info(f"Telegram message sent to {chat_id}")
                return {"success": True}
            else:
                # Parse Telegram error
                try:
                    error_data = response.json()
                    error_desc = error_data.get("description", "Unknown error")
                    error_code = error_data.get("error_code", response.status_code)
                    
                    # User-friendly error messages
                    if "chat not found" in error_desc.lower():
                        error_msg = f"Chat ID {chat_id} не найден. Убедитесь, что вы написали боту /start"
                    elif "bot was blocked" in error_desc.lower():
                        error_msg = "Пользователь заблокировал бота"
                    elif "unauthorized" in error_desc.lower():
                        error_msg = "Неверный Bot Token"
                    else:
                        error_msg = f"Telegram API ошибка: {error_desc}"
                    
                    logger.error(f"Telegram API error [{error_code}]: {error_desc}")
                    return {"success": False, "error": error_msg}
                except:
                    logger.error(f"Telegram API error: {response.text}")
                    return {"success": False, "error": f"Ошибка API: {response.status_code}"}
                
    except httpx.TimeoutException:
        logger.error(f"Telegram timeout for {chat_id}")
        return {"success": False, "error": "Таймаут при отправке (Telegram не отвечает)"}
    except Exception as e:
        logger.error(f"Failed to send Telegram message to {chat_id}: {e}")
        return {"success": False, "error": f"Ошибка: {str(e)}"}


def format_error_message(url: MonitoredURL, is_recovery: bool = False, is_regular_check: bool = False) -> tuple:
    """Format notification message for error, recovery, or regular check"""
    if is_regular_check:
        # Regular check - show actual status
        status_code = url.last_status_code
        if status_code and 200 <= status_code < 300:
            subject = f"✅ OK: {url.name or url.url}"
            emoji = "✅"
            status = "OK"
        elif status_code and 300 <= status_code < 400:
            subject = f"↪️ REDIRECT: {url.name or url.url}"
            emoji = "↪️"
            status = "REDIRECT"
        elif status_code and status_code >= 400:
            subject = f"🚨 ERROR: {url.name or url.url}"
            emoji = "🚨"
            status = "ОШИБКА"
        else:
            subject = f"⚠️ CHECK: {url.name or url.url}"
            emoji = "⚠️"
            status = "ПРОВЕРКА"
    elif is_recovery:
        subject = f"✅ RECOVERED: {url.name or url.url}"
        emoji = "✅"
        status = "ВОССТАНОВЛЕНО"
    else:
        subject = f"🚨 ALERT: {url.name or url.url}"
        emoji = "🚨"
        status = "ОШИБКА"
    
    body = f"""
{emoji} <b>{status}</b>

<b>URL:</b> {url.url}
{f'<b>Название:</b> {url.name}' if url.name else ''}
<b>Статус код:</b> {url.last_status_code or 'N/A'}
<b>Время отклика:</b> {url.last_response_time or 'N/A'} ms
{f'<b>Ошибка:</b> {url.last_error}' if url.last_error else ''}
{f'<b>Финальный URL:</b> {url.last_final_url}' if url.last_final_url and url.last_final_url != url.url else ''}

<i>Время: {url.last_check.strftime('%d.%m.%Y %H:%M:%S') if url.last_check else 'N/A'}</i>
"""
    
    # Plain text version for email
    plain_body = body.replace('<b>', '').replace('</b>', '').replace('<i>', '').replace('</i>', '')
    
    return subject, plain_body, body


async def send_notification(
    db: AsyncSession,
    url: MonitoredURL,
    is_recovery: bool = False,
    force: bool = False,
    is_regular_check: bool = False
) -> dict:
    """Send notification to all active recipients"""
    settings = await get_notification_settings(db)
    
    if not settings:
        return {"success": False, "error": "Notification settings not configured"}
    
    # Check if we should send notification
    if not force:
        if is_recovery and not settings.notify_on_recovery:
            return {"success": True, "skipped": True, "reason": "Recovery notifications disabled"}
        if not is_recovery and not settings.notify_on_error:
            return {"success": True, "skipped": True, "reason": "Error notifications disabled"}
    
    subject, plain_body, html_body = format_error_message(url, is_recovery, is_regular_check)
    
    results = {"email": [], "telegram": []}
    
    # Send to email recipients
    email_recipients = await get_active_recipients(db, "email")
    for recipient in email_recipients:
        success = await send_email(settings, recipient.address, subject, plain_body)
        results["email"].append({
            "address": recipient.address,
            "success": success
        })
    
    # Send to Telegram recipients
    telegram_recipients = await get_active_recipients(db, "telegram")
    for recipient in telegram_recipients:
        result = await send_telegram(settings, recipient.address, html_body)
        results["telegram"].append({
            "address": recipient.address,
            "success": result.get("success", False),
            "error": result.get("error")
        })
    
    return {
        "success": True,
        "results": results
    }


async def send_test_notification(
    db: AsyncSession,
    channel: str,
    address: str
) -> dict:
    """Send test notification to a specific address"""
    settings = await get_notification_settings(db)
    
    if not settings:
        return {"success": False, "error": "Настройки уведомлений не сконфигурированы"}
    
    test_message = """
📡 <b>Тестовое уведомление</b>

Это тестовое сообщение от URL Monitor.
Если вы его получили, уведомления настроены правильно!
"""
    
    test_plain = test_message.replace('<b>', '').replace('</b>', '')
    
    if channel == "email":
        success = await send_email(
            settings,
            address,
            "📡 URL Monitor - Test Notification",
            test_plain
        )
        if success:
            return {"success": True}
        else:
            return {"success": False, "error": "Не удалось отправить email. Проверьте настройки SMTP."}
    elif channel == "telegram":
        result = await send_telegram(settings, address, test_message)
        return result
    else:
        return {"success": False, "error": f"Неизвестный канал: {channel}"}

