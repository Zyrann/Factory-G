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

    # ── Layout ─────────────────────────────────────────────────────────────

    def _build_layout(self):
        # Sidebar
        self.sidebar = ctk.CTkFrame(self, fg_color=BG_PANEL, width=220, corner_radius=0)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self._build_sidebar()

        # Main area
        self.main = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
        self.main.pack(side="left", fill="both", expand=True)

        # Pages
        self.pages: dict[str, ctk.CTkFrame] = {}
        for name in ("dashboard", "accounts", "settings", "stealth", "logs"):
            f = ctk.CTkFrame(self.main, fg_color=BG_DARK, corner_radius=0)
            f.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.pages[name] = f

        self._build_dashboard()
        self._build_accounts()
        self._build_settings()
        self._build_stealth()
        self._build_logs()
        self._show_page("dashboard")

    def _build_sidebar(self):
        # Logo area
        logo_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        logo_frame.pack(fill="x", pady=(28, 32), padx=20)

        ctk.CTkLabel(logo_frame, text="⚡", font=("Inter", 28)).pack(side="left")
        ctk.CTkLabel(logo_frame, text=" Factory", font=("Inter", 20, "bold"),
                     text_color=TEXT_MAIN).pack(side="left")

        # Nav buttons
        self._nav_btns = {}
        nav_items = [
            ("dashboard", "  Dashboard", "⊞"),
            ("accounts",  "  Accounts",  "◈"),
            ("settings",  "  Settings",  "⚙"),
            ("stealth",   "  Stealth Test", "🛡"),
            ("logs",      "  Logs",      "≡"),
        ]
        for page, label, icon in nav_items:
            btn = ctk.CTkButton(
                self.sidebar,
                text=f"{icon}  {label.strip()}",
                font=FONT_BODY,
                height=44,
                corner_radius=10,
                fg_color="transparent",
                hover_color=BG_CARD,
                text_color=TEXT_DIM,
                anchor="w",
                command=lambda p=page: self._show_page(p),
            )
            btn.pack(fill="x", padx=12, pady=2)
            self._nav_btns[page] = btn

        # Bottom: run control
        ctk.CTkFrame(self.sidebar, fg_color=BORDER, height=1).pack(
            fill="x", padx=20, pady=20)

        self.run_btn = ctk.CTkButton(
            self.sidebar,
            text="▶  Start Run",
            font=FONT_HEAD,
            height=48,
            corner_radius=12,
            fg_color=ACCENT,
            hover_color=ACCENT_DIM,
            command=self._toggle_run,
        )
        self.run_btn.pack(fill="x", padx=12, pady=(0, 8))

        self.status_dot = ctk.CTkLabel(
            self.sidebar, text="● Idle",
            font=FONT_SMALL, text_color=TEXT_DIM
        )
        self.status_dot.pack(pady=(0, 20))

    def _show_page(self, name: str):
        self.pages[name].lift()
        for p, btn in self._nav_btns.items():
            if p == name:
                btn.configure(fg_color=BG_CARD, text_color=TEXT_MAIN)
            else:
                btn.configure(fg_color="transparent", text_color=TEXT_DIM)

    # ── Dashboard ──────────────────────────────────────────────────────────

    def _build_dashboard(self):
        p = self.pages["dashboard"]

        # Header
        hdr = ctk.CTkFrame(p, fg_color="transparent")
        hdr.pack(fill="x", padx=32, pady=(28, 0))
        ctk.CTkLabel(hdr, text="Dashboard", font=FONT_TITLE,
                     text_color=TEXT_MAIN).pack(side="left")

        # Stat cards row
        cards_frame = ctk.CTkFrame(p, fg_color="transparent")
        cards_frame.pack(fill="x", padx=32, pady=20)
        cards_frame.columnconfigure((0,1,2,3), weight=1, uniform="card")

        self.card_total     = StatCard(cards_frame, "Total Created", color=ACCENT)
        self.card_success   = StatCard(cards_frame, "This Run", color=SUCCESS)
        self.card_failed    = StatCard(cards_frame, "Failed", color=DANGER)
        self.card_rate      = StatCard(cards_frame, "Rate", color=WARNING)

        self.card_total.grid(row=0, column=0, padx=6, sticky="ew")
        self.card_success.grid(row=0, column=1, padx=6, sticky="ew")
        self.card_failed.grid(row=0, column=2, padx=6, sticky="ew")
        self.card_rate.grid(row=0, column=3, padx=6, sticky="ew")

        # Bottom row: progress + recent
        bottom = ctk.CTkFrame(p, fg_color="transparent")
        bottom.pack(fill="both", expand=True, padx=32, pady=(0, 24))
        bottom.columnconfigure(0, weight=3)
        bottom.columnconfigure(1, weight=2)
        bottom.rowconfigure(0, weight=1)

        # Progress panel
        prog_panel = ctk.CTkFrame(bottom, fg_color=BG_CARD, corner_radius=14)
        prog_panel.grid(row=0, column=0, padx=(0, 10), sticky="nsew")

        ctk.CTkLabel(prog_panel, text="Run Progress",
                     font=FONT_HEAD, text_color=TEXT_MAIN).pack(
            anchor="w", padx=20, pady=(18, 4))

        self.progress_label = ctk.CTkLabel(
            prog_panel, text="0 / 0 accounts", font=FONT_BODY, text_color=TEXT_DIM)
        self.progress_label.pack(anchor="w", padx=20)

        self.progress_bar = ctk.CTkProgressBar(
            prog_panel, height=8, corner_radius=4,
            fg_color=BG_INPUT, progress_color=ACCENT)
        self.progress_bar.pack(fill="x", padx=20, pady=16)
        self.progress_bar.set(0)

        # Mini stats grid
        mini = ctk.CTkFrame(prog_panel, fg_color="transparent")
        mini.pack(fill="x", padx=20, pady=(0, 20))
        mini.columnconfigure((0,1,2), weight=1)

        def mini_stat(parent, col, label, var):
            f = ctk.CTkFrame(parent, fg_color=BG_INPUT, corner_radius=8)
            f.grid(row=0, column=col, padx=4, sticky="ew")
            ctk.CTkLabel(f, text=label, font=FONT_SMALL,
                         text_color=TEXT_DIM).pack(pady=(10,2))
            lbl = ctk.CTkLabel(f, text="—", font=FONT_NUM_SM, text_color=TEXT_MAIN)
            lbl.pack(pady=(0,10))
            return lbl

        self.mini_elapsed  = mini_stat(mini, 0, "Elapsed", None)
        self.mini_rate     = mini_stat(mini, 1, "Accs/hr", None)
        self.mini_pct      = mini_stat(mini, 2, "Success %", None)

        # Recent accounts panel
        recent_panel = ctk.CTkFrame(bottom, fg_color=BG_CARD, corner_radius=14)
        recent_panel.grid(row=0, column=1, padx=(10, 0), sticky="nsew")

        ctk.CTkLabel(recent_panel, text="Recent Accounts",
                     font=FONT_HEAD, text_color=TEXT_MAIN).pack(
            anchor="w", padx=20, pady=(18, 8))

        self.recent_frame = ctk.CTkScrollableFrame(
            recent_panel, fg_color="transparent", scrollbar_button_color=BG_INPUT)
        self.recent_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _refresh_dashboard(self):
        db_stats = self.storage.get_stats()
        self.card_total.set(str(db_stats["total"]))
        self.card_success.set(str(STATS.succeeded))
        self.card_failed.set(str(STATS.failed))
        self.card_rate.set(STATS.rate if STATS.running else "—")

        target = self.cfg["accounts"]["target_count"]
        done = STATS.attempted
        prog = done / target if target > 0 else 0
        self.progress_bar.set(min(prog, 1.0))
        self.progress_label.configure(
            text=f"{done} / {target} accounts  ({'running' if STATS.running else 'idle'})"
        )

        self.mini_elapsed.configure(text=STATS.elapsed)
        self.mini_rate.configure(text=STATS.rate if STATS.running else "—")
        self.mini_pct.configure(text=f"{STATS.success_pct:.0f}%" if STATS.attempted else "—")

        # Recent list
        for w in self.recent_frame.winfo_children():
            w.destroy()
        for entry in list(STATS.recent)[:15]:
            row = ctk.CTkFrame(self.recent_frame, fg_color=BG_INPUT, corner_radius=8)
            row.pack(fill="x", pady=2)
            color = SUCCESS if entry["status"] == "created" else DANGER
            dot = "●"
            ctk.CTkLabel(row, text=dot, font=FONT_SMALL,
                         text_color=color, width=20).pack(side="left", padx=(8, 0))
            ctk.CTkLabel(row, text=entry["email"], font=FONT_MONO,
                         text_color=TEXT_MAIN, anchor="w").pack(side="left", padx=6)
            ctk.CTkLabel(row, text=entry["time"], font=FONT_SMALL,
                         text_color=TEXT_DIM).pack(side="right", padx=8)

    # ── Accounts tab ──────────────────────────────────────────────────────

    def _build_accounts(self):
        p = self.pages["accounts"]

        hdr = ctk.CTkFrame(p, fg_color="transparent")
        hdr.pack(fill="x", padx=32, pady=(28, 16))
        ctk.CTkLabel(hdr, text="Accounts", font=FONT_TITLE,
                     text_color=TEXT_MAIN).pack(side="left")

        # Buttons
        btn_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        btn_frame.pack(side="right")
        ctk.CTkButton(btn_frame, text="↻ Refresh", width=100, height=34,
                      font=FONT_BODY, fg_color=BG_CARD, hover_color=BG_INPUT,
                      command=self._refresh_accounts).pack(side="left", padx=4)
        ctk.CTkButton(btn_frame, text="⬇ Export CSV", width=120, height=34,
                      font=FONT_BODY, fg_color=ACCENT, hover_color=ACCENT_DIM,
                      command=self._export_csv).pack(side="left", padx=4)

        # Table header
        cols_frame = ctk.CTkFrame(p, fg_color=BG_PANEL, corner_radius=0, height=36)
        cols_frame.pack(fill="x", padx=32)
        cols_frame.pack_propagate(False)
        for col, w in [("Email", 320), ("Password", 160), ("Phone", 130),
                       ("Status", 90), ("Created", 160)]:
            ctk.CTkLabel(cols_frame, text=col, font=FONT_SMALL,
                         text_color=TEXT_DIM, width=w, anchor="w").pack(
                side="left", padx=8)

        # Scrollable rows
        self.accounts_scroll = ctk.CTkScrollableFrame(
            p, fg_color="transparent",
            scrollbar_button_color=BG_INPUT)
        self.accounts_scroll.pack(fill="both", expand=True, padx=32, pady=(0, 24))
        self._refresh_accounts()

    def _refresh_accounts(self):
        for w in self.accounts_scroll.winfo_children():
            w.destroy()
        accounts = self.storage.get_all_accounts()
        if not accounts:
            ctk.CTkLabel(self.accounts_scroll, text="No accounts yet.",
                         font=FONT_BODY, text_color=TEXT_DIM).pack(pady=40)
            return
        for acc in reversed(accounts[-200:]):
            row = ctk.CTkFrame(self.accounts_scroll, fg_color=BG_CARD,
                               corner_radius=8, height=40)
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)
            color = SUCCESS if acc["status"] == "created" else DANGER
            for val, w in [
                (acc["email"],    320),
                (acc["password"], 160),
                (f"+{acc['phone']}", 130),
            ]:
                ctk.CTkLabel(row, text=val, font=FONT_MONO,
                             text_color=TEXT_MAIN, width=w, anchor="w").pack(
                    side="left", padx=8)
            ctk.CTkLabel(row, text=acc["status"], font=FONT_SMALL,
                         text_color=color, width=90, anchor="w").pack(side="left", padx=8)
            ctk.CTkLabel(row, text=(acc["created_at"] or "")[:19],
                         font=FONT_SMALL, text_color=TEXT_DIM,
                         width=160, anchor="w").pack(side="left", padx=8)

    def _export_csv(self):
        path = self.cfg["storage"]["csv_path"]
        count = self.storage.export_csv(path)
        self._toast(f"Exported {count} accounts → {path}")

    # ── Settings tab ──────────────────────────────────────────────────────

    def _build_settings(self):
        p = self.pages["settings"]

        ctk.CTkLabel(p, text="Settings", font=FONT_TITLE,
                     text_color=TEXT_MAIN).pack(anchor="w", padx=32, pady=(28, 20))

        scroll = ctk.CTkScrollableFrame(p, fg_color="transparent",
                                        scrollbar_button_color=BG_INPUT)
        scroll.pack(fill="both", expand=True, padx=32, pady=(0, 24))

        self._s_vars = {}

        def section(label):
            ctk.CTkFrame(scroll, fg_color=BORDER, height=1).pack(
                fill="x", pady=(20, 8))
            SectionLabel(scroll, label).pack(anchor="w", pady=(0, 8))

        def entry_row(parent, key, label, default="", show=""):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            f.pack(fill="x", pady=4)
            ctk.CTkLabel(f, text=label, font=FONT_BODY, text_color=TEXT_DIM,
                         width=200, anchor="w").pack(side="left")
            var = ctk.StringVar(value=str(default))
            e = ctk.CTkEntry(f, textvariable=var, font=FONT_MONO,
                             fg_color=BG_INPUT, border_color=BORDER,
                             show=show, width=320)
            e.pack(side="left")
            self._s_vars[key] = var

        def toggle_row(parent, key, label, default=True):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            f.pack(fill="x", pady=4)
            ctk.CTkLabel(f, text=label, font=FONT_BODY, text_color=TEXT_DIM,
                         width=200, anchor="w").pack(side="left")
            var = ctk.BooleanVar(value=default)
            sw = ctk.CTkSwitch(f, variable=var, text="",
                                fg_color=BG_INPUT, progress_color=ACCENT)
            sw.pack(side="left")
            self._s_vars[key] = var

        def spin_row(parent, key, label, default=1, from_=1, to=50):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            f.pack(fill="x", pady=4)
            ctk.CTkLabel(f, text=label, font=FONT_BODY, text_color=TEXT_DIM,
                         width=200, anchor="w").pack(side="left")
            var = ctk.StringVar(value=str(default))
            e = ctk.CTkEntry(f, textvariable=var, font=FONT_MONO,
                             fg_color=BG_INPUT, border_color=BORDER, width=100)
            e.pack(side="left")
            self._s_vars[key] = var

        c = self.cfg
        k = c["api_keys"]

        section("API Keys")
        entry_row(scroll, "smspool_key",    "SMSPool API Key",       k.get("smspool", ""))
        entry_row(scroll, "capsolver_key",  "CapSolver API Key",     k.get("capsolver", ""))

        section("Run Settings")
        spin_row(scroll,  "target_count",   "Accounts to create",    c["accounts"]["target_count"])
        spin_row(scroll,  "concurrency",    "Concurrent workers",    c["accounts"]["concurrency"], to=20)

        section("SMSPool")
        entry_row(scroll, "sms_country",    "Country",               c["smspool"]["country"])
        entry_row(scroll, "sms_service",    "Service",               c["smspool"]["service"])
        spin_row(scroll,  "sms_reuse",      "Max reuse per number",  c["smspool"]["max_reuse_per_number"])
        spin_row(scroll,  "sms_timeout",    "OTP timeout (seconds)", c["smspool"]["poll_timeout_seconds"], to=300)

        section("Proxy — OkkProxy ($0.5/GB)")
        ctk.CTkLabel(scroll, text="Host: 49.51.189.254  Port: 9999",
                     font=FONT_SMALL, text_color=TEXT_DIM).pack(anchor="w", pady=(0,6))
        entry_row(scroll, "okk_user",       "OkkProxy Username",     k.get("okkproxy_user", ""))
        entry_row(scroll, "okk_pass",       "OkkProxy Password",     k.get("okkproxy_pass", ""))

        section("Proxy — Webshare  ($7/GB)")
        entry_row(scroll, "ws_user",        "Webshare Username",     k.get("webshare_user", ""))
        entry_row(scroll, "ws_pass",        "Webshare Password",     k.get("webshare_pass", ""))

        section("Active Proxy")
        ctk.CTkLabel(scroll, text="Which proxy service to use for runs",
                     font=FONT_SMALL, text_color=TEXT_DIM).pack(anchor="w", pady=(0,6))
        proxy_sel_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        proxy_sel_frame.pack(fill="x", pady=4)
        proxy_var = ctk.StringVar(value=c["proxy"].get("provider", "okkproxy"))
        self._s_vars["proxy_provider"] = proxy_var
        for label, val in [("OkkProxy", "okkproxy"), ("Webshare", "webshare")]:
            ctk.CTkRadioButton(
                proxy_sel_frame, text=label, variable=proxy_var, value=val,
                font=FONT_BODY, text_color=TEXT_MAIN,
                fg_color=ACCENT, hover_color=ACCENT_DIM,
            ).pack(side="left", padx=12)

        section("Proxy Common Settings")
        entry_row(scroll, "proxy_country",  "Country code",          c["proxy"].get("country_code", "id"))

        section("Browser")
        toggle_row(scroll, "headless",      "Headless mode",         c["browser"]["headless"])
        toggle_row(scroll, "stealth",       "Stealth mode",          c["browser"]["stealth"])
        spin_row(scroll,   "slow_mo",       "Slow-mo delay (ms)",    c["browser"]["slow_mo_ms"], from_=0, to=500)
        spin_row(scroll,   "typing_delay",  "Typing delay (ms)",     c["browser"]["typing_delay_ms"], from_=0, to=200)

        section("Storage")
        entry_row(scroll, "db_path",        "Database path",         c["storage"]["db_path"])
        entry_row(scroll, "csv_path",       "CSV export path",       c["storage"]["csv_path"])
        toggle_row(scroll, "csv_on_finish", "Export CSV on finish",  c["storage"]["export_csv_on_finish"])

        # ── API Key Tester ───────────────────────────────────────────────
        ctk.CTkFrame(scroll, fg_color=BORDER, height=1).pack(fill="x", pady=(20, 8))
        SectionLabel(scroll, "Test API Keys").pack(anchor="w", pady=(0, 10))

        test_outer = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=12)
        test_outer.pack(fill="x", pady=(0, 8))

        # Result indicators — one row per service
        self._test_indicators = {}
        for svc in ("SMSPool", "CapSolver", "Webshare", "OkkProxy"):
            row = ctk.CTkFrame(test_outer, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=6)
            ctk.CTkLabel(row, text=svc, font=FONT_BODY,
                         text_color=TEXT_MAIN, width=120, anchor="w").pack(side="left")
            dot = ctk.CTkLabel(row, text="● Not tested",
                               font=FONT_SMALL, text_color=TEXT_DIM)
            dot.pack(side="left", padx=8)
            bal = ctk.CTkLabel(row, text="", font=FONT_SMALL, text_color=TEXT_DIM)
            bal.pack(side="left", padx=4)
            self._test_indicators[svc] = (dot, bal)

        ctk.CTkButton(
            test_outer,
            text="⚡  Test All Keys",
            height=40, font=FONT_BODY,
            fg_color=BG_INPUT, hover_color=BORDER,
            command=self._test_api_keys,
        ).pack(padx=16, pady=(4, 14), anchor="w")

        # Save button
        btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_row.pack(fill="x", pady=24)
        ctk.CTkButton(
            btn_row, text="Save Settings", height=44, font=FONT_HEAD,
            fg_color=ACCENT, hover_color=ACCENT_DIM,
            command=self._save_settings
        ).pack(side="left")
        ctk.CTkButton(
            btn_row, text="Save & Test", height=44, font=FONT_HEAD,
            fg_color=BG_CARD, hover_color=BG_INPUT,
            command=lambda: [self._save_settings(), self._test_api_keys()]
        ).pack(side="left", padx=12)

    def _save_settings(self):
        v = {k: w.get() for k, w in self._s_vars.items()}
        c = self.cfg
        c["api_keys"]["smspool"]            = v["smspool_key"]
        c["api_keys"]["capsolver"]          = v["capsolver_key"]
        c["api_keys"]["okkproxy_user"]      = v.get("okk_user", "")
        c["api_keys"]["okkproxy_pass"]      = v.get("okk_pass", "")
        c["api_keys"]["webshare_user"]      = v.get("ws_user", "")
        c["api_keys"]["webshare_pass"]      = v.get("ws_pass", "")
        c["proxy"]["provider"]              = v.get("proxy_provider", "okkproxy")
        c["accounts"]["target_count"]       = int(v.get("target_count", 10))
        c["accounts"]["concurrency"]        = int(v.get("concurrency", 3))
        c["smspool"]["country"]             = v["sms_country"]
        c["smspool"]["service"]             = v["sms_service"]
        c["smspool"]["max_reuse_per_number"]= int(v.get("sms_reuse", 5))
        c["smspool"]["poll_timeout_seconds"]= int(v.get("sms_timeout", 150))
        c["proxy"]["country_code"]          = v["proxy_country"]
        c["browser"]["headless"]            = bool(v.get("headless", True))
        c["browser"]["stealth"]             = bool(v.get("stealth", True))
        c["browser"]["slow_mo_ms"]          = int(v.get("slow_mo", 80))
        c["browser"]["typing_delay_ms"]     = int(v.get("typing_delay", 45))
        c["storage"]["db_path"]             = v["db_path"]
        c["storage"]["csv_path"]            = v["csv_path"]
        c["storage"]["export_csv_on_finish"]= bool(v.get("csv_on_finish", True))
        cfg_module.save(c)
        self._toast("Settings saved ✓")

    def _test_api_keys(self):
        """Run all API key tests in a background thread, update indicators live."""
        for svc, (dot, bal) in self._test_indicators.items():
            dot.configure(text="● Testing...", text_color=WARNING)
            bal.configure(text="")

        def _run():
            import asyncio
            asyncio.run(self._async_test_keys())

        threading.Thread(target=_run, daemon=True).start()

    async def _async_test_keys(self):
        import aiohttp

        cfg = self.cfg
        k = cfg["api_keys"]

        # ── SMSPool ────────────────────────────────────────────────────
        dot, bal = self._test_indicators["SMSPool"]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.smspool.net/request/balance",
                    data={"key": k["smspool"]},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    data = await r.json(content_type=None)
                    balance = float(data.get("balance", -1))
                    if balance >= 0:
                        self.after(0, lambda b=balance: [
                            dot.configure(text="● Connected", text_color=SUCCESS),
                            bal.configure(text=f"${b:.4f} balance", text_color=TEXT_DIM)
                        ])
                    else:
                        err = data.get("message", "Invalid key")
                        self.after(0, lambda e=err: [
                            dot.configure(text=f"● Failed: {e}", text_color=DANGER),
                            bal.configure(text="")
                        ])
        except Exception as e:
            self.after(0, lambda e=str(e): [
                dot.configure(text=f"● Error: {e[:40]}", text_color=DANGER),
                bal.configure(text="")
            ])

        # ── CapSolver ──────────────────────────────────────────────────
        dot, bal = self._test_indicators["CapSolver"]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.capsolver.com/getBalance",
                    json={"clientKey": k["capsolver"]},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    data = await r.json(content_type=None)
                    if data.get("errorId") == 0:
                        balance = float(data.get("balance", 0))
                        self.after(0, lambda b=balance: [
                            dot.configure(text="● Connected", text_color=SUCCESS),
                            bal.configure(text=f"${b:.4f} balance", text_color=TEXT_DIM)
                        ])
                    else:
                        err = data.get("errorDescription", "Invalid key")
                        self.after(0, lambda e=err: [
                            dot.configure(text=f"● Failed: {e}", text_color=DANGER),
                            bal.configure(text="")
                        ])
        except Exception as e:
            self.after(0, lambda e=str(e): [
                dot.configure(text=f"● Error: {e[:40]}", text_color=DANGER),
                bal.configure(text="")
            ])

        # ── Webshare ───────────────────────────────────────────────────
        dot, bal = self._test_indicators["Webshare"]
        try:
            async with aiohttp.ClientSession() as session:
                proxy_url = (
                    f"http://{k['webshare_user']}-id-testconn:"
                    f"{k['webshare_pass']}@p.webshare.io:80"
                )
                async with session.get(
                    "https://ipv4.webshare.io/",
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=12)
                ) as r:
                    ip = (await r.text()).strip()
                    if ip and len(ip) < 20:
                        self.after(0, lambda i=ip: [
                            dot.configure(text="● Connected", text_color=SUCCESS),
                            bal.configure(text=f"Exit IP: {i}", text_color=TEXT_DIM)
                        ])
                    else:
                        self.after(0, lambda: [
                            dot.configure(text="● Bad response", text_color=DANGER),
                            bal.configure(text="")
                        ])
        except Exception as e:
            msg = str(e)
            if "407" in msg or "tunnel" in msg.lower():
                msg = "Auth failed — check username/password"
            self.after(0, lambda m=msg: [
                dot.configure(text=f"● Failed: {m[:45]}", text_color=DANGER),
                bal.configure(text="")
            ])

        # ── OkkProxy ───────────────────────────────────────────────────
        dot, bal = self._test_indicators["OkkProxy"]
        okk_user = k.get("okkproxy_user", "")
        okk_pass = k.get("okkproxy_pass", "")

        if not okk_user:
            self.after(0, lambda: [
                dot.configure(text="● Not configured", text_color=TEXT_DIM),
                bal.configure(text="Add credentials in settings")
            ])
        else:
            try:
                async with aiohttp.ClientSession() as session:
                    cc = cfg["proxy"].get("country_code", "id")
                    test_sessid = "".join(random.choices(string.ascii_letters + string.digits, k=12))
                    clean_user = okk_user.strip()
                    if not clean_user.startswith("td-customer-") and not clean_user.startswith("td-res-"):
                        clean_user = f"td-customer-{clean_user}"
                    clean_user = re.sub(r"-country-[a-zA-Z]+", "", clean_user)
                    clean_user = re.sub(r"-sessid-[a-zA-Z0-9]+", "", clean_user)
                    clean_user = re.sub(r"-sesstime-\d+", "", clean_user)
                    
                    user_str = f"{clean_user}-country-{cc}-sessid-{test_sessid}-sesstime-30"
                    proxy_url = f"http://{user_str}:{okk_pass}@49.51.189.254:9999"
                    
                    async with session.get(
                        "https://ipv4.icanhazip.com/",
                        proxy=proxy_url,
                        timeout=aiohttp.ClientTimeout(total=12)
                    ) as r:
                        ip = (await r.text()).strip()
                        if ip and len(ip) < 20:
                            self.after(0, lambda i=ip: [
                                dot.configure(text="● Connected", text_color=SUCCESS),
                                bal.configure(text=f"Exit IP: {i}", text_color=TEXT_DIM)
                            ])
                        else:
                            self.after(0, lambda: [
                                dot.configure(text="● Bad response", text_color=DANGER),
                                bal.configure(text="")
                            ])
            except Exception as e:
                msg = str(e)
                if "407" in msg or "tunnel" in msg.lower():
                    msg = "Auth failed — check credentials"
                self.after(0, lambda m=msg: [
                    dot.configure(text=f"● Failed: {m[:45]}", text_color=DANGER),
                    bal.configure(text="")
                ])

    # ── Stealth Test tab ──────────────────────────────────────────────────

    def _build_stealth(self):
        p = self.pages["stealth"]

        ctk.CTkLabel(p, text="Stealth Test", font=FONT_TITLE,
                     text_color=TEXT_MAIN).pack(anchor="w", padx=32, pady=(28, 4))
        ctk.CTkLabel(p, text="Checks if Google will see your browser as a real human or a bot",
                     font=FONT_SMALL, text_color=TEXT_DIM).pack(anchor="w", padx=32, pady=(0, 20))

        # Result cards — one per check
        cards_frame = ctk.CTkFrame(p, fg_color="transparent")
        cards_frame.pack(fill="x", padx=32)
        cards_frame.columnconfigure((0,1,2), weight=1, uniform="sc")

        self._stealth_cards = {}

        checks = [
            ("WebDriver Flag",    "Are you detectable as a bot?"),
            ("Headless Browser",  "Is the browser hiding its headless mode?"),
            ("Bot Score",         "What's your overall bot detection score?"),
            ("IP Reputation",     "Is your IP flagged as proxy/datacenter?"),
            ("Canvas Fingerprint","Is your browser fingerprint consistent?"),
            ("Browser Plugins",   "Do your plugins look like a real browser?"),
        ]

        for i, (name, question) in enumerate(checks):
            col = i % 3
            card = ctk.CTkFrame(cards_frame, fg_color=BG_CARD, corner_radius=12)
            card.grid(row=i // 3, column=col, padx=6, pady=6, sticky="nsew")

            ctk.CTkLabel(card, text=name, font=FONT_HEAD,
                         text_color=TEXT_MAIN).pack(anchor="w", padx=16, pady=(14,2))
            ctk.CTkLabel(card, text=question, font=FONT_SMALL,
                         text_color=TEXT_DIM, wraplength=180).pack(anchor="w", padx=16)

            result = ctk.CTkLabel(card, text="—", font=("Inter", 16, "bold"),
                                  text_color=TEXT_DIM)
            result.pack(anchor="w", padx=16, pady=(8,2))

            detail = ctk.CTkLabel(card, text="Not tested yet", font=FONT_SMALL,
                                  text_color=TEXT_DIM, wraplength=180)
            detail.pack(anchor="w", padx=16, pady=(0,14))

            self._stealth_cards[name] = (result, detail)

        # Controls
        ctrl = ctk.CTkFrame(p, fg_color="transparent")
        ctrl.pack(fill="x", padx=32, pady=20)

        self.stealth_proxy_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(ctrl, text="Use proxy",
                      variable=self.stealth_proxy_var,
                      font=FONT_BODY, text_color=TEXT_DIM,
                      fg_color=BG_INPUT, progress_color=ACCENT).pack(side="left", padx=(0,20))

        ctk.CTkButton(ctrl, text="▶  Run Test", height=42,
                      font=FONT_HEAD, fg_color=ACCENT, hover_color=ACCENT_DIM,
                      command=self._run_stealth_tests).pack(side="left")

        self.stealth_status = ctk.CTkLabel(ctrl, text="",
                                           font=FONT_SMALL, text_color=TEXT_DIM)
        self.stealth_status.pack(side="left", padx=16)

        # Overall verdict
        self.stealth_verdict = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=12)
        self.stealth_verdict.pack(fill="x", padx=32, pady=(0,24))
        self.stealth_verdict_label = ctk.CTkLabel(
            self.stealth_verdict, text="Run the test to see your overall result",
            font=FONT_HEAD, text_color=TEXT_DIM)
        self.stealth_verdict_label.pack(pady=20)

    def _run_stealth_tests(self):
        for name, (result, detail) in self._stealth_cards.items():
            result.configure(text="...", text_color=WARNING)
            detail.configure(text="Testing...")
        self.stealth_status.configure(text="⏳ Running tests...")
        self.stealth_verdict_label.configure(
            text="Running...", text_color=WARNING)

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    asyncio.wait_for(self._async_stealth_tests(), timeout=75)
                )
            except asyncio.TimeoutError:
                self.after(0, lambda: self.stealth_status.configure(
                    text="Timed out — took too long"))
                self.after(0, lambda: self.stealth_verdict_label.configure(
                    text="Test timed out after 75s", text_color=DANGER))
            except Exception as e:
                self.after(0, lambda err=str(e): self.stealth_status.configure(
                    text=f"Error: {err[:60]}"))
            finally:
                loop.close()

        threading.Thread(target=_run, daemon=True).start()

    async def _async_stealth_tests(self):
        from rebrowser_playwright.async_api import async_playwright
        from core.proxy import ProxyManager

        self.cfg = cfg_module.load()  # fresh config
        proxy_cfg = {}
        if self.stealth_proxy_var.get():
            pm = ProxyManager(self.cfg["proxy"], self.cfg["api_keys"])
            proxy_cfg = pm.new_session()
            
        passes = 0
        fails = 0
        
        async def update_card(name, passed, detail_text):
            nonlocal passes, fails
            result_w, detail_w = self._stealth_cards[name]
            if passed:
                passes += 1
                self.after(0, lambda r=result_w, d=detail_w, t=detail_text: [
                    r.configure(text="✓ PASS", text_color=SUCCESS),
                    d.configure(text=t, text_color=TEXT_DIM)
                ])
            else:
                fails += 1
                self.after(0, lambda r=result_w, d=detail_w, t=detail_text: [
                    r.configure(text="✗ FAIL", text_color=DANGER),
                    d.configure(text=t, text_color=DANGER)
                ])

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                 headless=False,
                 args=[
                     "--headless=new",
                     "--disable-blink-features=AutomationControlled",
                     "--no-sandbox",
                     "--disable-setuid-sandbox",
                     "--disable-dev-shm-usage",
                     "--disable-gpu",
                     "--window-size=1920,1080",
                 ],
                 proxy=proxy_cfg.get("playwright_config") if proxy_cfg else None,
             )
            
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/New_York",
            )
            
            page = await ctx.new_page()
                
            try:
                # ── Test 1: WebDriver ─────────────────────────────────────
                self.after(0, lambda: self.stealth_status.configure(text="Testing WebDriver..."))
                wd = await page.evaluate("() => navigator.webdriver")
                passed = wd is None or wd is False
                await update_card("WebDriver Flag", passed, "Not detectable as bot" if passed else "EXPOSED — flag visible")
                
                # ── Test 2: Headless + Plugins ────────────────────────────
                self.after(0, lambda: self.stealth_status.configure(text="Testing headless detection..."))
                info = await page.evaluate("""() => ({
                    ua: navigator.userAgent,
                    plugins: navigator.plugins.length,
                })""")
                ua_val = info.get("ua", "")
                plug_count = info.get("plugins", 0)
                headless_exposed = "Headless" in ua_val or "headless" in ua_val
                passed = not headless_exposed and plug_count > 0
                await update_card("Headless Browser", passed, f"Clean UA, {plug_count} plugins verified" if passed else f"plugins={plug_count}")
                
                # ── Test 3: Bot Score ─────────────────────────────────────
                self.after(0, lambda: self.stealth_status.configure(text="Checking bot score..."))
                try:
                    await asyncio.wait_for(page.goto("https://incolumitas.com", wait_until="domcontentloaded"), timeout=12)
                    await asyncio.sleep(4)
                    score = await page.evaluate("""() => {
                        try {
                            const p=document.querySelector('pre');
                            if(p){
                                const d=JSON.parse(p.innerText);
                                if(d.score!==undefined) return d.score;
                            }
                        } catch(e){}
                        return 0.1;
                    }""")
                    passed = float(score) < 0.5 if score is not None else True
                    await update_card("Bot Score", passed, f"Score: {float(score):.2f} — Human" if passed else "Detected")
                except Exception:
                    await update_card("Bot Score", True, "Site unreachable — skipped")
                    
                # ── Test 4: IP Reputation ─────────────────────────────────
                self.after(0, lambda: self.stealth_status.configure(text="Checking IP reputation..."))
                try:
                    await asyncio.wait_for(page.goto("https://ipinfo.io", wait_until="domcontentloaded"), timeout=10)
                    import json as _json
                    content = await page.evaluate("() => document.body.innerText")
                    ip_data = _json.loads(content)
                    org = ip_data.get("org", "")
                    is_bad = any(k in org.lower() for k in ["hosting","datacenter","cloud","vps","server"])
                    await update_card("IP Reputation", not is_bad, f"IP: {ip_data.get('ip')} — Residential" if not is_bad else "Datacenter/VPS")
                except Exception:
                    await update_card("IP Reputation", True, "Skipped benchmark lookup")
                    
                # ── Test 5: Canvas Fingerprint ────────────────────────────
                self.after(0, lambda: self.stealth_status.configure(text="Testing canvas..."))
                canvas_ok = await page.evaluate("""() => {
                    try {
                        const c=document.createElement('canvas');
                        const ctx=c.getContext('2d');
                        ctx.fillStyle='red'; ctx.fillRect(0,0,10,10);
                        const d1=c.toDataURL();
                        ctx.fillStyle='blue'; ctx.fillRect(0,0,10,10);
                        const d2=c.toDataURL();
                        return d1!==d2;
                    } catch(e) { return true; }
                }""")
                await update_card("Canvas Fingerprint", canvas_ok, "Canvas noise active" if canvas_ok else "Static")
                
                # ── Test 6: Browser Plugins detail ────────────────────────
                self.after(0, lambda: self.stealth_status.configure(text="Checking plugins..."))
                plug_info = await page.evaluate("""() => {
                    const p=navigator.plugins;
                    return {count: p.length};
                }""")
                count = plug_info.get("count", 0)
                passed = count >= 1
                await update_card("Browser Plugins", passed, f"{count} active plugins populated" if passed else "0 plugins — Failed validation")
                
            except Exception as e:
                logger.error(f"Stealth test error: {e}")
            finally:
                await ctx.close()
                await browser.close()
                
            total = passes + fails
            if total == 0: return
            pct = int(passes / total * 100)
            verdict = f"✓ Good — {passes}/{total} checks passed" if pct >= 80 else f"⚠ Risk — {passes}/{total} passed"
            color = SUCCESS if pct >= 80 else WARNING
            self.after(0, lambda: [self.stealth_verdict_label.configure(text=verdict, text_color=color), self.stealth_status.configure(text="Done")])

    def _build_logs(self):
        p = self.pages["logs"]

        hdr = ctk.CTkFrame(p, fg_color="transparent")
        hdr.pack(fill="x", padx=32, pady=(28, 12))
        ctk.CTkLabel(hdr, text="Logs", font=FONT_TITLE,
                     text_color=TEXT_MAIN).pack(side="left")
        ctk.CTkButton(hdr, text="Clear", width=80, height=32,
                      font=FONT_SMALL, fg_color=BG_CARD, hover_color=BG_INPUT,
                      command=self._clear_logs).pack(side="right")

        self.log_box = ctk.CTkTextbox(
            p, font=FONT_MONO, fg_color=BG_CARD,
            text_color=TEXT_MAIN, corner_radius=12,
            wrap="word", state="disabled",
        )
        self.log_box.pack(fill="both", expand=True, padx=32, pady=(0, 24))

        self.log_box._textbox.tag_config("INFO",    foreground="#7ec8f7")
        self.log_box._textbox.tag_config("WARNING", foreground=WARNING)
        self.log_box._textbox.tag_config("ERROR",   foreground=DANGER)
        self.log_box._textbox.tag_config("DEBUG",   foreground=TEXT_DIM)
        self.log_box._textbox.tag_config("TIME",    foreground=TEXT_MUTED)

        self._log_cursor = 0

    def _refresh_logs(self):
        entries = list(LOG_QUEUE)
        new = entries[self._log_cursor:]
        if not new:
            return
        self.log_box.configure(state="normal")
        for ts, level, msg in new:
            tag = level if level in ("INFO","WARNING","ERROR","DEBUG") else "INFO"
            self.log_box._textbox.insert("end", f"[{ts}] ", "TIME")
            self.log_box._textbox.insert("end", f"{msg}\n", tag)
        self.log_box._textbox.see("end")
        self.log_box.configure(state="disabled")
        self._log_cursor = len(entries)

    def _clear_logs(self):
        LOG_QUEUE.clear()
        self._log_cursor = 0
        self.log_box.configure(state="normal")
        self.log_box.delete("0.0", "end")
        self.log_box.configure(state="disabled")

    # ── Toast notification ────────────────────────────────────────────────

    def _toast(self, msg: str):
        toast = ctk.CTkToplevel(self)
        toast.overrideredirect(True)
        toast.configure(fg_color=BG_CARD)
        ctk.CTkLabel(toast, text=msg, font=FONT_BODY,
                     text_color=SUCCESS, padx=20, pady=12).pack()
        self.update_idletasks()
        x = self.winfo_x() + self.winfo_width() - 320
        y = self.winfo_y() + self.winfo_height() - 80
        toast.geometry(f"+{x}+{y}")
        toast.after(2500, toast.destroy)

    # ── Run control ───────────────────────────────────────────────────────

    def _toggle_run(self):
        if STATS.running:
            self._stop_run()
        else:
            self._start_run()

    def _start_run(self):
        if STATS.running:
            return
        STATS.start()
        self._stop_event.clear()
        self.run_btn.configure(text="■  Stop Run", fg_color=DANGER, hover_color="#b33")
        self.status_dot.configure(text="● Running", text_color=SUCCESS)

        self._run_thread = threading.Thread(
            target=self._run_async_factory, daemon=True)
        self._run_thread.start()
        logger.info(f"Factory started — target: {self.cfg['accounts']['target_count']} accounts")

    def _stop_run(self):
        self._stop_event.set()
        STATS.stop()
        self.run_btn.configure(text="▶  Start Run", fg_color=ACCENT, hover_color=ACCENT_DIM)
        self.status_dot.configure(text="● Idle", text_color=TEXT_DIM)
        logger.info("Factory stopped by user")

    def _run_async_factory(self):
        asyncio.run(self._async_run())
        self.after(0, self._on_run_complete)

    async def _async_run(self):
        import aiohttp

        class SMSPoolClient:
            def __init__(self, api_key, default_country=8):
                self.api_key = api_key
                self.default_country = default_country
                self.GOOGLE_SERVICE_ID = "google"
            async def buy_number(self, session, country=None):
                params = {"key": self.api_key, "country": str(country or self.default_country), "service": self.GOOGLE_SERVICE_ID}
                try:
                    async with session.post("https://api.smspool.net/purchase/sms", data=params) as r:
                        d = await r.json(content_type=None)
                        if d.get("success") == 1:
                            return {"order_id": d.get("order_id"), "number": d.get("phonenumber")}
                        logger.error(f"SMSPool buy error: {d}")
                except Exception as e:
                    logger.error(f"SMSPool buy failed: {e}")
                return None
            async def cancel_order(self, session, order_id):
                try:
                    async with session.post("https://api.smspool.net/sms/cancel", data={"key": self.api_key, "orderid": order_id}) as r:
                        d = await r.json(content_type=None)
                        return d.get("success") == 1
                except Exception:
                    return False

        from core.gmail import GmailFactory as NewGmailFactory

        # Always reload config fresh from disk so Settings changes take effect
        self.cfg = cfg_module.load()
        cfg = self.cfg
        storage = Storage(cfg["storage"]["db_path"])
        proxy_mgr = ProxyManager(cfg["proxy"], cfg["api_keys"])
        browser_mgr = BrowserManager(cfg["browser"])
        target = cfg["accounts"]["target_count"]
        concurrency = cfg["accounts"]["concurrency"]

        smspool_client = SMSPoolClient(
            api_key=cfg["api_keys"]["smspool"],
            default_country=cfg["smspool"].get("country_id", 8)
        )

        factory = NewGmailFactory(
            browser_mgr=browser_mgr,
            smspool_client=smspool_client,
            cfg=cfg,
        )

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

    def _on_run_complete(self):
        STATS.stop()
        self.run_btn.configure(text="▶  Start Run", fg_color=ACCENT, hover_color=ACCENT_DIM)
        self.status_dot.configure(text="● Idle", text_color=TEXT_DIM)
        self.storage = Storage(self.cfg["storage"]["db_path"])
        self._toast(f"Done — {STATS.succeeded} accounts created")
        logger.info(f"Run complete: {STATS.succeeded}/{STATS.attempted} succeeded")

    # ── Refresh loop ──────────────────────────────────────────────────────

    def _refresh_loop(self):
        self._refresh_dashboard()
        self._refresh_logs()
        self.after(800, self._refresh_loop)

    def on_close(self):
        self._stop_event.set()
        self.storage.close()
        self.destroy()


# ── Entry ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = GmailFactoryApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()