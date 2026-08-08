import aiohttp
import asyncio
import logging
from typing import Optional, Tuple

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
    # *buys a stranger's voice for each account — uses it once, moves on*

    def __init__(self, api_key: str, cfg: dict):
        self.api_key = api_key
        self.country = cfg.get("country", "Indonesia")
        self.service = cfg.get("service", "google")
        self.max_reuse = cfg.get("max_reuse_per_number", 5)
        self.poll_interval = cfg.get("poll_interval_seconds", 5)
        self.poll_timeout = cfg.get("poll_timeout_seconds", 150)
        self.session: Optional[aiohttp.ClientSession] = None

    async def _post(self, endpoint: str, params: dict) -> dict:
        """POST with form-data. Key injected here so callers stay clean."""
        params["key"] = self.api_key
        url = f"{SMSPOOL_BASE}{endpoint}"
        try:
            async with self.session.post(
                url,
                data=params,  # form-data, not JSON
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                return await r.json(content_type=None)
        except Exception as e:
            logger.error(f"SMSPool [{endpoint}] error: {e}")
            return {}

    async def buy_number(self) -> Optional[Tuple[str, str]]:
        """
        Purchase a number.
        Returns (order_id, full_number_e164) or None.

        Success response:
          {"success": 1, "number": 628123456789, "order_id": "ABCDEFGH",
           "country": "Indonesia", "service": "google", "expires_in": 1200, ...}
        """
        data = await self._post("/purchase/sms", {
            "country": self.country,
            "service": self.service,
        })

        if data.get("success") == 1:
            order_id = str(data["order_id"])
            # "number" is the full E.164 number without "+"
            number = str(data["number"])
            logger.info(f"Bought +{number} (order={order_id}, expires_in={data.get('expires_in')}s)")
            return order_id, number

        err = data.get("type") or data.get("message") or str(data)
        logger.warning(f"buy_number failed: {err}")
        return None

    async def poll_sms(self, order_id: str) -> Optional[str]:
        """
        Poll /sms/check until OTP arrives or timeout.

        Status codes:
          1 = pending (no code yet)
          3 = complete (code in response["sms"])
          6 = refunded

        Returns the extracted OTP string or None.
        """
        elapsed = 0
        while elapsed < self.poll_timeout:
            await asyncio.sleep(self.poll_interval)
            elapsed += self.poll_interval

            data = await self._post("/sms/check", {"orderid": order_id})

            status = data.get("status")

            if status == 3:
                # "sms" = extracted code, "full_sms" = raw message
                otp = str(data.get("sms", "")).strip()
                if otp and otp != "0":
                    logger.info(f"OTP received for {order_id}: {otp}")
                    return otp
                # Sometimes status=3 but sms field is empty — keep polling
                logger.debug(f"Status 3 but empty sms field for {order_id}, continuing...")

            elif status == 6:
                logger.warning(f"Order {order_id} was refunded by SMSPool")
                return None

            elif status == 1:
                logger.debug(f"Polling {order_id} [{elapsed}s elapsed] — pending")

            else:
                logger.debug(f"Polling {order_id} [{elapsed}s] — status={status} data={data}")

        logger.warning(f"OTP timeout ({self.poll_timeout}s) for order {order_id}")
        return None

    async def cancel(self, order_id: str) -> bool:
        """
        Cancel an order. Auto-refunds if no code received.
        Note: time-locked for a few seconds after purchase — retry on "cannot be cancelled yet".
        """
        for attempt in range(3):
            data = await self._post("/sms/cancel", {"orderid": order_id})
            if data.get("success") == 1:
                logger.info(f"Cancelled order {order_id}")
                return True
            msg = data.get("message", "")
            if "cannot be cancelled yet" in msg.lower():
                logger.debug(f"Cancel time-locked for {order_id}, retrying in 3s...")
                await asyncio.sleep(3)
            else:
                logger.warning(f"Cancel failed for {order_id}: {msg}")
                return False
        return False

    async def get_active_orders(self) -> list:
        """Returns all active orders. Uses order_code field (not order_id)."""
        data = await self._post("/request/active", {})
        if isinstance(data, list):
            return data
        return []

    async def get_balance(self) -> float:
        """Check account balance."""
        data = await self._post("/request/balance", {})
        try:
            return float(data.get("balance", 0))
        except Exception:
            return 0.0

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()
