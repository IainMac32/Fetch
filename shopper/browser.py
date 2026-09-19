import asyncio
import logging
import re
import time
from queue import Empty
from urllib.parse import urlsplit

from browserbase import Browserbase
from playwright.sync_api import Error as PlaywrightError, sync_playwright

LOG = logging.getLogger(__name__)
ACCOUNT_URL = "https://www.doordash.com/consumer/edit_profile/"
LOGIN_URL = "https://www.doordash.com/consumer/login/?redirect=%2Fconsumer%2Fedit_profile%2F"


class LoginExpired(Exception):
    pass


class LoginCancelled(Exception):
    pass


def is_doordash(url):
    host = urlsplit(url).hostname or ""
    return host == "doordash.com" or host.endswith(".doordash.com")


def login_required(page):
    url = urlsplit(page.url)
    if not is_doordash(page.url):
        return True  # Includes external identity-provider pages.
    if re.search(r"/(login|signin|sign-in|auth)(/|$)", url.path, re.I):
        return True
    return page.locator('input[type="password"]:visible, input[autocomplete="one-time-code"]:visible').count() > 0


def signed_in(page):
    """Positive evidence only; closing a dialog or leaving /login is insufficient."""
    if login_required(page):
        return False
    if page.get_by_role("button", name=re.compile(r"^(sign out|log out)$", re.I)).count():
        return page.get_by_role("button", name=re.compile(r"^(sign out|log out)$", re.I)).first.is_visible()
    if page.get_by_role("link", name=re.compile(r"^(sign out|log out)$", re.I)).count():
        return page.get_by_role("link", name=re.compile(r"^(sign out|log out)$", re.I)).first.is_visible()
    # On the protected profile page, check that the account form is populated.
    # Return a boolean only; never extract the user's profile data into logs or an LLM.
    if not urlsplit(page.url).path.rstrip("/").endswith("/consumer/edit_profile"):
        return False
    return page.evaluate("""() => {
        const visible = el => el.getClientRects().length > 0;
        const fields = [...document.querySelectorAll('input')].filter(visible);
        const email = fields.some(el => /email/i.test([el.type, el.name, el.id,
            el.autocomplete, el.getAttribute('aria-label')].join(' ')) && /.+@.+/.test(el.value));
        const name = fields.some(el => /first.?name|given-name|last.?name|family-name/i.test(
            [el.name, el.id, el.autocomplete, el.placeholder, el.getAttribute('aria-label')].join(' '))
            && el.value.trim().length > 0);
        return email && name;
    }""")


def login_fields(page):
    """Locate a visible DoorDash login identifier/password pair."""
    identifier = page.locator(
        'input[type="email"]:visible, input[type="tel"]:visible, '
        'input[autocomplete="username"]:visible'
    )
    password = page.locator('input[type="password"]:visible')
    if identifier.count() != 1 or password.count() != 1:
        return None
    return identifier, password


def verification_field(page):
    field = page.locator(
        'input[autocomplete="one-time-code"]:visible, input[name*="otp" i]:visible, '
        'input[name*="verification" i]:visible'
    )
    if field.count() == 1:
        return field
    digits = page.locator('input[maxlength="1"]:visible')
    return digits if 4 <= digits.count() <= 8 else None


def fill_doordash_credentials(page, login, password):
    if not is_doordash(page.url):
        raise RuntimeError("DoorDash opened an external sign-in provider")
    fields = login_fields(page)
    if not fields:
        raise RuntimeError("Could not identify DoorDash's sign-in fields")
    identifier, password_field = fields
    identifier.fill(login)
    password_field.fill(password)
    submit = page.get_by_role("button", name=re.compile(r"sign in|continue|log in", re.I))
    if submit.count() != 1 or not submit.is_visible() or not submit.is_enabled():
        raise RuntimeError("Could not identify DoorDash's sign-in button")
    submit.click()


def wait_for_login(page, job, *, clock=time.monotonic):
    """Submit stored credentials and wait for login or a user-provided MFA code."""
    next_check = 0
    while clock() < job.deadline:
        if job.cancelled.is_set():
            raise LoginCancelled()
        try:
            action, value = job.commands.get(timeout=0.2)
        except Empty:
            action, value = None, None
        if signed_in(page):
            return
        if action is None and clock() >= next_check and verification_field(page):
            if job.state != "awaiting_code":
                job.transition("awaiting_code", "Enter the verification code DoorDash sent you.")
        elif action is None and clock() >= next_check and login_fields(page) and job.state in {"submitting", "verifying_code"}:
            job.transition("awaiting_credentials", "DoorDash did not accept the sign-in yet. Check your details and try again.")
        if action == "credentials":
            credentials = value
            job.transition("submitting", "Signing in to DoorDash…")
            try:
                fill_doordash_credentials(page, credentials["login"], credentials["password"])
            finally:
                credentials.clear()
            job.set_message("DoorDash is checking your sign-in…")
        elif action == "code":
            code = value
            fields = verification_field(page)
            if fields is None:
                job.set_message("DoorDash has not shown a verification field yet.")
                continue
            try:
                count = fields.count()
                if count == 1:
                    fields.fill(code)
                else:
                    for index, digit in enumerate(code):
                        fields.nth(index).fill(digit)
                submit = page.get_by_role("button", name=re.compile(r"verify|continue|submit|confirm", re.I))
                if submit.count() == 1 and submit.is_visible() and submit.is_enabled():
                    job.transition("verifying_code", "Checking the DoorDash code…")
                    submit.click()
                elif count > 1:
                    job.transition("verifying_code", "Checking the DoorDash code…")
                else:
                    job.set_message("Could not find DoorDash's verification button.")
            finally:
                code = ""
        if clock() >= next_check:
            try:
                if signed_in(page):
                    return
            except PlaywrightError:
                if page.is_closed():
                    raise
                # A document can be replaced while the user submits login/MFA.
            next_check = clock() + 2
    raise LoginExpired()


class DoorDashBrowser:
    def __init__(self, settings, store, *, client=None):
        self.settings = settings
        self.store = store
        self.client = client or Browserbase(api_key=settings.browserbase_api_key, timeout=30, max_retries=1)

    def run(self, job, notify, *, after_login=None, on_status=None):
        session = browser = None
        cfg = self.settings
        project = {"project_id": cfg.browserbase_project_id} if cfg.browserbase_project_id else {}
        def status(message):
            if on_status:
                on_status(message)
        try:
            saved = self.store.context_for(job.user_key)
            if saved:
                context_id, reusable_at = saved
                status("Reusing the saved Browserbase context…")
                if job.cancelled.wait(max(0, reusable_at - time.time())):
                    raise LoginCancelled()
            else:
                status("Creating a Browserbase context…")
                context_id = self.client.contexts.create(**project).id
                self.store.save_context(job.user_key, context_id)
            if job.cancelled.is_set():
                raise LoginCancelled()
            status("Starting a Browserbase session…")
            session = self.client.sessions.create(
                **project, keep_alive=True, api_timeout=cfg.session_timeout,
                browser_settings={"context": {"id": context_id, "persist": True},
                                  "record_session": False, "log_session": False,
                                  "viewport": {"width": 390, "height": 720}},
            )
            # A process crash must not immediately reuse a context still in use remotely.
            self.store.defer_context(job.user_key, time.time() + cfg.session_timeout + 5)
            with sync_playwright() as playwright:
                try:
                    status("Connecting to the remote browser…")
                    browser = playwright.chromium.connect_over_cdp(session.connect_url, timeout=30000)
                    context = browser.contexts[0]
                    page = context.pages[0] if context.pages else context.new_page()
                    page.set_default_timeout(10000)
                    already_signed_in = False
                    if saved:
                        status("Checking the saved DoorDash login…")
                        page.goto(ACCOUNT_URL, wait_until="domcontentloaded", timeout=45000)
                        # Give a returning session a short bounded chance to restore its cookies.
                        for _ in range(10):
                            if job.cancelled.is_set():
                                raise LoginCancelled()
                            if signed_in(page):
                                already_signed_in = True
                                break
                            page.wait_for_timeout(300)
                    if not already_signed_in:
                        if not saved or not login_required(page):
                            status("Opening the DoorDash sign-in page…")
                            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
                        job.begin_credentials(cfg.sign_in_timeout)
                        notify("Connect your DoorDash account here:\n" + job.link
                               + f"This link expires in {cfg.sign_in_timeout // 60} minutes.")
                        saved_credentials = self.store.credentials_for(job.user_key)
                        if saved_credentials:
                            status("Signing in with the saved DoorDash credentials…")
                            login, password = saved_credentials
                            try:
                                job.transition("submitting", "Signing in to DoorDash…")
                                fill_doordash_credentials(page, login, password)
                                job.set_message("DoorDash is checking your sign-in…")
                            except Exception:
                                job.transition("awaiting_credentials", "Could not sign in automatically. Check your saved details.")
                            finally:
                                login = password = ""
                                saved_credentials = None
                        wait_for_login(page, job)
                    # Revoke input before giving the browser back to automation.
                    job.transition("resuming", "DoorDash is connected. Finishing up…")
                    if job.cancelled.is_set():
                        raise LoginCancelled()
                    if after_login:
                        after_login(session.id, page)
                finally:
                    if browser:
                        browser.close()
        finally:
            if session:
                try:
                    self.client.sessions.update(session.id, **project, status="REQUEST_RELEASE")
                    # Context persistence is asynchronous after release.
                    self.store.defer_context(job.user_key, time.time() + 5)
                except Exception as exc:
                    LOG.warning("Browser release failed (%s); session timeout will reclaim it", type(exc).__name__)


def stagehand_search(settings, session_id, page, query):
    """Small continuation demo: locate the search field using Stagehand, then type.

    Called only after verified login. No agent runs during password entry, and
    this function has no cart or checkout actions.
    """
    if not settings.model_api_key or not settings.stagehand_model:
        raise ValueError("Set MODEL_API_KEY and STAGEHAND_MODEL for the search demo")
    page.goto("https://www.doordash.com/", wait_until="domcontentloaded", timeout=30000)
    if login_required(page):
        raise RuntimeError("DoorDash requested sign-in again")
    selector = asyncio.run(_search_selector(settings, session_id, page.url))
    field = page.locator(selector)
    if field.count() != 1 or not field.is_visible() or not field.is_editable():
        raise RuntimeError("DoorDash search field could not be identified")
    field.fill(query)
    field.press("Enter")


async def _search_selector(settings, session_id, page_url):
    from stagehand import Stagehand, browserbase

    browser = await browserbase.connect(api_key=settings.browserbase_api_key, session_id=session_id)
    agent = None
    try:
        agent = await Stagehand.create(browser=browser, model=settings.stagehand_model,
                                       model_api_key=settings.model_api_key, logging={"level": "off"})
        pages = await browser.context.pages()
        matches = [page for page in pages if await page.url() == page_url]
        if len(matches) != 1:
            raise RuntimeError("Could not identify the original DoorDash tab")
        result = await agent.observe("Find the product or restaurant search text input. "
                                     "Do not select a delivery address, email, or password field.", page=matches[0])
        if not result.data:
            raise RuntimeError("DoorDash needs an address or its search field is unavailable")
        return result.data[0].selector
    finally:
        if agent:
            await agent.close()
        await browser.close()  # Attaching to an existing session does not release it.
