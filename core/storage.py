import sqlite3
import csv
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger("storage")

@dataclass
class Account:
    email: str
    password: str
    phone: str
    phone_order_id: str
    proxy: str
    status: str = "created"
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


@dataclass
class PhoneRecord:
    order_id: str
    number: str
    country: str
    uses: int = 0
    active: bool = True


class Storage:
    # *knows every secret this factory makes — and keeps them all*

    def __init__(self, db_path: str = "accounts.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                password TEXT,
                phone TEXT,
                phone_order_id TEXT,
                proxy TEXT,
                status TEXT DEFAULT 'created',
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS phone_numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE,
                number TEXT,
                country TEXT,
                uses INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                purchased_at TEXT
            );

            CREATE TABLE IF NOT EXISTS run_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT,
                attempted INTEGER,
                succeeded INTEGER,
                failed INTEGER,
                numbers_used INTEGER,
                proxies_used INTEGER
            );
        """)
        self.conn.commit()

    def save_account(self, acc: Account) -> bool:
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO accounts
                   (email, password, phone, phone_order_id, proxy, status, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (acc.email, acc.password, acc.phone,
                 acc.phone_order_id, acc.proxy, acc.status, acc.created_at)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"save_account error: {e}")
            return False

    def save_phone(self, phone: PhoneRecord) -> bool:
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO phone_numbers
                   (order_id, number, country, uses, active, purchased_at)
                   VALUES (?,?,?,?,?,?)""",
                (phone.order_id, phone.number, phone.country,
                 phone.uses, int(phone.active), datetime.now().isoformat())
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"save_phone error: {e}")
            return False

    def increment_phone_use(self, order_id: str):
        self.conn.execute(
            "UPDATE phone_numbers SET uses = uses + 1 WHERE order_id = ?",
            (order_id,)
        )
        self.conn.commit()

    def deactivate_phone(self, order_id: str):
        self.conn.execute(
            "UPDATE phone_numbers SET active = 0 WHERE order_id = ?",
            (order_id,)
        )
        self.conn.commit()

    def get_reusable_phone(self, max_uses: int) -> Optional[dict]:
        row = self.conn.execute(
            """SELECT * FROM phone_numbers
               WHERE active = 1 AND uses < ?
               ORDER BY uses DESC LIMIT 1""",
            (max_uses,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_accounts(self) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM accounts ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        success = self.conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE status='created'"
        ).fetchone()[0]
        failed = self.conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE status='failed'"
        ).fetchone()[0]
        numbers = self.conn.execute(
            "SELECT COUNT(*) FROM phone_numbers"
        ).fetchone()[0]
        active_numbers = self.conn.execute(
            "SELECT COUNT(*) FROM phone_numbers WHERE active=1"
        ).fetchone()[0]
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "numbers_bought": numbers,
            "numbers_active": active_numbers,
        }

    def update_account_status(self, email: str, status: str):
        self.conn.execute(
            "UPDATE accounts SET status=? WHERE email=?",
            (status, email)
        )
        self.conn.commit()

    def export_csv(self, path: str = "accounts_export.csv"):
        accounts = self.get_all_accounts()
        if not accounts:
            return 0
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=accounts[0].keys())
            writer.writeheader()
            writer.writerows(accounts)
        return len(accounts)

    def close(self):
        self.conn.close()
