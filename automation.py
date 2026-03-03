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
            browser = p.chromium.launch(headless=False, slow_mo=100)
            page = browser.new_page()

            # ===================== STEP 1: LOGIN =====================
            print("[STEP 1] Opening login page...")
            # Only wait for domcontentloaded — the SPA renders after JS loads
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            safe_check(page.url)
            print(f"  Page loaded. URL = {page.url}")

            # Wait for the text input to appear (Vuetify renders it dynamically)
            print("  Waiting for username input to render...")
            page.wait_for_selector("input[type='text']", state="visible", timeout=30000)
            print("  Username input found.")

            # Extra wait for Vue to finish hydrating
            page.wait_for_timeout(3000)

            username = os.environ["TPO_USERNAME"]
            password = os.environ["TPO_PASSWORD"]

            # Use native JS setter to fill Vuetify inputs (bypasses fill timeout)
            print("  Filling username...")
            page.evaluate("""(val) => {
                const el = document.querySelector("input[type='text']");
                if (!el) throw new Error('username input not found');
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, val);
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }""", username)

            print("  Filling password...")
            page.evaluate("""(val) => {
                const el = document.querySelector("input[type='password']");
                if (!el) throw new Error('password input not found');
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, val);
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }""", password)

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
            print("[STEP 1] Login done.\n")

            # ===================== STEP 2: GO TO COMPANY PAGE (same tab) =====================
            print("[STEP 2] Navigating to company page...")
            page.goto(COMPANY_URL, wait_until="domcontentloaded", timeout=60000)
            safe_check(page.url)
            print(f"  URL = {page.url}")

            print("  Waiting for company cards...")
            page.wait_for_selector(".v-card", state="visible", timeout=30000)
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