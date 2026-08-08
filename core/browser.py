import os
# Force environment variables before rebrowser imports execute
os.environ["REBROWSER_PATCHES_UTILITY_WORLD_NAME"] = "customUtilityWorld"

import random
import string
import re
import asyncio
import logging
from typing import Optional
from rebrowser_playwright.async_api import async_playwright, Browser, BrowserContext, Page, Route

logger = logging.getLogger("browser")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

TIMEZONES = ["Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura", "America/New_York", "Europe/London", "Asia/Singapore"]

STEALTH_JS = """
() => {
    // ── Canvas noise ─────────────────────────────────────────────────────
    try {
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(...args) {
            const ctx = this.getContext('2d');
            if (ctx && this.width > 0 && this.height > 0) {
                const img = ctx.getImageData(0, 0, this.width, this.height);
                for (let i = 0; i < img.data.length; i += 99) {
                    img.data[i] ^= (Math.random() * 3 | 0);
                }
                ctx.putImageData(img, 0, 0);
            }
            return origToDataURL.apply(this, args);
        };
    } catch(e) {}

    // ── WebGL spoof ──────────────────────────────────────────────────────
    try {
        const getParam = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(p) {
            if (p === 37445) return 'Intel Inc.';
            if (p === 37446) return 'Intel Iris OpenGL Engine';
            return getParam.call(this, p);
        };
    } catch(e) {}

    // ── Chrome object ────────────────────────────────────────────────────
    try {
        if (!window.chrome) {
            window.chrome = { runtime: { connect: ()=>{}, sendMessage: ()=>{} } };
        }
    } catch(e) {}
}
"""

class BrowserManager:
    def __init__(self, cfg: dict):
        self.headless = cfg.get("headless", True)
        self.slow_mo = cfg.get("slow_mo_ms", 80)
        self.typing_delay = cfg.get("typing_delay_ms", 45)
        self.page_timeout = cfg.get("page_timeout_ms", 35000)
        self.stealth = cfg.get("stealth", True)
        self.viewports = [
            {"width": 1366, "height": 768},
            {"width": 1440, "height": 900},
            {"width": 1920, "height": 1080},
        ]

    def _random_viewport(self) -> dict:
        return random.choice(self.viewports)

    def _random_ua(self) -> str:
        return random.choice(USER_AGENTS)

    def _random_timezone(self) -> str:
        return random.choice(TIMEZONES)

    def _rotate_sessid(self, proxy_config: Optional[dict]) -> Optional[dict]:
        """
        Dynamically clears whatever sits between -sessid- and -sesstime- and replaces it
        with a unique 12-character alphanumeric string every single time a context is initialized.
        """
        if not proxy_config or "playwright_config" not in proxy_config:
            return proxy_config

        p_cfg = dict(proxy_config["playwright_config"])
        username = p_cfg.get("username", "")
        if "-sessid-" in username and "-sesstime-" in username:
            fresh_sess = "".join(random.choices(string.ascii_letters + string.digits, k=12))
            p_cfg["username"] = re.sub(
                r"(-sessid-)[^-]+(-sesstime-)",
                f"\\g<1>{fresh_sess}\\g<2>",
                username
            )
            logger.debug(f"Rotated gateway IP sessid -> {fresh_sess}")
        
        updated_config = dict(proxy_config)
        updated_config["playwright_config"] = p_cfg
        return updated_config

    async def new_context(self, playwright, proxy_config: dict) -> tuple:
        # Guarantee dynamic automated IP rotation upon browser lifecycle initialization
        rotated_proxy = self._rotate_sessid(proxy_config)
        active_proxy = rotated_proxy.get("playwright_config") if rotated_proxy else None

        if self.headless:
            # Invisible full Chrome — passes 6/6 stealth
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--no-first-run",
                "--disable-infobars",
                "--window-size=1920,1080",
            ]
            # headless flag (Playwright new mode) only when running headless
            launch_args.append("--headless=new")
        else:
            # Visible window — minimal args so window actually appears on screen
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--start-maximized",
            ]

        browser: Browser = await playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=launch_args,
            proxy=active_proxy,
        )
        viewport = self._random_viewport()
        ua = self._random_ua()
        tz = self._random_timezone()
        
        ctx: BrowserContext = await browser.new_context(
            user_agent=ua,
            viewport=viewport,
            locale="en-US",
            timezone_id=tz,
            java_script_enabled=True,
        )
        
        if self.stealth:
            await ctx.add_init_script(STEALTH_JS)
            
        ctx.set_default_timeout(self.page_timeout)
        logger.debug(f"Secure context attached: {ua[:40]}... | {viewport}")
        return browser, ctx

    async def new_page(self, ctx: BrowserContext) -> Page:
        page = await ctx.new_page()

        # Safe asset route intercept: abort images and media while keeping stylesheets and forms operational
        async def _intercept_low_data_route(route: Route):
            req = route.request
            if req.resource_type in ["image", "media", "font"]:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", _intercept_low_data_route)
        return page

    async def human_type(self, page: Page, selector: str, text: str):
        # Best-effort: wait for selector before clicking/typing
        try:
            await page.wait_for_selector(selector, timeout=4000)
        except Exception:
            pass
        await page.click(selector)
        for char in text:
            await page.type(selector, char, delay=self.typing_delay + random.randint(-15, 30))
            
    async def human_click(self, page: Page, selector: str):
        await asyncio.sleep(random.uniform(0.2, 0.6))
        try:
            await page.wait_for_selector(selector, timeout=3000)
        except Exception:
            pass
        await page.click(selector)

    async def wait_random(self, min_ms: int = 500, max_ms: int = 1800):
        await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))
