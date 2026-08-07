# Gmail Factory

Bulk Gmail creator — SMSPool (Indonesia) + DataImpulse residential proxies + CapSolver

## Setup

```bash
# Install deps
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Edit your API keys
nano config.json
```

## Config (config.json)

Fill these before running:
- `api_keys.smspool` — your SMSPool API key
- `api_keys.capsolver` — your CapSolver API key  
- `api_keys.dataimpulse_user` — DataImpulse username
- `api_keys.dataimpulse_pass` — DataImpulse password

Everything else is editable in-app via the Settings menu.

## Run

```bash
python main.py
```

## Menu

1. **Start factory run** — launches workers, live dashboard
2. **View / export accounts** — browse DB, export CSV
3. **Settings** — change everything without touching JSON
4. **Check API balances** — SMSPool + CapSolver balance check
5. **Exit**

## File structure

```
gmail_factory/
├── main.py          # TUI entry point
├── config.py        # Config loader/saver
├── config.json      # All settings (editable in-app too)
├── requirements.txt
├── accounts.db      # SQLite (auto-created)
├── accounts_export.csv  # CSV export (auto on finish)
└── core/
    ├── smspool.py   # SMSPool API + OTP polling
    ├── proxy.py     # DataImpulse session manager
    ├── browser.py   # Playwright + stealth JS
    ├── gmail.py     # Full signup flow
    ├── captcha.py   # CapSolver integration
    └── storage.py   # SQLite + CSV
```

## Cost estimate

- Indonesia numbers: ~$0.10/number, 4-5 accounts each → ~$0.02-0.025/account
- DataImpulse proxies: ~$1/GB, ~5-10MB/account → ~$0.005-0.01/account
- CapSolver: ~$0.001/solve (if CAPTCHA appears)

**~$0.025-0.035 per account** at current rates.
