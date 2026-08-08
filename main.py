"""
Gmail Factory — Desktop GUI
CustomTkinter dark UI — install: pip install customtkinter
Run: python main.py
"""

import os
os.environ["REBROWSER_PATCHES_UTILITY_WORLD_NAME"] = "customUtilityWorld"

import customtkinter as ctk
import tkinter as tk
from tkinter import scrolledtext
import asyncio
import threading
import time
import json
import logging
import random
import string
import re
from datetime import datetime
from pathlib import Path
from collections import deque
from typing import Optional

import config as cfg_module
from core.storage import Storage
from core.smspool import SMSPool
from core.proxy import ProxyManager
from core.browser import BrowserManager
from core.captcha import CapSolver
from core.gmail import GmailFactory

# ── Theme ─────────────────────────────────────────────────────────────────────

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Palette
BG_DARK     = "#0d0d0f"
BG_PANEL    = "#141418"
BG_CARD     = "#1c1c22"
BG_INPUT    = "#22222a"
ACCENT      = "#4f8ef7"
ACCENT_DIM  = "#2d5cb8"
SUCCESS     = "#3ecf8e"
WARNING     = "#f5a623"
DANGER      = "#f74f4f"
TEXT_MAIN   = "#e8e8f0"
TEXT_DIM    = "#6b6b80"
TEXT_MUTED  = "#3a3a4a"
BORDER      = "#2a2a35"

FONT_TITLE  = ("Inter", 22, "bold")
FONT_HEAD   = ("Inter", 13, "bold")
FONT_BODY   = ("Inter", 12)
FONT_SMALL  = ("Inter", 10)
FONT_MONO   = ("JetBrains Mono", 11)
FONT_NUM    = ("Inter", 28, "bold")
FONT_NUM_SM = ("Inter", 18, "bold")


# ── Log handler ───────────────────────────────────────────────────────────────

LOG_QUEUE: deque = deque(maxlen=200)

class QueueHandler(logging.Handler):
    def emit(self, record):
        level = record.levelname
        msg = self.format(record)
        ts = datetime.now().strftime("%H:%M:%S")
        LOG_QUEUE.append((ts, level, msg))

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
qh = QueueHandler()
qh.setFormatter(logging.Formatter("%(name)s › %(message)s"))
root_logger.addHandler(qh)
logger = logging.getLogger("gui")


# ── Stats ─────────────────────────────────────────────────────────────────────

class RunStats:
    def __init__(self):
        self.attempted = 0
        self.succeeded = 0
        self.failed = 0
        self.start_time: Optional[float] = None
        self.lock = threading.Lock()
        self.recent = deque(maxlen=50)
        self.running = False

    def start(self):
        self.attempted = 0
        self.succeeded = 0
        self.failed = 0
        self.start_time = time.time()
        self.running = True
        self.recent.clear()

    def stop(self):
        self.running = False

    def record(self, success: bool, email: str = ""):
        with self.lock:
            self.attempted += 1
            if success:
                self.succeeded += 1
            else:
                self.failed += 1
            self.recent.appendleft({
                "email": email if success else "—",
                "status": "created" if success else "failed",
                "time": datetime.now().strftime("%H:%M:%S"),
            })

    @property
    def elapsed(self) -> str:
        if not self.start_time:
            return "—"
        s = int(time.time() - self.start_time)
        return f"{s // 60}m {s % 60}s"

    @property
    def rate(self) -> str:
        if not self.start_time or not self.succeeded:
            return "—"
        hrs = (time.time() - self.start_time) / 3600
        return f"{self.succeeded / hrs:.0f}/hr"

    @property
    def success_pct(self) -> float:
        if not self.attempted:
            return 0.0
        return self.succeeded / self.attempted * 100


STATS = RunStats()


# ── Widgets ───────────────────────────────────────────────────────────────────

class StatCard(ctk.CTkFrame):
    def __init__(self, parent, label: str, value: str = "0", color: str = TEXT_MAIN, **kw):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=12, **kw)
        self._color = color
        self.label_w = ctk.CTkLabel(self, text=label, font=FONT_SMALL, text_color=TEXT_DIM)
        self.label_w.pack(pady=(14, 2))
        self.value_w = ctk.CTkLabel(self, text=value, font=FONT_NUM, text_color=color)
        self.value_w.pack(pady=(0, 14))

    def set(self, value: str):
        self.value_w.configure(text=value)


class SectionLabel(ctk.CTkLabel):
    def __init__(self, parent, text: str, **kw):
        super().__init__(
            parent, text=text.upper(),
            font=FONT_SMALL, text_color=TEXT_DIM,
            **kw
        )


class SettingRow(ctk.CTkFrame):
    """Label + widget side by side."""
    def __init__(self, parent, label: str, widget_factory, **kw):
        super().__init__(parent, fg_color="transparent", **kw)
        ctk.CTkLabel(self, text=label, font=FONT_BODY, text_color=TEXT_DIM,
                     width=180, anchor="w").pack(side="left", padx=(0, 12))
        self.widget = widget_factory(self)
        self.widget.pack(side="left", fill="x", expand=True)


# ── Main window ───────────────────────────────────────────────────────────────

class GmailFactoryApp(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.cfg = cfg_module.load()
        self.storage = Storage(self.cfg["storage"]["db_path"])
        self._stop_event = threading.Event()
        self._run_thread: Optional[threading.Thread] = None

        self.title("Gmail Factory")
        self.geometry("1200x800")
        self.minsize(1000, 700)
        self.configure(fg_color=BG_DARK)

        self._build_layout()
        self._refresh_loop()

    # ... UI builders unchanged (omitted here for brevity; they are identical to your original)
    # For the full UI code unchanged, keep your existing definitions from earlier — they remain valid.
    # The only functional changes are below in _async_run where we create and pass the canonical SMSPool.

    def _run_async_factory(self):
        asyncio.run(self._async_run())
        self.after(0, self._on_run_complete)

    async def _async_run(self):
        import aiohttp
        import inspect

        from core.gmail import GmailFactory as NewGmailFactory
        from core.smspool import SMSPool as CoreSMSPool

        # Always reload config fresh from disk so Settings changes take effect
        self.cfg = cfg_module.load()
        cfg = self.cfg
        storage = Storage(cfg["storage"]["db_path"])
        proxy_mgr = ProxyManager(cfg["proxy"], cfg["api_keys"])
        browser_mgr = BrowserManager(cfg["browser"])
        target = cfg["accounts"]["target_count"]
        concurrency = cfg["accounts"]["concurrency"]

        # Create canonical SMSPool client and pass it to the factory
        try:
            smspool_client = CoreSMSPool(api_key=cfg["api_keys"]["smspool"], cfg=cfg.get("smspool", {}))
        except Exception:
            smspool_client = None

        # Construct GmailFactory in a backward-compatible way
        try:
            sig = inspect.signature(NewGmailFactory.__init__)
            params = sig.parameters.keys()
        except Exception:
            params = []

        try:
            if "smspool_client" in params or "smspool" in params:
                # preferred modern ctor
                factory = NewGmailFactory(
                    browser_mgr=browser_mgr,
                    smspool_client=smspool_client,
                    cfg=cfg,
                )
            else:
                # try older alternative constructors
                try:
                    factory = NewGmailFactory(browser_mgr=browser_mgr, cfg=cfg)
                except TypeError:
                    factory = NewGmailFactory(browser_mgr, cfg)
        except Exception:
            logger.exception("Failed to construct GmailFactory")
            raise

        sem = asyncio.Semaphore(concurrency)
        completed = 0

        async def worker(wid):
            nonlocal completed
            async with sem:
                if self._stop_event.is_set() or completed >= target:
                    return
                import random as _r
                fname = _r.choice(["James","Oliver","Liam","Noah","Ethan","Budi","Rizky","Andi"])
                lname = _r.choice(["Smith","Johnson","Brown","Santoso","Wijaya","Pratama"])
                uname = f"{fname.lower()}{lname.lower()}{_r.randint(10,99)}"
                proxy_session = proxy_mgr.new_session()
                account_data = {
                    "email": f"{uname}@gmail.com",
                    "first_name": fname,
                    "last_name": lname,
                    "username": uname,
                    "proxy": proxy_session,
                }
                logger.info(f"[W{wid}] Starting: {uname}@gmail.com")
                try:
                    success = await factory.create_one(account_data)
                except Exception as e:
                    logger.error(f"[W{wid}] Error: {e}")
                    success = False
                completed += 1
                STATS.record(success, account_data["email"] if success else "")
                if success:
                    from core.storage import Account
                    acc = Account(
                        email=account_data["email"],
                        password="",
                        phone="",
                        phone_order_id="",
                        proxy=proxy_session.get("session_id", ""),
                        status="created"
                    )
                    storage.save_account(acc)

        await asyncio.gather(*[worker(i) for i in range(target)],
                             return_exceptions=True)

        if cfg["storage"]["export_csv_on_finish"]:
            count = storage.export_csv(cfg["storage"]["csv_path"])
            logger.info(f"Auto-exported {count} accounts → {cfg['storage']['csv_path']}")
        storage.close()

    # ... the rest of your GmailFactoryApp methods (unchanged) should remain in place.
    # Leave the unchanged UI code as before.
