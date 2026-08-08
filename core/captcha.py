import aiohttp
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("captcha")

# CapSolver API — all POST, JSON body
# Base: https://api.capsolver.com
#
# Flow:
#   POST /createTask  → {"clientKey": key, "task": {...}} → {"taskId": "..."}
#   POST /getTaskResult → {"clientKey": key, "taskId": "..."} → {"status": "ready", "solution": {...}}
#   POST /getBalance  → {"clientKey": key} → {"balance": 1.23}
#
# Token task types used here:
#   ReCaptchaV2TaskProxyLess  — no proxy needed, CapSolver routes it
#   ReCaptchaV3TaskProxyLess
#
# Solution field: solution.gRecaptchaResponse

CAPSOLVER_BASE = "https://api.capsolver.com"

# Google signup reCAPTCHA v2 site key (public, same for all)
GOOGLE_SIGNUP_SITE_KEY_V2 = "6LeTnxkTAAAAAN9QEuDZRpn90WwKk_R1TRW_g-JC"
GOOGLE_SIGNUP_URL = "https://accounts.google.com/signup"


class CapSolver:
    # *outsources the part that needs eyes — pays by the solve*

    def __init__(self, api_key: str, cfg: dict):
        self.api_key = api_key
        self.max_attempts = cfg.get("max_attempts", 3)
        self.timeout = cfg.get("timeout_seconds", 120)
        self.session: Optional[aiohttp.ClientSession] = None

    async def _post(self, endpoint: str, payload: dict) -> dict:
        # clientKey always at top level, never inside task
        payload["clientKey"] = self.api_key
        url = f"{CAPSOLVER_BASE}{endpoint}"
        try:
            async with self.session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                return await r.json(content_type=None)
        except Exception as e:
            logger.error(f"CapSolver [{endpoint}] error: {e}")
            return {}

    async def _create_task(self, task: dict) -> Optional[str]:
        """
        POST /createTask
        Returns taskId string or None on error.

        Success: {"errorId": 0, "taskId": "uuid-..."}
        Error:   {"errorId": 1, "errorCode": "...", "errorDescription": "..."}
        """
        data = await self._post("/createTask", {"task": task})
        if data.get("errorId") == 0:
            task_id = data.get("taskId")
            logger.debug(f"Task created: {task_id}")
            return task_id
        logger.error(f"createTask error: {data.get('errorCode')} — {data.get('errorDescription')}")
        return None

    async def _get_result(self, task_id: str) -> Optional[str]:
        """
        Poll /getTaskResult until status="ready" or timeout.

        Pending: {"errorId": 0, "status": "processing"}
        Ready:   {"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "03AG..."}}
        Error:   {"errorId": 1, ...}
        """
        elapsed = 0
        while elapsed < self.timeout:
            await asyncio.sleep(4)
            elapsed += 4

            data = await self._post("/getTaskResult", {"taskId": task_id})

            if data.get("errorId", 0) != 0:
                logger.error(f"getTaskResult error [{task_id}]: {data.get('errorDescription')}")
                return None

            if data.get("status") == "ready":
                solution = data.get("solution", {})
                token = solution.get("gRecaptchaResponse") or solution.get("token")
                if token:
                    logger.info(f"CAPTCHA solved [{task_id}] — token length: {len(token)}")
                    return token
                logger.warning(f"Status ready but no token in solution: {solution}")
                return None

            logger.debug(f"CAPTCHA pending [{task_id}] [{elapsed}s]")

        logger.warning(f"CAPTCHA timeout [{task_id}] after {self.timeout}s")
        return None

    async def solve_recaptcha_v2(
        self,
        site_key: str = GOOGLE_SIGNUP_SITE_KEY_V2,
        page_url: str = GOOGLE_SIGNUP_URL,
    ) -> Optional[str]:
        """
        Solve reCAPTCHA v2 via ProxyLess task.
        Returns the gRecaptchaResponse token to inject into the page.
        """
        for attempt in range(1, self.max_attempts + 1):
            logger.info(f"Solving reCAPTCHA v2 [attempt {attempt}/{self.max_attempts}]")

            task_id = await self._create_task({
                "type": "ReCaptchaV2TaskProxyLess",
                "websiteURL": page_url,
                "websiteKey": site_key,
            })
            if not task_id:
                continue

            token = await self._get_result(task_id)
            if token:
                return token

        logger.error(f"reCAPTCHA v2 solve failed after {self.max_attempts} attempts")
        return None

    async def solve_recaptcha_v3(
        self,
        site_key: str,
        page_url: str,
        action: str = "signup",
        min_score: float = 0.5,
    ) -> Optional[str]:
        """Solve reCAPTCHA v3 via ProxyLess task."""
        for attempt in range(1, self.max_attempts + 1):
            logger.info(f"Solving reCAPTCHA v3 [attempt {attempt}]")
            task_id = await self._create_task({
                "type": "ReCaptchaV3TaskProxyLess",
                "websiteURL": page_url,
                "websiteKey": site_key,
                "pageAction": action,
                "minScore": min_score,
            })
            if not task_id:
                continue
            token = await self._get_result(task_id)
            if token:
                return token
        return None

    async def get_balance(self) -> float:
        """
        POST /getBalance → {"errorId": 0, "balance": 1.234567}
        balance is a decimal in USD.
        """
        data = await self._post("/getBalance", {})
        if data.get("errorId") == 0:
            try:
                return float(data.get("balance", 0))
            except Exception:
                pass
        logger.warning(f"getBalance failed: {data}")
        return 0.0

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()
