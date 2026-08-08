import random
import string
import time
import logging
from typing import Optional

logger = logging.getLogger("proxy")

# Proxy providers:
#
# OkkProxy (default, cheapest at $0.5/GB):
#   Host: un-residential.okkproxy.com  Port: 16666
#   Username: {user}-country-{cc}-sessid-{id}  → sticky  (standardized to -sessid-)
#   Username: {user}-country-{cc}               → rotating
#
# Webshare ($7/GB):
#   Host: p.webshare.io  Port: 80
#   Username: {user}-{cc}-{session_id}  → sticky
#   Username: {user}-{cc}-rotate        → rotating

class ProxyManager:
    # *one sticky session per account — 30 minutes is enough to be born*

    def __init__(self, cfg: dict, keys: dict):
        self.provider = cfg.get("provider", "okkproxy")
        self.country  = cfg.get("country_code", "id")
        self.rotate_on_fail = cfg.get("rotate_on_fail", True)

        # OkkProxy
        self.okk_host = "un-residential.okkproxy.com"
        self.okk_port = 16666
        self.okk_user = keys.get("okkproxy_user", "")
        self.okk_pass = keys.get("okkproxy_pass", "")

        # Webshare
        self.ws_host  = cfg.get("host", "p.webshare.io")
        self.ws_port  = cfg.get("port", 80)
        self.ws_user  = keys.get("webshare_user", "")
        self.ws_pass  = keys.get("webshare_pass", "")

        self._active_sessions: dict[str, float] = {}
        self._failed_sessions: set = set()

    def _random_session_id(self, length=10) -> str:
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

    def new_session(self, country_code: Optional[str] = None) -> dict:
        cc = country_code or self.country
        session_id = self._random_session_id()
        self._active_sessions[session_id] = time.time()

        if self.provider == "okkproxy":
            # STANDARDIZED: use -sessid- token so rotation helpers can find it
            user = f"{self.okk_user}-country-{cc}-sessid-{session_id}"
            logger.debug(f"OkkProxy session [{cc}]: {session_id}")
            return {
                "session_id": session_id, "country": cc, "provider": "okkproxy",
                "proxy_url": f"http://{user}:{self.okk_pass}@{self.okk_host}:{self.okk_port}",
                "playwright_config": {
                    "server": f"http://{self.okk_host}:{self.okk_port}",
                    "username": user,
                    "password": self.okk_pass,
                }
            }
        else:
            user = f"{self.ws_user}-{cc}-{session_id}"
            logger.debug(f"Webshare session [{cc}]: {session_id}")
            return {
                "session_id": session_id, "country": cc, "provider": "webshare",
                "proxy_url": f"http://{user}:{self.ws_pass}@{self.ws_host}:{self.ws_port}",
                "playwright_config": {
                    "server": f"http://{self.ws_host}:{self.ws_port}",
                    "username": user,
                    "password": self.ws_pass,
                }
            }

    def new_rotating_session(self, country_code: Optional[str] = None) -> dict:
        cc = country_code or self.country
        session_id = f"rotate_{self._random_session_id(6)}"

        if self.provider == "okkproxy":
            user = f"{self.okk_user}-country-{cc}"
            return {
                "session_id": session_id, "country": cc, "provider": "okkproxy",
                "proxy_url": f"http://{user}:{self.okk_pass}@{self.okk_host}:{self.okk_port}",
                "playwright_config": {
                    "server": f"http://{self.okk_host}:{self.okk_port}",
                    "username": user,
                    "password": self.okk_pass,
                }
            }
        else:
            user = f"{self.ws_user}-{cc}-rotate"
            return {
                "session_id": session_id, "country": cc, "provider": "webshare",
                "proxy_url": f"http://{user}:{self.ws_pass}@{self.ws_host}:{self.ws_port}",
                "playwright_config": {
                    "server": f"http://{self.ws_host}:{self.ws_port}",
                    "username": user,
                    "password": self.ws_pass,
                }
            }

    def mark_failed(self, session_id: str):
        self._failed_sessions.add(session_id)
        self._active_sessions.pop(session_id, None)
        logger.warning(f"Proxy session {session_id} marked failed")

    def cleanup_old_sessions(self, max_age: int = 1800):
        now = time.time()
        expired = [s for s, t in self._active_sessions.items() if now - t > max_age]
        for s in expired:
            del self._active_sessions[s]

    @property
    def active_count(self) -> int:
        return len(self._active_sessions)

    @property
    def failed_count(self) -> int:
        return len(self._failed_sessions)

    def proxy_string(self, session: dict) -> str:
        p = session.get("provider", "?")
        return f"[{p}] {session['country']} session={session['session_id']}"
