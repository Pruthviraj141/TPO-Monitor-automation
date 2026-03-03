import os
import time
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import os
import requests

def send_to_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }

    response = requests.post(url, data=payload)

    if response.status_code != 200:
        print("Failed to send Telegram message:", response.text)

load_dotenv()

LOGIN_URL = "https://tpo.vierp.in/"
COMPANY_URL = "https://tpo.vierp.in/apply_company"
ALLOWED_BASE = "https://tpo.vierp.in"
DEBUG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug")


def debug_dump(page, label="debug"):
    """Save screenshot + HTML for debugging CI failures."""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    try:
        page.screenshot(path=os.path.join(DEBUG_DIR, f"{label}.png"), full_page=True)
        print(f"  [DEBUG] Screenshot saved: debug/{label}.png")
    except Exception as e:
        print(f"  [DEBUG] Screenshot failed: {e}")
    try:
        html = page.content()
        with open(os.path.join(DEBUG_DIR, f"{label}.html"), "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [DEBUG] HTML saved: debug/{label}.html")
    except Exception as e:
        print(f"  [DEBUG] HTML dump failed: {e}")


def safe_check(url):
    """Block any navigation outside allowed domain."""
    if not url.startswith(ALLOWED_BASE):
        raise Exception(f"Blocked navigation outside allowed domain: {url}")


def logout_and_close(page, browser):
    """Force logout and close browser no matter what."""
    try:
        page.context.clear_cookies()
        page.evaluate("window.localStorage.clear(); window.sessionStorage.clear();")
    except Exception:
        pass
    try:
        browser.close()
    except Exception:
        pass
    print("\n[CLEANUP] Browser closed and session cleared.")


def scrape_companies():
    companies = []
    browser = None
    page = None

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )

            # --- Stealth: mask headless fingerprints BEFORE any page loads ---
            context.add_init_script("""
                // Hide webdriver flag
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                });

                // Fake chrome runtime
                window.chrome = { runtime: {} };

                // Fake plugins (headless has 0)
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });

                // Fake languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                });

                // Fake permissions API
                const origQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (params) =>
                    params.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : origQuery(params);
            """)

            page = context.new_page()

            # Log all JS console messages & errors for debugging
            page.on("console", lambda msg: print(f"  [CONSOLE {msg.type}] {msg.text}"))
            page.on("pageerror", lambda err: print(f"  [PAGE ERROR] {err}"))

            # ===================== STEP 1: LOGIN =====================
            print("[STEP 1] Opening login page...")
            page.goto(LOGIN_URL, wait_until="load", timeout=120000)
            safe_check(page.url)
            print(f"  Page loaded (load event). URL = {page.url}")

            # Wait for network to settle so Vue/Vuetify JS bundles finish
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
                print("  Network idle reached.")
            except Exception:
                print("  Network idle timed out — continuing anyway.")

            # Wait for Vue to actually mount — check DOM for any <input>
            print("  Waiting for Vue to mount (checking for input elements)...")
            try:
                page.wait_for_function(
                    "() => document.querySelectorAll('input').length > 0",
                    timeout=60000,
                )
                print("  Vue mounted — inputs detected in DOM.")
            except Exception:
                # If still nothing, dump the raw HTML for debugging
                debug_dump(page, "vue-not-mounted")
                raw_html = page.content()
                print(f"  [DEBUG] Page HTML length: {len(raw_html)}")
                print(f"  [DEBUG] Has <input>: {'<input' in raw_html}")
                print(f"  [DEBUG] Has v-app: {'v-app' in raw_html}")
                print(f"  [DEBUG] Has #app: {'id=\"app\"' in raw_html}")
                raise Exception(
                    "Vue app did not mount — no input elements appeared after 60s. "
                    "Check debug/vue-not-mounted.png and .html"
                )

            page.wait_for_timeout(2000)

            # Now find the username input with multiple selectors
            INPUT_SELECTORS = [
                "input[type='text']",
                "input[type='email']",
                ".v-text-field input",
                ".v-input input",
                "input:not([type='password']):not([type='hidden'])",
                "input",
            ]

            print("  Waiting for username input to render...")
            username_input = None
            for sel in INPUT_SELECTORS:
                try:
                    page.wait_for_selector(sel, state="visible", timeout=5000)
                    username_input = sel
                    print(f"  Username input found with selector: {sel}")
                    break
                except Exception:
                    print(f"    Selector '{sel}' not found, trying next...")

            if not username_input:
                debug_dump(page, "login-page-no-input")
                raise Exception(
                    "Could not find any username input on the login page. "
                    "Check debug/login-page-no-input.png and .html"
                )

            # Extra wait for Vue hydration
            page.wait_for_timeout(2000)

            username = os.environ["TPO_USERNAME"]
            password = os.environ["TPO_PASSWORD"]

            # Use native JS setter to fill Vuetify inputs (bypasses fill timeout)
            print("  Filling username...")
            page.evaluate("""(args) => {
                const [val, sel] = args;
                const el = document.querySelector(sel);
                if (!el) throw new Error('username input not found with: ' + sel);
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, val);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""", [username, username_input])

            print("  Filling password...")
            # Try multiple password selectors
            PWD_SELECTORS = [
                "input[type='password']",
                ".v-text-field input[type='password']",
            ]
            pwd_sel = None
            for sel in PWD_SELECTORS:
                if page.query_selector(sel):
                    pwd_sel = sel
                    break
            if not pwd_sel:
                debug_dump(page, "login-page-no-password")
                raise Exception("Could not find password input.")

            page.evaluate("""(args) => {
                const [val, sel] = args;
                const el = document.querySelector(sel);
                if (!el) throw new Error('password input not found with: ' + sel);
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, val);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""", [password, pwd_sel])

            page.wait_for_timeout(1000)

            # Click login button
            print("  Clicking login button...")
            login_btn = page.query_selector("button.logi") or page.query_selector("button[type='submit']")
            if not login_btn:
                raise Exception("Login button not found!")
            login_btn.click()

            print("  Waiting for login to complete...")
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                page.wait_for_timeout(5000)

            print(f"  Post-login URL = {page.url}")
            debug_dump(page, "post-login")
            print("[STEP 1] Login done.\n")

            # ===================== STEP 2: GO TO COMPANY PAGE (same tab) =====================
            print("[STEP 2] Navigating to company page...")
            page.goto(COMPANY_URL, wait_until="load", timeout=120000)
            safe_check(page.url)
            print(f"  URL = {page.url}")

            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                print("  Network idle timed out on company page.")

            page.wait_for_timeout(5000)

            print("  Waiting for company cards...")
            try:
                page.wait_for_selector(".v-card", state="visible", timeout=60000)
            except Exception:
                debug_dump(page, "company-page-no-cards")
                raise Exception(
                    "Company cards not found. Check debug/company-page-no-cards.png"
                )
            page.wait_for_timeout(3000)  # let all cards render
            print("[STEP 2] Company page loaded.\n")

            # ===================== STEP 3: READ CARDS + CLICK MORE =====================
            print("[STEP 3] Reading company cards...\n")

            cards = page.query_selector_all(".v-card")
            print(f"  Found {len(cards)} company cards.\n")

            for index, card in enumerate(cards, start=1):
                try:
                    title_el = card.query_selector(".v-card__title")
                    name = title_el.inner_text().strip() if title_el else "N/A"

                    subtitle_el = card.query_selector(".v-card__subtitle")
                    category = subtitle_el.inner_text().strip() if subtitle_el else "N/A"

                    body_text = card.inner_text()
                    lines = [l.strip() for l in body_text.splitlines() if l.strip()]

                    drive_type = "N/A"
                    deadline = "N/A"
                    for line in lines:
                        if line in ("Regular", "Dream", "Super Dream", "Open"):
                            drive_type = line
                        if any(m in line for m in [
                            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
                        ]) and any(ch.isdigit() for ch in line):
                            deadline = line

                    company_data = {
                        "name": name,
                        "category": category,
                        "drive_type": drive_type,
                        "deadline": deadline,
                        "details": "",
                    }

                    print(f"  [{index}] {name}")
                    print(f"       Category  : {category}")
                    print(f"       Drive Type: {drive_type}")
                    print(f"       Deadline  : {deadline}")

                    # --- Click MORE to get full details ---
                    try:
                        more_btn = None
                        for b in card.query_selector_all("button"):
                            try:
                                if "MORE" in b.inner_text().strip().upper():
                                    more_btn = b
                                    break
                            except Exception:
                                pass

                        if more_btn:
                            more_btn.click()
                            # Wait for any v-dialog to become visible
                            page.wait_for_selector(".v-dialog", state="visible", timeout=8000)
                            page.wait_for_timeout(1000)

                            # Grab visible dialog text
                            details_text = ""
                            for d in page.query_selector_all(".v-dialog"):
                                if d.is_visible():
                                    details_text = d.inner_text().strip()
                                    break

                            company_data["details"] = details_text
                            if details_text:
                                print(f"       Details   : ({len(details_text)} chars captured)")
                            else:
                                print("       Details   : (dialog empty)")

                            # Close dialog
                            page.keyboard.press("Escape")
                            page.wait_for_timeout(800)
                        else:
                            print("       (no More button)")

                    except Exception as modal_err:
                        print(f"       Modal error: {modal_err}")
                        try:
                            page.keyboard.press("Escape")
                        except Exception:
                            pass
                        page.wait_for_timeout(500)

                    companies.append(company_data)
                    print()

                except Exception as card_err:
                    print(f"  [{index}] Error reading card: {card_err}\n")

        except Exception as e:
            print(f"\n[ERROR] {e}")
            if page:
                debug_dump(page, "error-state")
            print("[ERROR] Cleaning up...")
        finally:
            if page and browser:
                logout_and_close(page, browser)
            elif browser:
                try:
                    browser.close()
                except Exception:
                    pass
                print("[CLEANUP] Browser force-closed.")

    return companies


if __name__ == "__main__":
    if "TPO_USERNAME" not in os.environ or "TPO_PASSWORD" not in os.environ:
        raise Exception(
            "Set TPO_USERNAME and TPO_PASSWORD in your .env file.\n"
            "Example .env:\n"
            "  TPO_USERNAME=your_username\n"
            "  TPO_PASSWORD=your_password"
        )

results = scrape_companies()

print("\n===== COMPANY LISTINGS =====\n")

if not results:
    print("No companies found or an error occurred.")
    send_to_telegram("❌ No companies found or error occurred.")
else:
    full_message = "🚀 *TPO COMPANY LISTINGS*\n\n"

    for i, c in enumerate(results, 1):

        # ----- PRINT TO TERMINAL -----
        print(f"{i}. {c['name']}")
        print(f"   Category  : {c['category']}")
        print(f"   Drive Type: {c['drive_type']}")
        print(f"   Deadline  : {c['deadline']}")

        # ----- BUILD TELEGRAM MESSAGE -----
        full_message += "━━━━━━━━━━━━━━━━━━━━━━\n"
        full_message += f"🏢 *{i}. {c['name']}*\n\n"
        full_message += f"📌 Category     : {c['category']}\n"
        full_message += f"🚘 Drive Type   : {c['drive_type']}\n"
        full_message += f"⏳ Deadline     : {c['deadline']}\n"

        if c.get("details"):
            print(f"   Details   :\n{c['details']}")
            full_message += f"\n📝 Details:\n{c['details']}\n"

        print("-" * 60)

        # 5–6 blank lines spacing for better Telegram readability
        full_message += "\n\n\n\n\n\n"

    print(f"\nTotal: {len(results)} companies")
    full_message += "━━━━━━━━━━━━━━━━━━━━━━\n"
    full_message += f"📊 *Total Companies:* {len(results)}"

    # ---- Telegram Limit Handling ----
    if len(full_message) > 4000:
        for i in range(0, len(full_message), 4000):
            send_to_telegram(full_message[i:i+4000])
    else:
        send_to_telegram(full_message)