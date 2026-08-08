import asyncio
import logging
from typing import Optional, Dict, Any
from rebrowser_playwright.async_api import Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger("gmail_factory")

# GmailFactory expects a smspool-like client with:
# - buy_number(session, country=None) -> dict or None
# - poll_sms(session, order_id) -> str or None
# - cancel_order(session, order_id) -> bool
#
# The core.smspool.SMSPool provided in this repo matches that interface.

class GmailFactory:
    def __init__(self, browser_mgr, smspool_client=None, cfg: Optional[Dict[str, Any]] = None):
        """
        browser_mgr: BrowserManager instance
        smspool_client: optional SMSPool-like client. If None, this class will attempt to import and construct one from core.smspool using cfg.
        cfg: configuration dict
        """
        self.browser_mgr = browser_mgr
        self.smspool = smspool_client
        self.cfg = cfg or {}

        if self.smspool is None:
            try:
                from core.smspool import SMSPool
                self.smspool = SMSPool(api_key=self.cfg.get("api_keys", {}).get("smspool", ""), cfg=self.cfg.get("smspool", {}))
            except Exception:
                logger.warning("No SMSPool client available; phone verification will fail if requested")
                self.smspool = None

    async def create_one(self, account_data: dict) -> bool:
        import aiohttp
        from rebrowser_playwright.async_api import async_playwright

        proxy = account_data.get("proxy", {})
        logger.info(f"create_one: launching playwright...")

        async with async_playwright() as p:
            logger.info(f"create_one: playwright started, launching browser...")
            browser, ctx = await self.browser_mgr.new_context(p, proxy)
            logger.info(f"create_one: browser launched, opening page...")
            page = await self.browser_mgr.new_page(ctx)
            logger.info(f"create_one: page opened, navigating to signup...")

            try:
                # Navigate to signup
                await page.goto(
                    "https://accounts.google.com/signup/v2/createaccount?flowName=GlifWebSignIn&flowEntry=SignUp",
                    wait_until="networkidle"
                )
                await asyncio.sleep(1.5)

                import random, string as _str
                fname = account_data.get("first_name", "James")
                lname = account_data.get("last_name", "Smith")
                username = account_data.get("username", "user123")
                password = (
                    random.choice(_str.ascii_uppercase) +
                    random.choice(_str.digits) +
                    random.choice("!@#$") +
                    ''.join(random.choices(_str.ascii_letters + _str.digits, k=10))
                )

                # Name step
                try:
                    await page.wait_for_selector('input[name="firstName"]', timeout=8000)
                    await self.browser_mgr.human_type(page, 'input[name="firstName"]', fname)
                    await self.browser_mgr.human_type(page, 'input[name="lastName"]', lname)
                    await page.click('button:has-text("Next")')
                    await asyncio.sleep(1.5)
                except Exception as e:
                    logger.error(f"Name step failed: {e}")
                    return False

                # Birthday + Gender
                try:
                    await page.wait_for_selector('select#month', timeout=8000)
                    await page.select_option('select#month', str(random.randint(1, 12)))
                    await self.browser_mgr.human_type(page, 'input#day', str(random.randint(1, 28)))
                    await self.browser_mgr.human_type(page, 'input#year', str(random.randint(1990, 2000)))
                    await page.select_option('select#gender', random.choice(["1", "2"]))
                    await page.click('button:has-text("Next")')
                    await asyncio.sleep(1.5)
                except Exception as e:
                    logger.error(f"Birthday step failed: {e}")
                    return False

                # Username
                try:
                    await page.wait_for_selector('input[name="Username"]', timeout=8000)
                    await self.browser_mgr.human_type(page, 'input[name="Username"]', username)
                    await page.click('button:has-text("Next")')
                    await asyncio.sleep(1.5)
                    if await page.query_selector('text="That username is taken"'):
                        username = username + str(random.randint(10, 99))
                        await page.fill('input[name="Username"]', username)
                        await page.click('button:has-text("Next")')
                        await asyncio.sleep(1.5)
                except Exception as e:
                    logger.error(f"Username step failed: {e}")
                    return False

                # Password
                try:
                    await page.wait_for_selector('input[name="Passwd"]', timeout=8000)
                    await self.browser_mgr.human_type(page, 'input[name="Passwd"]', password)
                    await self.browser_mgr.human_type(page, 'input[name="PasswdAgain"]', password)
                    await page.click('button:has-text("Next")')
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"Password step failed: {e}")
                    return False

                # JIT phone verification
                async with aiohttp.ClientSession() as session:
                    success = await self._handle_jit_phone_verification(page, session)

                if not success:
                    return False

                # Skip optional steps + agree terms
                for btn_text in ["Skip", "Not now", "I agree", "Agree", "Accept", "Continue", "Done"]:
                    try:
                        btn = await page.query_selector(f'button:has-text("{btn_text}")')
                        if btn:
                            await asyncio.sleep(0.5)
                            await btn.click()
                            await asyncio.sleep(1.2)
                    except Exception:
                        pass

                logger.info(f"Account flow completed: {username}@gmail.com")
                return True

            except Exception as e:
                logger.exception(f"Worker runtime error: {e}")
                return False
            finally:
                try:
                    await ctx.close()
                except Exception:
                    pass
                try:
                    await browser.close()
                except Exception:
                    pass

    async def _handle_jit_phone_verification(self, page: Page, session) -> bool:
        """
        Procures an SMS number strictly on-demand when the browser reaches the phone challenge DOM.
        Uses the unified SMSPool client API.
        """
        phone_selectors = "input#phoneNumberId, input[type='tel'], input[name='phoneNumber']"

        # Step A: Wait to see if Google actually requests phone verification
        try:
            logger.info("Checking if phone verification screen is active...")
            await page.wait_for_selector(phone_selectors, state="visible", timeout=12000)
            logger.info("Phone verification screen detected. Executing on-demand SMS purchase...")
        except PlaywrightTimeout:
            logger.info("No phone verification required by Google. Skipping SMS purchase entirely.")
            return True
        except Exception:
            # selector wait may raise other exceptions; treat as "no phone" to be safe
            logger.info("Phone input not detected; skipping SMS purchase.")
            return True

        # Step B: Purchase number on-demand now that we know we need it
        if not self.smspool:
            logger.error("No SMSPool client available to buy number")
            return False

        order = await self.smspool.buy_number(session, country=self.cfg.get("smspool", {}).get("country", 8))
        if not order:
            logger.error("Could not obtain number from SMSPool. Aborting flow.")
            return False

        order_id = order.get("order_id")
        phone_num = order.get("number")

        # Step C: Input phone number and handle OTP polling safely
        try:
            await self.browser_mgr.human_type(page, phone_selectors, str(phone_num))
            
            # Submit phone number
            next_btn = "button:has-text('Next'), button:has-text('Send'), #next-button"
            await page.click(next_btn)

            # Check for immediate carrier rejection
            error_selector = "div[type='error'], .o6982b"
            try:
                if await page.is_visible(error_selector):
                    logger.warning(f"Google rejected phone number +{phone_num}. Cancelling order to refund balance...")
                    await self.smspool.cancel_order(session, order_id)
                    return False
            except Exception:
                # ignore selector checking problems
                pass

            # Wait for OTP input box
            otp_input_selector = "input[name='code'], input#code, input[type='number']"
            await page.wait_for_selector(otp_input_selector, state="visible", timeout=15000)

            # Poll for OTP code
            logger.info(f"Polling SMS code for order {order_id}...")
            code = await self.smspool.poll_sms(session, order_id)

            if not code:
                logger.warning(f"OTP timeout for order {order_id}. Cancelling order...")
                await self.smspool.cancel_order(session, order_id)
                return False

            # Submit OTP Code
            await self.browser_mgr.human_type(page, otp_input_selector, str(code))
            await page.click("button:has-text('Verify'), button:has-text('Next')")
            return True

        except Exception as e:
            logger.exception(f"Error inside verification block: {e}")
            try:
                await self.smspool.cancel_order(session, order_id)
            except Exception:
                pass
            return False
