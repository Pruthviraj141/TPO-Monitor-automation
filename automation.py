"""
TPO Company Monitor — Pure API approach (no browser needed).
Works on local machines AND GitHub Actions without any Playwright/headless issues.
"""

import os
import sys
import json
import base64

import requests
from dotenv import load_dotenv

# ---------- AES encryption (matches the site's CryptoJS AES-ECB) ----------
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
except ImportError:
    from Cryptodome.Cipher import AES
    from Cryptodome.Util.Padding import pad

load_dotenv()

# ── Constants ────────────────────────────────────────────────────────────────
API_BASE = "https://tpoapi.vierp.in"
AES_KEY_B64 = "flGQWOmgQDCh0dgChB6l1k74fJgTnj+AGSTGotH5CFo="
AES_KEY = base64.b64decode(AES_KEY_B64)  # 32-byte AES-256 key


# ── Helpers ──────────────────────────────────────────────────────────────────
def encrypt_params(data) -> str:
    """AES-256-ECB encrypt (matches the site's CryptoJS implementation)."""
    if isinstance(data, str):
        plaintext = json.dumps(data).encode("utf-8")
    else:
        plaintext = json.dumps(data).encode("utf-8")
    cipher = AES.new(AES_KEY, AES.MODE_ECB)
    ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))
    return base64.b64encode(ciphertext).decode("utf-8")


def send_to_telegram(message: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[TELEGRAM] Bot token or chat ID not set — skipping.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    resp = requests.post(url, data=payload)
    if resp.status_code != 200:
        print(f"[TELEGRAM] Failed: {resp.text}")


# ── API calls ────────────────────────────────────────────────────────────────
def api_login(username: str, password: str) -> dict:
    """Login via the TPO API and return session headers."""
    print("[STEP 1] Logging in via API...")
    encrypted = encrypt_params({"uid": username, "pass": password})
    resp = requests.post(
        f"{API_BASE}/login/process",
        json={"params": encrypted},
        headers={
            "Content-Type": "application/json",
            "Origin": "https://tpo.vierp.in",
            "Referer": "https://tpo.vierp.in/",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("msg") != "200":
        raise Exception(f"Login failed: {data}")

    print(f"  Logged in as: {data.get('uid')}")

    # Build the auth headers used by all subsequent API calls
    auth_headers = {
        "eps-uid": data["enc_uid"],
        "eps-token": data["token"],
        "eps-tenant": encrypt_params(data["tenant"]),
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://tpo.vierp.in",
        "Referer": "https://tpo.vierp.in/",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    print("[STEP 1] Login done.\n")
    return auth_headers


def api_get_companies(headers: dict) -> list:
    """Fetch the list of companies available to apply."""
    print("[STEP 2] Fetching company list...")
    resp = requests.post(
        f"{API_BASE}/TPOCompanyScheduling/apply_company",
        headers={**headers, "router-path": "/apply_company"},
        data="",
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("msg") != "200":
        raise Exception(f"Failed to fetch companies: {data}")

    companies = data.get("company_list", [])
    print(f"  Found {len(companies)} companies.\n")
    return companies


def api_get_company_details(headers: dict, offering_id: int) -> dict:
    """Fetch detailed info for a single company offering."""
    resp = requests.post(
        f"{API_BASE}/TPOCompanyScheduling/CompanyofferingInfo",
        json={"offering": offering_id},
        headers={**headers, "router-path": "/company-info"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ── Main logic ───────────────────────────────────────────────────────────────
def scrape_companies() -> list:
    username = os.environ.get("TPO_USERNAME")
    password = os.environ.get("TPO_PASSWORD")
    if not username or not password:
        raise Exception(
            "Set TPO_USERNAME and TPO_PASSWORD in your .env file.\n"
            "Example .env:\n"
            "  TPO_USERNAME=your_username\n"
            "  TPO_PASSWORD=your_password"
        )

    # ── Login ──
    headers = api_login(username, password)

    # ── Get company list ──
    companies_raw = api_get_companies(headers)

    # ── Get details for each company ──
    print("[STEP 3] Fetching details for each company...\n")
    results = []
    for i, comp in enumerate(companies_raw, start=1):
        name = comp.get("Company_name") or comp.get("name", "N/A")
        offering_id = comp.get("id")
        placement_type = comp.get("placementtype", "N/A")
        company_type = comp.get("companytype", "N/A")
        deadline = f"{comp.get('regEnddate', 'N/A')} {comp.get('regEndtime', '')}"

        company_data = {
            "name": name,
            "category": placement_type,
            "drive_type": company_type,
            "deadline": deadline.strip(),
            "details": "",
            "package": "",
            "stipend": "",
            "eligible_branches": [],
            "selection_rounds": [],
        }

        print(f"  [{i}] {name}")
        print(f"       Category  : {placement_type}")
        print(f"       Drive Type: {company_type}")
        print(f"       Deadline  : {deadline}")

        # Fetch detailed info
        if offering_id:
            try:
                detail = api_get_company_details(headers, offering_id)

                # Package info
                min_pkg = detail.get("minpackage", 0)
                max_pkg = detail.get("maxpackage", 0)
                if min_pkg or max_pkg:
                    company_data["package"] = f"{min_pkg} - {max_pkg} LPA"
                    print(f"       Package   : {company_data['package']}")

                # Stipend info
                min_stip = detail.get("minstipend", 0)
                max_stip = detail.get("maxstipend", 0)
                if min_stip or max_stip:
                    company_data["stipend"] = f"Rs.{min_stip} - Rs.{max_stip}"
                    print(f"       Stipend   : {company_data['stipend']}")

                # Package description
                desc = detail.get("description", "")
                if desc:
                    company_data["details"] = desc
                    print(f"       Pkg Desc  : {desc}")

                # Selection rounds
                rounds = detail.get("selction_procedure", [])
                for r in rounds:
                    company_data["selection_rounds"].append(r.get("companyround", ""))
                if rounds:
                    print(f"       Rounds    : {', '.join(company_data['selection_rounds'])}")

                # Eligible branches
                programs = detail.get("programlist", [])
                for p in programs:
                    branch = f"{p.get('org', '')} - {p.get('program', '')}"
                    company_data["eligible_branches"].append(branch)
                if programs:
                    print(f"       Branches  : {len(programs)} programs eligible")

                # Additional details
                locations = detail.get("locations", [])
                if locations:
                    loc_str = ", ".join(str(l) for l in locations if l and str(l).strip())
                    if loc_str:
                        company_data["details"] += f"\nLocations: {loc_str}"

                backlog_info = []
                if detail.get("is_dead_backlog_allowed"):
                    backlog_info.append("Dead backlog: Allowed")
                else:
                    backlog_info.append("Dead backlog: Not Allowed")
                if detail.get("is_live_backlog_allowed"):
                    backlog_info.append("Live backlog: Allowed")
                else:
                    backlog_info.append("Live backlog: Not Allowed")
                company_data["details"] += "\n" + " | ".join(backlog_info)

                if detail.get("isplacedstudentallowed"):
                    company_data["details"] += "\nPlaced students: Allowed"
                else:
                    company_data["details"] += "\nPlaced students: Not Allowed"

            except Exception as e:
                print(f"       [WARN] Could not fetch details: {e}")

        results.append(company_data)
        print()

    return results


# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    results = scrape_companies()

    print("\n===== COMPANY LISTINGS =====\n")

    if not results:
        print("No companies found or an error occurred.")
        send_to_telegram("No companies found or error occurred.")
    else:
        full_message = "TPO COMPANY LISTINGS\n\n"

        for i, c in enumerate(results, 1):
            # ── Terminal output ──
            print(f"{i}. {c['name']}")
            print(f"   Category  : {c['category']}")
            print(f"   Drive Type: {c['drive_type']}")
            print(f"   Deadline  : {c['deadline']}")
            if c.get("package"):
                print(f"   Package   : {c['package']}")
            if c.get("stipend"):
                print(f"   Stipend   : {c['stipend']}")
            if c.get("selection_rounds"):
                print(f"   Rounds    : {', '.join(c['selection_rounds'])}")
            if c.get("details"):
                print(f"   Details   : {c['details']}")
            print("-" * 60)

            # ── Telegram message ──
            full_message += "----------------------\n"
            full_message += f"{i}. {c['name']}\n\n"
            full_message += f"Category     : {c['category']}\n"
            full_message += f"Drive Type   : {c['drive_type']}\n"
            full_message += f"Deadline     : {c['deadline']}\n"

            if c.get("package"):
                full_message += f"Package      : {c['package']}\n"
            if c.get("stipend"):
                full_message += f"Stipend      : {c['stipend']}\n"
            if c.get("selection_rounds"):
                full_message += f"Rounds       : {', '.join(c['selection_rounds'])}\n"
            if c.get("details"):
                full_message += f"\nDetails:\n{c['details']}\n"

            full_message += "\n\n\n"

        print(f"\nTotal: {len(results)} companies")
        full_message += "----------------------\n"
        full_message += f"Total Companies: {len(results)}"

        # ── Telegram limit handling ──
        if len(full_message) > 4000:
            for j in range(0, len(full_message), 4000):
                send_to_telegram(full_message[j : j + 4000])
        else:
            send_to_telegram(full_message)
