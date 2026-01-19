import httpx
import time
import logging
from datetime import datetime
from urllib.parse import quote
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import MonitoredURL, URLCheck, Proxy
from app.notifier import send_notification, get_notification_settings

logger = logging.getLogger(__name__)


def get_proxy_url_for_httpx(proxy: Proxy) -> str:
    """Format proxy URL for httpx library"""
    # URL-encode username and password to handle special characters
    username = quote(proxy.username, safe='') if proxy.username else None
    password = quote(proxy.password, safe='') if proxy.password else None
    
    # For SOCKS5 proxies
    if proxy.protocol == "socks5":
        if username and password:
            return f"socks5://{username}:{password}@{proxy.host}:{proxy.port}"
        return f"socks5://{proxy.host}:{proxy.port}"
    
    # For HTTP/HTTPS proxies - always use http:// as the proxy protocol
    # The proxy itself communicates over HTTP, even when tunneling HTTPS
    if username and password:
        return f"http://{username}:{password}@{proxy.host}:{proxy.port}"
    return f"http://{proxy.host}:{proxy.port}"


async def check_url(url: str, proxy: Proxy = None, timeout: int = 30) -> dict:
    """Check a single URL and return result"""
    result = {
        "status_code": None,
        "response_time": None,
        "error": None,
        "final_url": None,
        "redirect_count": 0,
        "redirect_code": None  # Код первого редиректа
    }
    
    proxy_url = None
    if proxy:
        proxy_url = get_proxy_url_for_httpx(proxy)
        logger.info(f"Using proxy: {proxy.host}:{proxy.port} ({proxy.protocol})")
    
    start_time = time.time()
    
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url,
            timeout=timeout,
            follow_redirects=True,
            verify=False
        ) as client:
            response = await client.get(url)
            result["status_code"] = response.status_code
            result["response_time"] = int((time.time() - start_time) * 1000)
            # Track redirects
            result["redirect_count"] = len(response.history)
            if response.history:
                result["final_url"] = str(response.url)
                result["redirect_code"] = response.history[0].status_code  # Код первого редиректа
    except httpx.TimeoutException as e:
        result["error"] = "Timeout"
        result["response_time"] = int((time.time() - start_time) * 1000)
        logger.warning(f"Timeout checking {url}: {e}")
    except httpx.ProxyError as e:
        result["error"] = f"Proxy error: {str(e)[:100]}"
        result["response_time"] = int((time.time() - start_time) * 1000)
        logger.warning(f"Proxy error checking {url}: {e}")
    except httpx.ConnectError as e:
        result["error"] = f"Connection error: {str(e)[:100]}"
        result["response_time"] = int((time.time() - start_time) * 1000)
        logger.warning(f"Connection error checking {url}: {e}")
    except Exception as e:
        result["error"] = f"Error: {str(e)[:100]}"
        result["response_time"] = int((time.time() - start_time) * 1000)
        logger.warning(f"Error checking {url}: {e}")
    
    return result


async def check_monitored_url(db: AsyncSession, monitored_url: MonitoredURL) -> URLCheck:
    """Check a monitored URL and save result to database"""
    proxy = None
    if monitored_url.proxy_id:
        proxy = await db.get(Proxy, monitored_url.proxy_id)
    
    # Remember previous status for comparison
    previous_status = monitored_url.last_status_code
    previous_error = monitored_url.last_error
    
    result = await check_url(monitored_url.url, proxy)
    
    # Create check record
    url_check = URLCheck(
        monitored_url_id=monitored_url.id,
        status_code=result["status_code"],
        response_time=result["response_time"],
        error_message=result["error"],
        checked_at=datetime.utcnow()
    )
    db.add(url_check)
    
    # Update monitored URL
    monitored_url.last_check = datetime.utcnow()
    monitored_url.last_status_code = result["status_code"]
    monitored_url.last_response_time = result["response_time"]
    monitored_url.last_error = result["error"]
    monitored_url.last_final_url = result.get("final_url")
    monitored_url.last_redirect_count = result.get("redirect_count", 0)
    monitored_url.last_redirect_code = result.get("redirect_code")
    
    await db.commit()
    await db.refresh(url_check)
    
    # Check if we need to send notifications
    current_is_error = result["error"] or (result["status_code"] and result["status_code"] >= 400)
    previous_was_error = previous_error or (previous_status and previous_status >= 400)
    
    settings = await get_notification_settings(db)
    
    if settings:
        # Notify on every check (if enabled) - highest priority
        if settings.notify_on_every_check:
            try:
                await send_notification(db, monitored_url, is_recovery=False, force=True, is_regular_check=True)
                logger.info(f"Sent every-check notification for {monitored_url.url}")
            except Exception as e:
                logger.error(f"Failed to send every-check notification: {e}")
        # Notify on new error
        elif current_is_error and not previous_was_error:
            try:
                await send_notification(db, monitored_url, is_recovery=False)
                logger.info(f"Sent error notification for {monitored_url.url}")
            except Exception as e:
                logger.error(f"Failed to send error notification: {e}")
        
        # Notify on recovery
        elif not current_is_error and previous_was_error and previous_status is not None:
            try:
                await send_notification(db, monitored_url, is_recovery=True)
                logger.info(f"Sent recovery notification for {monitored_url.url}")
            except Exception as e:
                logger.error(f"Failed to send recovery notification: {e}")
        
        # Notify on any status change (if enabled)
        elif settings.notify_on_status_change and previous_status != result["status_code"] and previous_status is not None:
            try:
                await send_notification(db, monitored_url, is_recovery=False)
                logger.info(f"Sent status change notification for {monitored_url.url}")
            except Exception as e:
                logger.error(f"Failed to send status change notification: {e}")
    
    return url_check


async def check_all_active_urls(db: AsyncSession):
    """Check all active monitored URLs"""
    query = select(MonitoredURL).where(MonitoredURL.is_active == True)
    result = await db.execute(query)
    urls = result.scalars().all()
    
    results = []
    for url in urls:
        try:
            check = await check_monitored_url(db, url)
            results.append(check)
        except Exception as e:
            logger.error(f"Error checking URL {url.url}: {e}")
    
    return results

