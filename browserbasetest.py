from browserbase import Browserbase
from playwright.sync_api import sync_playwright

BROWSERBASE_API_KEY = ""

bb = Browserbase(api_key=BROWSERBASE_API_KEY)

with sync_playwright() as p:

    session = bb.sessions.create()

    browser = p.chromium.connect_over_cdp(
        session.connect_url
    )

    page = browser.contexts[0].pages[0]

    # Open DoorDash
    page.goto("https://www.doordash.com/")

    page.wait_for_timeout(5000)

    # Print the page so we can see what DoorDash calls its
    # search buttons/fields
    print(page.locator("body").inner_text()[:10000])

    # Example: find a search box
    search = page.get_by_placeholder("Search")

    search.fill("banana")
    search.press("Enter")

    page.wait_for_timeout(3000)

    print("===== SEARCH RESULTS =====")
    print(page.locator("body").inner_text()[:10000])

    input("Press Enter to continue...")

    browser.close()