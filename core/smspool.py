import aiohttp
import asyncio
import logging
from typing import Optional, Tuple, Dict

logger = logging.getLogger("smspool")

# Real SMSPool API — all POST, form-data, JSON responses
# Base: https://api.smspool.net/
#
# Endpoints used:
#   POST /purchase/sms   — buy number
#   POST /sms/check      — poll for OTP (status: 1=pending, 3=complete, 6=refunded)
#   POST /sms/cancel     — cancel + auto-refund
#   POST /request/active — list active orders (uses order_code not order_id)
#
# Auth: param "key" = your API key (32-char string)
# Responses: always JSON

SMSPOOL_BASE = "https://api.smspool.net"

class SMSPool:
    """
    Unified SMSPool client used by GmailFactory.
    Methods accept an optional aiohttp session (to allow GmailFactory's session reuse).
    All methods return consistent shapes (dicts or primitives).
    """

    def __init__(self, api_key: str, cfg: dict):
        self.api_key = api_key
        self.country = cfg.get("country", 8)
        self.service = cfg.get("service", "google")
        self.max_reuse = cfg.get("max_reuse_per_number", 5)
        self.poll_interval = cfg.get("poll_interval_seconds", 5)
        self.poll_timeout = cfg.get("poll_timeout_seconds", 150)

    async def _post(self, session: aiohttp.ClientSession, endpoint: str, params: dict) -> dict:
        params = dict(params)
        params["key"] = self.api_key
        url = f"{SMSPOOL_BASE}{endpoint}"
        try:
            async with session.post(url, data=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
                return await r.json(content_type=None)
        except Exception as e:
            logger.error(f"SMSPool [{endpoint}] error: {e}")
            return {}

    async def buy_number(self, session: Optional[aiohttp.ClientSession] = None, country: Optional[int] = None) -> Optional[Dict[str, str]]:
        """
        Buy a phone number. Returns dict: {"order_id": ..., "number": ..., "cc": ...} or None.
        If session is None, creates a temporary session for the call.
        """
        params = {"country": str(country or self.country), "service": str(self.service)}
        created_session = False
        if session is None:
            session = aiohttp.ClientSession()
            created_session = True
        try:
            data = await self._post(session, "/purchase/sms", params)
            if data.get("success") == 1:
                order_id = str(data.get("order_id"))
                number = str(data.get("phonenumber") or data.get("number") or "")
                cc = data.get("cc") or data.get("country") or ""
                logger.info(f"SMSPool: Bought +{number} (order: {order_id})")
                return {"order_id": order_id, "number": number, "cc": cc}
            else:
                logger.warning(f"SMSPool purchase failed: {data}")
                return None
        finally:
            if created_session:
                await session.close()

    async def poll_sms(self, session: aiohttp.ClientSession, order_id: str) -> Optional[str]:
        """
        Poll /sms/check until OTP arrives or timeout.
        Expects a session provided (polling is usually called repeatedly).
        """
        elapsed = 0
        while elapsed < self.poll_timeout:
            try:
                data = await self._post(session, "/sms/check", {"orderid": order_id})
            except Exception:
                data = {}
            status = data.get("status")
            if status == 3:
                otp = str(data.get("sms", "")).strip()
                if otp:
                    logger.info(f"SMSPool OTP received for {order_id}: {otp}")
                    return otp
            elif status == 6:
                logger.warning(f"SMSPool order {order_id} refunded/closed")
                return None
            # status == 1 pending or any other state -> continue polling
            await asyncio.sleep(self.poll_interval)
            elapsed += self.poll_interval
        logger.warning(f"SMSPool OTP timeout for {order_id} after {self.poll_timeout}s")
        return None

    async def cancel_order(self, session: Optional[aiohttp.ClientSession], order_id: str) -> bool:
        """
        Cancel an order. If session is None, create a temporary one.
        """
        created_session = False
        if session is None:
            session = aiohttp.ClientSession()
            created_session = True
        try:
            data = await self._post(session, "/sms/cancel", {"orderid": order_id})
            success = data.get("success") == 1
            if success:
                logger.info(f"SMSPool: Cancelled order {order_id}")
            else:
                logger.warning(f"SMSPool cancel failed for {order_id}: {data}")
            return success
        finally:
            if created_session:
                await session.close()
