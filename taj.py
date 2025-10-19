#!/usr/bin/env python3
"""
cert_bot_qrocom_submit_mainmenu_with_sisbel.py

Merged TajCert Bot (main menu edition) with SISBEL (q.sisbel.com) verification added as
an additional certification-body (callback cb:sisbel).

- Keeps original token and behavior.
- Adds SISBEL API as a new certification body option.
- SISBEL flow asks for company name then certificate number and queries the SISBEL API.
- CAPTCHA logic and verification-preservation semantics remain unchanged.

Added:
- Request Certification submenu (buttons linking to Application_Form.php)
- FSSC flow: choose FSSC 22000 / 24000 -> Verify by COID or Verify by Company Name.
- find_coid_by_company_name() helper to resolve COID from FSSC public-register search HTML.
"""

import logging
import asyncio
import html
import re
import random
from typing import Optional, Dict, Any, Tuple

import requests
from bs4 import BeautifulSoup
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

# ---------------- CONFIG ----------------
# Keep your original bot token here
TOKEN = "8219317787:AAHuYrGVtY8U8gA5mvJVjJtOw487Z2Lhwsg"

REQUEST_TIMEOUT = 18.0
INFINITY_API_URL = "https://infinitycert.com/wp-json/v2/ssr_find_all"
INFINITY_HEADERS = {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "CertCheckBot/1.0"}
QRO_COM_VERIFY = "https://qrocert.com/verify-certification.aspx"
QRO_ORG_PAGE = "https://qrocert.org/verify.aspx"
QSI_BASE = "http://certificate.qsicert.ca/QSICERT?ID="
SISBEL_API_URL = "https://q.sisbel.com/api/belge-sorgula.php"  # SISBEL API added

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("cert_bot_qrocom_submit_mainmenu_with_sisbel")

# ---------------- UI TEXTS ----------------
MAIN_MENU_TEXT = (
    "🏠 *TajCert — Main Menu*\n\n"
    "Welcome to TajCert Bot, your trusted platform for professional certificate verification and validation. Please choose an option below to proceed with your request.\n\n"
    "For technical assistance or inquiries, contact the developer: @jamshidiyan"
)

# Main menu keyboard (buttons as requested)
MAIN_MENU_KB = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("🧐 Check Certificate", callback_data="main:check")],
        [InlineKeyboardButton("✍ Request Certification", callback_data="main:request")],
        [InlineKeyboardButton("📱 Contact", callback_data="main:contact")],
        [InlineKeyboardButton("🗺 Address", callback_data="main:address")],
        [InlineKeyboardButton("🌐 Website", url="https://taj-ra.com/en/")],
    ]
)

# Request submenu (all buttons link to the application form URL)
REQUEST_CERT_KB = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("🍽 FSSC Certification", url="https://taj-ra.com/Application_Form.php")],
        [InlineKeyboardButton("📋 ISO Certification", url="https://taj-ra.com/Application_Form.php")],
        [InlineKeyboardButton("☣ HACCP Certification", url="https://taj-ra.com/Application_Form.php")],
        [InlineKeyboardButton("🏭 GMP Certification", url="https://taj-ra.com/Application_Form.php")],
        [InlineKeyboardButton("🕌 HALAL Certification", url="https://taj-ra.com/Application_Form.php")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main:menu")],
    ]
)

# certificate type keyboard (same as before)
TYPE_KB = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("🧐 Verify FSSC Certificate 🍽", callback_data="type:fssc")],
        [InlineKeyboardButton("🧐 Verify ISO Certificate 📋", callback_data="type:iso")],
        [InlineKeyboardButton("🧐 Verify HACCP Certificate ☣", callback_data="type:haccp")],
        [InlineKeyboardButton("🧐 Verify GMP Certificate 🏭", callback_data="type:gmp")],
        [InlineKeyboardButton("🧐 Verify Halal Certificate 🕌", callback_data="type:halal")],
    ]
)

# FSSC standard selection keyboard
FSSC_STANDARD_KB = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("🧐 FSSC 22000 🌐", callback_data="fssc:22000")],
        [InlineKeyboardButton("🧐 FSSC 24000 🏭", callback_data="fssc:24000")],
        [InlineKeyboardButton("🔙 Back to Types", callback_data="main:check")],
    ]
)

# FSSC method keyboard builder
def make_fssc_method_kb(standard: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🆔 Verify by COID Number", callback_data=f"fssc_method:{standard}:coid")],
            [InlineKeyboardButton("🏭 Verify by Company Name", callback_data=f"fssc_method:{standard}:company")],
            [InlineKeyboardButton("🔙 Back to FSSC Standards", callback_data="type:fssc")],
        ]
    )

# certification-body keyboard builder function will include all CBs (qro_com, qro_org, qsi, infinity, fssc, sisbel, other)
def make_cb_keyboard_for_type(ctype: str) -> InlineKeyboardMarkup:
    kb = []
    kb.append([InlineKeyboardButton("QRO | IAF", callback_data="cb:qro_org")])
    kb.append([InlineKeyboardButton("QRO | UKAF", callback_data="cb:qro_com")])
    kb.append([InlineKeyboardButton("QSI Cert Canada", callback_data="cb:qsi")])
    kb.append([InlineKeyboardButton("Infinity ICI", callback_data="cb:infinity")])
    kb.append([InlineKeyboardButton("SiSBEL", callback_data="cb:sisbel")])  # SISBEL option
    kb.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main:menu")])
    return InlineKeyboardMarkup(kb)

# Re-run / back KB used after verification
AGAIN_KB = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("🔁 Next", callback_data="again")],
        [InlineKeyboardButton("🏠 Home", callback_data="main:menu")],
    ]
)

PROMPT_COID = (
    "Please provide the Certificate Organization Identification (COID) number associated with the certificate. This unique identifier is assigned by the certification body to each registered organization and is used to verify the authenticity, validity, and traceability of issued certificates. Entering the correct COID ensures that the system can accurately retrieve and validate the certification details from the authorized verification sources.\n\n"
)
PROMPT_CERT_NO = (
    "Please provide the certificate number exactly as it appears on the official certificate. This unique identifier is essential for accurately locating and verifying your record within the certification database. Ensure that the number is entered precisely, including any letters, digits, or symbols, to avoid discrepancies or retrieval errors during the verification process.\n\n"
)
PROMPT_ISSUE_DATE_TEXT = (
    "Please provide the certificate issue date using one of the accepted formats: YYYY-MM-DD or DD/MM/YYYY. This information helps the system accurately locate and verify the certificate record within the database. For example, you may enter the date as 2025-10-10 or 10/10/2025, ensuring consistency and precision in data validation.\n"
)

PROCESSING = "🔄 Processing your request — please wait while we securely fetch and verify your certificate data from the selected source. This may take a few moments depending on system response time."
NOT_FOUND = "❌ No matching certificate record found in the selected source for the provided details."
ERROR_MSG = "⚠️ An internal error occurred while processing your request. Please try again later or contact @jamshidiyan."
THANK_YOU_BRIEF = "✅ Your verification has been successfully completed. Thank you for using TajCert Bot — your trusted assistant for professional certificate validation. For technical support or inquiries, please contact @jamshidiyan"

# Office contact details (as requested)
OFFICE_PHONE = "+93796367367"
OFFICE_TELEGRAM = "t.me/jamshidiyan"
OFFICE_ADDRESS = "Ferdowsi Road, Between Ferdowsi 11 & 13, House #73, 4th Floor, Herat, Afghanistan"
OFFICE_EMAIL = "info@taj-ra.com"
REQUEST_CERT_LINK = "https://taj-ra.com/Application_Form.php"

# ---------------- simple in-memory cache (runtime) ----------------
_cache: Dict[str, Any] = {}

def cache_get(key: str) -> Optional[Any]:
    return _cache.get(key)

def cache_set(key: str, value: Any) -> None:
    _cache[key] = value

# ---------------- helpers ----------------
def validate_date_input(s: str) -> Optional[str]:
    s = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        y, m, d = s.split("-")
        try:
            return f"{int(d):02d}/{int(m):02d}/{int(y):04d}"
        except Exception:
            return None
    if re.match(r"^\d{2}/\d{2}/\d{4}$", s):
        return s
    return None

async def safe_send_text(chat_send, text: str, parse_mode=ParseMode.HTML, **kwargs):
    try:
        return await chat_send(text, parse_mode=parse_mode, **kwargs)
    except Exception:
        try:
            safe = html.unescape(re.sub(r"<[^>]+>", "", text))
            return await chat_send(safe, **kwargs)
        except Exception as e:
            logger.exception("Failed to send message: %s", e)
            return None

# ---------------- CAPTCHA helpers ----------------
def generate_captcha() -> Tuple[str, str]:
    """
    Generate a simple arithmetic captcha.
    Returns (question_text, expected_answer_as_string)
    Uses +, -, *, or integer division that yields integer result.
    """
    ops = ['+', '-', '*', '/']
    op = random.choice(ops)
    # choose numbers across a reasonable range
    if op == '+':
        a = random.randint(2, 50)
        b = random.randint(2, 50)
        q = f"{a} + {b} = ?"
        ans = str(a + b)
    elif op == '-':
        a = random.randint(5, 80)
        b = random.randint(1, min(40, a-1))
        q = f"{a} - {b} = ?"
        ans = str(a - b)
    elif op == '*':
        a = random.randint(2, 12)
        b = random.randint(2, 12)
        q = f"{a} × {b} = ?"
        ans = str(a * b)
    else:  # integer division
        b = random.randint(2, 12)
        c = random.randint(2, 12)
        a = b * c
        q = f"{a} ÷ {b} = ?"
        ans = str(c)
    return q, ans

def start_captcha_for_user(context_user_data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Initialize captcha fields in user_data and return the question text.
    """
    q, ans = generate_captcha()
    # keep previous behavior of /start clearing state for a fresh captcha
    context_user_data.clear()
    context_user_data["awaiting_captcha"] = True
    context_user_data["captcha_question"] = q
    context_user_data["captcha_answer"] = ans
    context_user_data["captcha_attempts"] = 3
    context_user_data["verified"] = False
    return q, context_user_data

def clear_flow_keep_verified(user_data: Dict[str, Any]) -> None:
    """Clear temporary flow keys but preserve the 'verified' flag if present.
    Use this instead of user_data.clear() when you want to reset inputs but keep verification.
    """
    verified = user_data.get("verified")
    user_data.clear()
    if verified:
        user_data["verified"] = True

# ---------------- (All verification functions unchanged) ----------------
# For brevity in this file these functions remain the same as in the original bot:
# - fetch_fssc_by_coid, format_fssc_result
# - infinity_post_cert, format_infty
# - extract_hidden_inputs, find_input_name_by_suffix, find_submit_name
# - parse_label_map, parse_certificate_from_html
# - submit_qro_com, submit_qro_org, format_qro
# - fetch_qsi_simple, format_qsi_simple
#
# Implementations follow (copied from original bot) with a single addition:
# a helper to find COID by company name on FSSC public-register pages.

# ---------- FSSC ----------
def fetch_fssc_by_coid(coid: str) -> Any:
    key = f"fssc:{coid}"
    cached = cache_get(key)
    if cached:
        return cached
    url = f"https://www.fssc.com/public-register/{coid}/"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CertCheckBot/1.0)"}
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        logger.exception("FSSC fetch error: %s", e)
        return {"error": str(e)}
    if r.status_code == 404:
        return "not_found"
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    soup = BeautifulSoup(r.text, "html.parser")
    article = soup.select_one("article#certification-body-detail, article.page-content--certification-body")
    if not article:
        if "No results found" in r.text or "No certificates match" in r.text:
            return "not_found"
        return "not_found"
    data = {}
    org = article.select_one("h1.page-header__title, header.page-header h1")
    data["organization"] = org.get_text(" ", strip=True) if org else None

    def find_h6_value(title):
        h6 = article.find("h6", string=lambda s: s and title.lower() in s.lower())
        if not h6:
            return None
        nxt = h6.find_next_sibling()
        if not nxt or not nxt.get_text(strip=True):
            nxt = h6.find_next()
        if not nxt:
            return None
        return nxt.get_text(" ", strip=True)

    data["scheme"] = find_h6_value("scheme")
    data["valid_until"] = find_h6_value("certificate valid until") or find_h6_value("certificate valid")
    data["coid"] = find_h6_value("coid") or coid
    address_h6 = article.find("h6", string=lambda s: s and "address" in s.lower())
    data["address"] = (
        address_h6.find_next("address").get_text(" ", strip=True)
        if address_h6 and address_h6.find_next("address")
        else find_h6_value("address")
    )
    ptypes_h6 = article.find("h6", string=lambda s: s and "product types" in s.lower())
    if ptypes_h6:
        ul = ptypes_h6.find_next_sibling("ul")
        data["product_types"] = [li.get_text(" ", strip=True) for li in ul.find_all("li")] if ul else []
    else:
        data["product_types"] = []
    scope_h6 = article.find("h6", string=lambda s: s and "scope statement" in s.lower())
    data["scope_statement"] = (
        scope_h6.find_next_sibling("p").get_text(" ", strip=True) if scope_h6 and scope_h6.find_next_sibling("p") else None
    )
    cat_block = article.select_one(".Scopes-block")
    cats = []
    if cat_block:
        for li in cat_block.select("li"):
            scope_span = li.find("span", class_="scope")
            title_span = li.find("span", class_="title")
            cats.append({"code": scope_span.get_text(strip=True) if scope_span else None, "title": title_span.get_text(strip=True) if title_span else None})
    data["categories"] = cats
    data["fssc_url"] = url
    cache_set(key, data)
    # store mapping by organization name if available
    try:
        name_key = data.get("organization", "").strip().lower()
        if name_key:
            idx = cache_get("fssc_name_index") or {}
            idx[name_key] = data.get("coid") or coid
            cache_set("fssc_name_index", idx)
    except Exception:
        pass
    return data

def format_fssc_result(data: dict) -> Optional[str]:
    esc = html.escape
    if not data or data == "not_found":
        return None
    lines = [f"<b>{esc(data.get('organization') or '-')}</b>", ""]
    lines.append(f"📜 <b>Scheme:</b> {esc(data.get('scheme') or '-')}") 
    lines.append(f"✅ <b>Valid until:</b> {esc(data.get('valid_until') or '-')}") 
    lines.append(f"🆔 <b>COID:</b> {esc(data.get('coid') or '-')}") 
    lines.append(
    f"📍 <b>Address:</b> {html.escape(' '.join(data.get('address', '-').split()))}")
    ptypes = ",".join(data.get("product_types") or []) or "-" 
    lines.append(f"🛒 <b>Product types:</b> {esc(ptypes)}") 
    lines.append(f"📝 <b>Scope:</b> {esc(data.get('scope_statement') or '-')}") 
    if data.get("categories"): 
        lines.append("") 
        lines.append("<b>📂 FSSC Categories:</b>") 
        for c in data.get("categories", []): 
            lines.append(f" - {esc(c.get('code') or '-')} — {esc(c.get('title') or '-')}") 
    lines.append("") 
    if data.get("fssc_url"): 
        lines.append(f"🔗 View on FSSC: <a href=\"{esc(data['fssc_url'])}\">{esc(data['fssc_url'])}</a>") 
    return "\n".join(lines)

# ---------- Infinity ----------
def infinity_post_cert(cert_no: str) -> Optional[Dict]:
    key = f"infty:{cert_no}"
    cached = cache_get(key)
    if cached:
        return cached
    try:
        r = requests.post(INFINITY_API_URL, data={"postID": cert_no}, headers=INFINITY_HEADERS, timeout=20)
        r.raise_for_status()
        j = r.json()
        if isinstance(j, dict) and j.get("success"):
            for k, v in j.items():
                if k.isdigit() and isinstance(v, dict):
                    cache_set(key, v)
                    return v
            if isinstance(j.get("data"), dict):
                cache_set(key, j["data"])
                return j["data"]
        return None
    except Exception as e:
        logger.exception("Infinity API error: %s", e)
        return None

def format_infty(obj: dict) -> Optional[str]:
    if not obj:
        return None
    esc = html.escape
    lines = ["<b>🟩 Infinity Certificate Information</b>\n"]
    fields = [
        ("rid", "Certificate No"),
        ("stdname", "Issue Date"),
        ("fathersname", "Expiry Date"),
        ("subject", "Standard"),
        ("dob", "Accreditation Body"),
        ("gender", "Organization"),
        ("address", "Address"),
        ("mnam", "Scope"),
        ("c1", "Status"),
    ]
    for k, label in fields:
        val = obj.get(k)
        lines.append(f"<b>{esc(label)}:</b> {esc(str(val)) if val not in (None, '') else '-'}")
    return "\n".join(lines)

# ---------- QRO helpers ----------
def extract_hidden_inputs(soup: BeautifulSoup) -> Dict[str, str]:
    return {inp.get("name"): inp.get("value", "") for inp in soup.select("input[type=hidden]") if inp.get("name")}

def find_input_name_by_suffix(soup: BeautifulSoup, suffixes: tuple) -> Optional[str]:
    for inp in soup.find_all("input"):
        idv = inp.get("id") or ""
        namev = inp.get("name") or ""
        for suf in suffixes:
            if idv.endswith(suf) or namev.endswith(suf):
                return namev or idv
    return None

def find_submit_name(soup: BeautifulSoup) -> Optional[str]:
    for inp in soup.find_all(["input", "button"]):
        t = (inp.get("type") or "").lower()
        name = inp.get("name") or inp.get("id") or ""
        if name.lower().endswith("button1") or t == "submit":
            return name
    return None

def parse_label_map(soup: BeautifulSoup) -> Dict[str, str]:
    labels = {}
    for tag in soup.find_all(attrs={"id": True}):
        m = re.search(r"(Label\d+)$", tag["id"])
        if m:
            labels[m.group(1)] = tag.get_text(" ", strip=True)
    return labels

def parse_certificate_from_html(html_text: str) -> Dict[str, Optional[str]]:
    soup = BeautifulSoup(html_text, "lxml")
    labels = parse_label_map(soup)
    def g(n): return labels.get(f"Label{n}")
    parsed = {
        "certificate_no": g(8) or g(0),
        "company": g(1),
        "address": g(2),
        "issue_date": g(3),
        "expiry_date": g(7),
        "status": g(4),
        "standard": g(5),
    }
    for k, v in parsed.items():
        if v is not None:
            parsed[k] = v.strip() or None
    return parsed

def submit_qro_com(cert_no: str, issue_date: str) -> Tuple[bool, Dict[str, Optional[str]]]:
    try:
        sess = requests.Session()
        sess.headers.update({"User-Agent": "CertCheckBot/1.0"})
        r = sess.get(QRO_COM_VERIFY, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        payload = extract_hidden_inputs(soup)
        cert_name = find_input_name_by_suffix(soup, ("TextBox1", "TextBox1$")) or "TextBox1"
        date_name = find_input_name_by_suffix(soup, ("TextBox2", "TextBox2$")) or "TextBox2"
        btn_name = find_submit_name(soup) or "Button1"
        payload[cert_name] = cert_no
        payload[date_name] = issue_date
        payload[btn_name] = "Submit"
        post = sess.post(QRO_COM_VERIFY, data=payload, headers={"Referer": QRO_COM_VERIFY, "User-Agent": "CertCheckBot/1.0"}, timeout=15)
        post.raise_for_status()
        parsed = parse_certificate_from_html(post.text)
        return True, parsed
    except Exception as e:
        logger.exception("submit_qro_com error: %s", e)
        return False, {"error": str(e)}

def submit_qro_org(cert_no: str, issue_date: str) -> Tuple[bool, Dict[str, Optional[str]]]:
    try:
        sess = requests.Session()
        sess.headers.update({"User-Agent": "CertCheckBot/1.0"})
        r = sess.get(QRO_ORG_PAGE, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        payload = extract_hidden_inputs(soup)
        cert_name = find_input_name_by_suffix(soup, ("TextBox1", "TextBox1$")) or "TextBox1"
        date_name = find_input_name_by_suffix(soup, ("TextBox2", "TextBox2$")) or "TextBox2"
        btn_name = find_submit_name(soup) or "Button1"
        payload[cert_name] = cert_no
        payload[date_name] = issue_date
        payload[btn_name] = "Submit"
        post = sess.post(QRO_ORG_PAGE, data=payload, headers={"Referer": QRO_ORG_PAGE, "User-Agent": "CertCheckBot/1.0"}, timeout=15)
        post.raise_for_status()
        parsed = parse_certificate_from_html(post.text)
        return True, parsed
    except Exception as e:
        logger.exception("submit_qro_org error: %s", e)
        return False, {"error": str(e)}

def format_qro(parsed: dict) -> Optional[str]:
    if not parsed:
        return None
    esc = html.escape
    lines = ["<b>🟩 QRO Certification Company</b>"]
    lines.append(f"<b>🏢 Company:</b> {esc(parsed.get('company') or '-')}") 
    lines.append(f"<b>🔢 Certificate No:</b> {esc(parsed.get('certificate_no') or '-')}") 
    lines.append(f"<b>📅 Issue Date:</b> {esc(parsed.get('issue_date') or '-')}") 
    lines.append(f"<b>⏳ Expiry Date:</b> {esc(parsed.get('expiry_date') or '-')}") 
    lines.append(f"<b>✅ Status:</b> {esc(parsed.get('status') or '-')}") 
    lines.append(f"<b>🔖 Standard:</b> {esc(parsed.get('standard') or '-')}") 
    lines.append(f"<b>📍 Address:</b> {esc(parsed.get('address') or '-')}") 
    return "\n".join(lines)

# ---------- QSI scraping ----------
def _text_after_colon(s: str) -> Optional[str]:
    if not s:
        return None
    if ':' in s:
        _, after = s.split(':', 1)
        return after.strip() or None
    return s.strip() or None

def _extract_neighbor_value(node):
    if node is None:
        return None
    try:
        tr = node.find_parent("tr")
    except Exception:
        tr = None
    if tr:
        tds = tr.find_all(["td", "th"])
        if len(tds) >= 2:
            for i, td in enumerate(tds):
                if td == node or td.find(node.name if hasattr(node, "name") else None) is not None:
                    if i + 1 < len(tds):
                        val = tds[i + 1].get_text(" ", strip=True)
                        if val:
                            return val
        next_row = tr.find_next_sibling("tr")
        if next_row:
            cells = next_row.find_all(["td", "th"])
            if cells:
                v = cells[0].get_text(" ", strip=True)
                if v:
                    return v
    if getattr(node, "name", "") == "dt":
        dd = node.find_next_sibling("dd")
        if dd:
            return dd.get_text(" ", strip=True)
    ns = node.find_next_sibling()
    if ns:
        v = ns.get_text(" ", strip=True)
        if v:
            return v
    pt = node.get_text(" ", strip=True)
    after = _text_after_colon(pt)
    if after:
        return after
    return None

def fetch_qsi_simple(cert_no: str) -> Dict[str, Optional[str]]:
    key = f"qsi:{cert_no}"
    cached = cache_get(key)
    if cached:
        return cached

    url = QSI_BASE + str(cert_no)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141 Safari/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        html_text = r.text
    except Exception as e:
        logger.exception("QSI GET error: %s", e)
        return {"error": str(e)}

    soup = BeautifulSoup(html_text, "lxml")

    logo_url = None
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        alt = (img.get("alt") or "").lower()
        if not src:
            continue
        if "logo" in src.lower() or "logo" in alt:
            logo_url = src
            break
    if not logo_url:
        for img in soup.find_all("img"):
            src = (img.get("src") or "").strip()
            if src and any(src.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".svg")):
                logo_url = src
                break

    pairs = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) >= 2:
            left = cells[0].get_text(" ", strip=True)
            right = cells[-1].get_text(" ", strip=True)
            if left:
                pairs[left] = right
    for dl in soup.find_all("dl"):
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                pairs[dt.get_text(" ", strip=True)] = dd.get_text(" ", strip=True)
    for tag in soup.find_all(["p", "div", "li", "span"]):
        txt = tag.get_text(" ", strip=True)
        if ':' in txt:
            left, right = txt.split(':', 1)
            left = left.strip()
            right = right.strip()
            if left and right:
                pairs[left] = right

    field_keywords = {
        "name": ["name", "company", "organisation", "organization", "entity"],
        "address": ["address", "addr", "location"],
        "scope": ["scope", "scope of certification", "scope of certificate"],
        "standard": ["standard", "standards"],
        "certificate_id": ["certificate id", "certificate no", "certificate number", "id", "certificate"],
        "nace": ["nace", "nace code", "category"],
        "certification_date": ["certification date", "certified on", "issue date", "date of certification", "certification"],
        "first_followup": ["1st follow", "1st follow-up", "first follow-up", "first surveillance", "surveillance 1", "follow up 1"],
        "second_followup": ["2nd follow", "2nd follow-up", "second follow-up", "second surveillance", "surveillance 2", "follow up 2"],
        "expiry_date": ["expiry", "expiry date", "valid until", "expiry on"],
        "status": ["status", "certificate status", "current status"],
        "accreditation": ["accreditation", "accredited by", "accrediting body", "accreditation body"],
    }

    def search_pairs(keywords):
        for k, v in pairs.items():
            kl = k.lower()
            for kw in keywords:
                if kw in kl:
                    return v
        return None

    def scan_nodes(keywords):
        for kw in keywords:
            regex = re.compile(r"\b" + re.escape(kw) + r"\b", re.I)
            for node in soup.find_all(string=regex):
                parent = node.parent
                val = _extract_neighbor_value(parent)
                if val:
                    return val
                txt = node.strip()
                after = _text_after_colon(txt)
                if after:
                    return after
        return None

    parsed = {"logo": logo_url}
    for fld, kws in field_keywords.items():
        val = search_pairs(kws)
        if not val:
            val = scan_nodes(kws)
        if not val:
            for kw in kws:
                m = re.search(rf"{re.escape(kw)}\s*[:\-]\s*([A-Za-z0-9\-/\s,\.]+)", html_text, re.I)
                if m:
                    raw = m.group(1).strip()
                    if 0 < len(raw) < 300:
                        val = raw
                        break
        parsed[fld] = val.strip() if isinstance(val, str) and val.strip() else None

    if not parsed.get("certificate_id"):
        parsed["certificate_id"] = str(cert_no)

    cache_set(key, parsed)
    return parsed

def format_qsi_simple(parsed: dict) -> Optional[str]:
    if not parsed:
        return None
    if isinstance(parsed, dict) and parsed.get("error"):
        return None
    esc = html.escape
    lines = ["<b>🟩 QSI Certificate</b>"]
    if parsed.get("logo"):
        logo = parsed["logo"]
        if logo.startswith("//"):
            logo = "http:" + logo
        elif logo.startswith("/"):
            logo = "http://certificate.qsicert.ca" + logo
        lines.append(f"<b>🏳️ Company Logo:</b> <a href=\"{esc(logo)}\">View Logo</a>")
    lines.append(f"<b>🏢 Name:</b> {esc(parsed.get('name') or '-')}")
    lines.append(f"<b>📍 Address:</b> {esc(parsed.get('address') or '-')}")
    lines.append(f"<b>🔖 Standard:</b> {esc(parsed.get('standard') or '-')}")
    lines.append(f"<b>📝 Scope:</b> {esc(parsed.get('scope') or '-')}")
    lines.append(f"<b>🔢 Certificate ID:</b> {esc(parsed.get('certificate_id') or '-')}")
    lines.append(f"<b>📂 NACE / Category:</b> {esc(parsed.get('nace') or '-')}")
    lines.append(f"<b>📅 Certification Date:</b> {esc(parsed.get('certification_date') or '-')}")
    lines.append(f"<b>⏳ Expiry Date:</b> {esc(parsed.get('expiry_date') or '-')}")
    lines.append(f"<b>✅ Status:</b> {esc(parsed.get('status') or '-')}")
    return "\n".join(lines)

# ---------------- New helper: find COID by company name on FSSC ----------------
def find_coid_by_company_name(company_name: str) -> Optional[str]:
    """
    Resolve COID by searching the FSSC public-register search endpoint:
      https://www.fssc.com/public-register/?search=<encoded>
    Strategy:
      1) Look for text like "COID: AFG-1-7798-696622" in raw html (case-insensitive).
      2) Parse <span class="co-id"> and extract COID text.
      3) Fallback: find hrefs like /public-register/<coid>
    Caches found mapping under 'fssc_name_index'.
    """
    if not company_name:
        return None
    name_key = re.sub(r"\s+", " ", company_name.strip().lower())
    # check cache
    idx = cache_get("fssc_name_index") or {}
    if name_key in idx:
        return idx[name_key]
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; CertCheckBot/1.0)",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        q_enc = requests.utils.quote(company_name)
        url = f"https://www.fssc.com/public-register/?search={q_enc}"
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        html_text = r.text or ""
    except Exception as e:
        logger.exception("find_coid_by_company_name: request error: %s", e)
        return None
    # 1) raw COID text
    m = re.search(r"COID\s*[:\-]?\s*([A-Za-z0-9\-]+)", html_text, re.I)
    if m:
        coid = m.group(1).strip()
        idx[name_key] = coid
        cache_set("fssc_name_index", idx)
        logger.info("find_coid_by_company_name: found COID (raw text) %s for %s", coid, name_key)
        return coid
    # 2) span.co-id
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        span = soup.select_one("span.co-id")
        if span:
            txt = span.get_text(" ", strip=True)
            m2 = re.search(r"COID\s*[:\-]?\s*([A-Za-z0-9\-]+)", txt, re.I)
            if m2:
                coid = m2.group(1).strip()
                idx[name_key] = coid
                cache_set("fssc_name_index", idx)
                logger.info("find_coid_by_company_name: found COID (span) %s for %s", coid, name_key)
                return coid
    except Exception:
        pass
    # 3) fallback: href /public-register/<coid>
    m3 = re.search(r"/public-register/([A-Za-z0-9\-]+)/?", html_text, re.I)
    if m3:
        coid = m3.group(1).strip()
        idx[name_key] = coid
        cache_set("fssc_name_index", idx)
        logger.info("find_coid_by_company_name: found COID (href) %s for %s", coid, name_key)
        return coid
    logger.info("find_coid_by_company_name: no COID found for %s", name_key)
    return None

# ---------------- Dispatcher / verification core ----------------
async def verify_for_cb(cb: str, cert_no: str, issue_date_qro: Optional[str]) -> Tuple[str, bool, Dict[str, str]]:
    meta = {"cb": "Unknown", "ab": "Not provided"}

    if cb == "infinity":
        inf = await asyncio.to_thread(infinity_post_cert, cert_no)
        meta["cb"] = "Infinity Cert International"
        if inf:
            ab = inf.get("dob") if isinstance(inf, dict) else None
            meta["ab"] = ab or "Not provided"
            return (format_infty(inf) or "Found (Infinity) but unable to format.", True, meta)
        return (NOT_FOUND, False, meta)

    if cb == "qsi":
        parsed = await asyncio.to_thread(fetch_qsi_simple, cert_no)
        meta["cb"] = "QSI (qsicert.ca)"
        if isinstance(parsed, dict) and parsed.get("error"):
            return (ERROR_MSG + f"\n\n{parsed.get('error')}", False, meta)
        if parsed and any(parsed.get(k) for k in ("name", "certificate_id", "standard")):
            ab = parsed.get("accreditation") or parsed.get("accreditation_body") or "Not provided"
            meta["ab"] = ab
            return (format_qsi_simple(parsed) or "Found (QSI) but unable to format.", True, meta)
        return (NOT_FOUND, False, meta)

    if cb == "qro_com":
        meta["cb"] = "QRO Certification (qrocert.com)"
        if not issue_date_qro:
            return ("QRO (qrocert.com) requires an issue date. Please provide it in `YYYY-MM-DD` or `DD/MM/YYYY` format.", False, meta)
        ok, parsed = await asyncio.get_event_loop().run_in_executor(None, submit_qro_com, cert_no, issue_date_qro)
        if ok and any(parsed.get(k) for k in ("company", "status", "standard", "issue_date")):
            ab = parsed.get("accreditation") or parsed.get("accreditation_body") or "Not provided"
            meta["ab"] = ab
            return ("<b>Source: qrocert.com</b>\n\n" + (format_qro(parsed) or ""), True, meta)
        if not ok and parsed and parsed.get("error"):
            return (ERROR_MSG + "\n\n" + parsed.get("error"), False, meta)
        return (NOT_FOUND, False, meta)

    if cb == "qro_org":
        meta["cb"] = "QRO Certification (qrocert.org)"
        if not issue_date_qro:
            return ("QRO (qrocert.org) requires an issue date. Please provide it in `YYYY-MM-DD` or `DD/MM/YYYY` format.", False, meta)
        ok, parsed = await asyncio.get_event_loop().run_in_executor(None, submit_qro_org, cert_no, issue_date_qro)
        if ok and any(parsed.get(k) for k in ("company", "status", "standard", "issue_date")):
            ab = parsed.get("accreditation") or parsed.get("accreditation_body") or "Not provided"
            meta["ab"] = ab
            return ("<b>Source: qrocert.org</b>\n\n" + (format_qro(parsed) or ""), True, meta)
        if not ok and parsed and parsed.get("error"):
            return (ERROR_MSG + "\n\n" + parsed.get("error"), False, meta)
        return (NOT_FOUND, False, meta)

    if cb == "fssc":
        meta["cb"] = "FSSC Public Register"
        res = await asyncio.to_thread(fetch_fssc_by_coid, cert_no)
        if isinstance(res, dict) and res.get("error"):
            return (ERROR_MSG + "\n\n" + html.escape(res.get('error')), False, meta)
        if res == "not_found":
            return (NOT_FOUND, False, meta)
        meta["ab"] = "FSSC / Not provided"
        return (format_fssc_result(res) or NOT_FOUND, True, meta)

    # fallback chain for 'other'
    meta["cb"] = "Other / Fallback chain"
    inf = await asyncio.to_thread(infinity_post_cert, cert_no)
    if inf:
        meta["cb"] = "Infinity Cert International"
        meta["ab"] = inf.get("dob") or "Not provided"
        return (format_infty(inf) or "Found (Infinity) but unable to format.", True, meta)

    parsed_qsi = await asyncio.to_thread(fetch_qsi_simple, cert_no)
    if isinstance(parsed_qsi, dict) and parsed_qsi.get("error"):
        logger.info("QSI fallback error: %s", parsed_qsi.get("error"))
    else:
        if parsed_qsi and any(parsed_qsi.get(k) for k in ("name", "certificate_id", "standard")):
            meta["cb"] = "QSI (qsicert.ca)"
            meta["ab"] = parsed_qsi.get("accreditation") or "Not provided"
            return (format_qsi_simple(parsed_qsi) or "Found (QSI) but unable to format.", True, meta)

    if issue_date_qro:
        ok, p = await asyncio.get_event_loop().run_in_executor(None, submit_qro_com, cert_no, issue_date_qro)
        if ok and any(p.get(k) for k in ("company", "status", "standard", "issue_date")):
            meta["cb"] = "QRO Certification (qrocert.com)"
            meta["ab"] = p.get("accreditation") or "Not provided"
            return ("<b>Source: qrocert.com</b>\n\n" + (format_qro(p) or ""), True, meta)
        ok2, p2 = await asyncio.get_event_loop().run_in_executor(None, submit_qro_org, cert_no, issue_date_qro)
        if ok2 and any(p2.get(k) for k in ("company", "status", "standard", "issue_date")):
            meta["cb"] = "QRO Certification (qrocert.org)"
            meta["ab"] = p2.get("accreditation") or "Not provided"
            return ("<b>Source: qrocert.org</b>\n\n" + (format_qro(p2) or ""), True, meta)

    fssc_res = await asyncio.to_thread(fetch_fssc_by_coid, cert_no)
    if isinstance(fssc_res, dict) and fssc_res.get("error"):
        return (ERROR_MSG + "\n\n" + html.escape(fssc_res.get('error')), False, meta)
    if fssc_res != "not_found":
        meta["cb"] = "FSSC Public Register"
        meta["ab"] = "FSSC / Not provided"
        return (format_fssc_result(fssc_res) or NOT_FOUND, True, meta)

    return (NOT_FOUND, False, meta)

# ---------------- Telegram handlers (Main Menu + flows) ----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start: initiate captcha if user not verified, otherwise show main menu.
    """
    # If user is already verified (from previous /start), just show main menu.
    if context.user_data.get("verified"):
        try:
            await update.message.reply_text("✅ You are already verified. Welcome!", parse_mode=ParseMode.MARKDOWN)
            await update.message.reply_text(MAIN_MENU_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU_KB)
            return
        except Exception:
            await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=MAIN_MENU_KB)
            return

    # Not verified: start captcha
    q_text, _ = start_captcha_for_user(context.user_data)
    try:
        # Present captcha question and instruct the user to reply with the numeric answer.
        await update.message.reply_text(
            "👋 Welcome to TajCert Bot!\n\nBefore you continue, please answer this simple verification question to confirm you're human:\n\n"
            f"*{q_text}*\n\n"
            "Please reply with the numeric answer (example: 42).\nYou have 3 attempts.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="main:menu")]]),
        )
    except Exception:
        # fallback text-only if parsing fails
        await update.message.reply_text(
            f"Please answer to continue: {q_text}\nReply with the numeric answer."
        )

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data  # e.g., "main:check" or "main:contact"
    _, action = data.split(":", 1)

    # If not verified yet, show (or re-show) captcha prompt instead of proceeding.
    if not context.user_data.get("verified"):
        # ensure captcha is initialized
        if not context.user_data.get("awaiting_captcha"):
            q_text, _ = start_captcha_for_user(context.user_data)
        else:
            q_text = context.user_data.get("captcha_question", "Please answer the verification question.")

        await q.edit_message_text(
            "🛡️ Verification required\n\n"
            "Please solve this simple question before using the bot:\n\n"
            f"*{q_text}*\n\n"
            "Reply with the numeric answer. You have 3 attempts.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="main:menu")]]),
        )
        return

    # --- Verified user: proceed with original actions ---

    if action == "check":
        # Go to the certificate type selection
        await q.edit_message_text("Please select the type of certificate you would like to verify:", parse_mode=ParseMode.MARKDOWN, reply_markup=TYPE_KB)
        return

    if action == "request":
        # Show request certification submenu (links)
        await q.edit_message_text("Choose the certification form to open:", parse_mode=ParseMode.MARKDOWN, reply_markup=REQUEST_CERT_KB)
        return

    if action == "menu":
        # back to main menu: clear flow but keep verified flag
        clear_flow_keep_verified(context.user_data)
        await q.edit_message_text(
            MAIN_MENU_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU_KB
        )
        return

    if action == "contact":
        # Show contact summary and a single button that opens contact options (telegram/whatsapp/call)
        text = (
            "📞 *Contact TajCert* \n\n"
            f"Phone: `{OFFICE_PHONE}`\n"
            f"Telegram: @{OFFICE_TELEGRAM.split('/')[-1]}\n\n"
            "Press *Contact Options* to choose Telegram or WhatsApp."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("☎️ Contact Options", callback_data="main:contact_opts")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="main:menu")],
            ]
        )
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    if action == "contact_opts":
        # Present Telegram / WhatsApp buttons as a secondary inline menu
        text = "Choose how you want to contact TajCert:"
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("💬 Telegram", url="https://t.me/jamshidiyan")],
                [InlineKeyboardButton("🟢 WhatsApp", url="https://wa.me/message/Q7M2T7A4YDAEA1")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="main:menu")],
            ]
        )
        await q.edit_message_text(text, reply_markup=kb)
        return

    if action == "address":
        text = f"📍 *Office Address*\n\n{OFFICE_ADDRESS}"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Main Menu", callback_data="main:menu")]])
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    if action == "email":
        # Open user's mail application using mailto: and show the email address
        text = f"֍ *Website*\n\n{OFFICE_EMAIL}\n\nPress the button below to open your mail app and compose an email."
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Visit Website", url=f"https://taj-ra.com/en/")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="main:menu")],
            ]
        )
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

# re-use existing type selection callback handler (adapted to work with main menu)
async def type_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # require verification for callbacks
    if not context.user_data.get("verified"):
        # ensure captcha is initialized
        if not context.user_data.get("awaiting_captcha"):
            q_text, _ = start_captcha_for_user(context.user_data)
        else:
            q_text = context.user_data.get("captcha_question", "Please answer the verification question.")
        await q.edit_message_text(
            "🛡️ Verification required\n\n"
            "Please solve this simple question before using the bot:\n\n"
            f"*{q_text}*\n\n"
            "Reply with the numeric answer. You have 3 attempts.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="main:menu")]]),
        )
        return

    data = q.data  # e.g., "type:iso"
    _, ctype = data.split(":", 1)
    # clear flow keys but keep verification status
    clear_flow_keep_verified(context.user_data)
    context.user_data["cert_type"] = ctype

    # FSSC now shows standard selection keyboard
    if ctype == "fssc":
        await q.edit_message_text("Choose FSSC standard:", parse_mode=ParseMode.MARKDOWN, reply_markup=FSSC_STANDARD_KB)
        return

    kb = make_cb_keyboard_for_type(ctype)
    await q.edit_message_text(f"You selected *{ctype.upper()}*.\n\nPlease select the Certification Body to proceed:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

# ---------- FSSC callback handlers ----------
async def fssc_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for callbacks like 'fssc:22000' or 'fssc:24000'
    Presents method keyboard (COID vs Company Name).
    """
    q = update.callback_query
    await q.answer()
    if not context.user_data.get("verified"):
        if not context.user_data.get("awaiting_captcha"):
            q_text, _ = start_captcha_for_user(context.user_data)
        else:
            q_text = context.user_data.get("captcha_question", "Please answer the verification question.")
        await q.edit_message_text(
            "🛡️ Verification required\n\n"
            "Please solve this simple question before using the bot:\n\n"
            f"*{q_text}*\n\n"
            "Reply with the numeric answer. You have 3 attempts.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="main:menu")]]),
        )
        return

    _, standard = q.data.split(":", 1)
    context.user_data["fssc_standard"] = standard
    # show method selection
    await q.edit_message_text(f"You selected *FSSC {standard}*.\n\nChoose verification method:", parse_mode=ParseMode.MARKDOWN, reply_markup=make_fssc_method_kb(standard))

async def fssc_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for 'fssc_method:<standard>:<method>' where method is 'coid' or 'company'
    """
    q = update.callback_query
    await q.answer()
    if not context.user_data.get("verified"):
        if not context.user_data.get("awaiting_captcha"):
            q_text, _ = start_captcha_for_user(context.user_data)
        else:
            q_text = context.user_data.get("captcha_question", "Please answer the verification question.")
        await q.edit_message_text(
            "🛡️ Verification required\n\n"
            "Please solve this simple question before using the bot:\n\n"
            f"*{q_text}*\n\n"
            "Reply with the numeric answer. You have 3 attempts.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="main:menu")]]),
        )
        return

    try:
        _, standard, method = q.data.split(":", 2)
    except Exception:
        await q.edit_message_text("Invalid selection. Returning to main menu.", reply_markup=MAIN_MENU_KB)
        return

    context.user_data["fssc_standard"] = standard
    # mark we're in an FSSC flow
    context.user_data["in_fssc_flow"] = True

    if method == "coid":
        context.user_data["awaiting_coid"] = True
        await q.edit_message_text(PROMPT_COID, parse_mode=ParseMode.MARKDOWN)
        return

    if method == "company":
        # Ask for company name (we will attempt to resolve COID automatically; if not found we will ask user for COID)
        context.user_data["awaiting_fssc_company"] = True
        await q.edit_message_text("Please enter the *company name* (exact or partial). I will try to find the COID automatically; if I can't, I'll ask you to provide the COID manually.", parse_mode=ParseMode.MARKDOWN)
        return

    await q.edit_message_text("Unknown method selected.", reply_markup=MAIN_MENU_KB)

# certification-body selection handler (unchanged behavior except for SISBEL addition)
async def cb_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # require verification for callbacks
    if not context.user_data.get("verified"):
        # ensure captcha is initialized
        if not context.user_data.get("awaiting_captcha"):
            q_text, _ = start_captcha_for_user(context.user_data)
        else:
            q_text = context.user_data.get("captcha_question", "Please answer the verification question.")
        await q.edit_message_text(
            "🛡️ Verification required\n\n"
            "Please solve this simple question before using the bot:\n\n"
            f"*{q_text}*\n\n"
            "Reply with the numeric answer. You have 3 attempts.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="main:menu")]]),
        )
        return

    data = q.data  # e.g., "cb:qro_com"
    _, cb = data.split(":", 1)
    context.user_data["cb"] = cb

    if cb == "fssc":
        context.user_data["awaiting_coid"] = True
        await q.edit_message_text(PROMPT_COID, parse_mode=ParseMode.MARKDOWN)
        return

    if cb == "infinity":
        context.user_data["awaiting_cert_no"] = True
        await q.edit_message_text("You selected *Infinity*.\n\n" + PROMPT_CERT_NO, parse_mode=ParseMode.MARKDOWN)
        return

    if cb == "qsi":
        context.user_data["awaiting_cert_no"] = True
        await q.edit_message_text("You selected *QSI*.\n\nPlease provide the certificate ID exactly as it is printed on your certificate.", parse_mode=ParseMode.MARKDOWN)
        return

    if cb in ("qro_com", "qro_org"):
        context.user_data["awaiting_cert_no"] = True
        await q.edit_message_text("You selected *QRO*.\n\n" + PROMPT_CERT_NO, parse_mode=ParseMode.MARKDOWN)
        return

    if cb == "sisbel":
        # SISBEL flow: ask for company name first
        context.user_data["awaiting_sisbel_company"] = True
        await q.edit_message_text("You selected *SISBEL*.\n\nPlease enter the *company name* to search:", parse_mode=ParseMode.MARKDOWN)
        return

    context.user_data["awaiting_cert_no"] = True
    await q.edit_message_text("You selected *Other / Auto-Fallback*.\n\n" + PROMPT_CERT_NO, parse_mode=ParseMode.MARKDOWN)

# 'again' handler -> return to main menu or restart flow
async def cb_again_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # require verification for callbacks
    if not context.user_data.get("verified"):
        # ensure captcha is initialized
        if not context.user_data.get("awaiting_captcha"):
            q_text, _ = start_captcha_for_user(context.user_data)
        else:
            q_text = context.user_data.get("captcha_question", "Please answer the verification question.")
        await q.edit_message_text(
            "🛡️ Verification required\n\n"
            "Please solve this simple question before using the bot:\n\n"
            f"*{q_text}*\n\n"
            "Reply with the numeric answer. You have 3 attempts.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="main:menu")]]),
        )
        return

    # clear flow but keep verified
    clear_flow_keep_verified(context.user_data)
    try:
        await q.edit_message_text(MAIN_MENU_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU_KB)
    except Exception:
        await q.edit_message_text("Welcome — choose an option.", reply_markup=MAIN_MENU_KB)

# Primary message handler (text input)
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Empty message received. Please send text or use /start to begin.", reply_markup=MAIN_MENU_KB)
        return

    # ----- CAPTCHA handling (highest priority) -----
    if context.user_data.get("awaiting_captcha"):
        # Expect numeric answer
        answer_expected = context.user_data.get("captcha_answer")
        attempts_left = context.user_data.get("captcha_attempts", 0)
        # sanitize the user input: extract first integer-like token
        user_resp = text.strip()
        # Accept numeric forms (allow + or -)
        m = re.search(r"[-+]?\d+", user_resp)
        if not m:
            # non-numeric reply
            context.user_data["captcha_attempts"] = attempts_left - 1
            if context.user_data["captcha_attempts"] <= 0:
                # exhausted attempts -> fully clear state (including verified)
                context.user_data.clear()
                await update.message.reply_text("❌ Verification failed. You have used all attempts. Please send /start to try again.")
                return
            await update.message.reply_text(f"Please reply with a numeric answer. Attempts left: {context.user_data['captcha_attempts']}")
            return
        user_ans = m.group(0)
        if user_ans == answer_expected:
            # Verified!
            context.user_data["verified"] = True
            context.user_data.pop("awaiting_captcha", None)
            context.user_data.pop("captcha_question", None)
            context.user_data.pop("captcha_answer", None)
            context.user_data.pop("captcha_attempts", None)
            # Show main menu now
            try:
                await update.message.reply_text("✅ Correct — verification passed. Welcome!", parse_mode=ParseMode.MARKDOWN)
                await update.message.reply_text(MAIN_MENU_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU_KB)
            except Exception:
                await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=MAIN_MENU_KB)
            return
        else:
            # Wrong answer
            attempts_left = attempts_left - 1
            context.user_data["captcha_attempts"] = attempts_left
            if attempts_left <= 0:
                context.user_data.clear()
                await update.message.reply_text("❌ Verification failed. You have used all attempts. Please send /start to try again.")
                return
            else:
                await update.message.reply_text(f"Incorrect answer. Attempts left: {attempts_left}. Please try again.")
                return
    # ----- END CAPTCHA handling -----

    # ----- SISBEL flow handling (company -> cert) -----
    if context.user_data.get("awaiting_sisbel_company"):
        # save company and ask for certificate number
        company = text.strip().upper()
        context.user_data.pop("awaiting_sisbel_company", None)
        context.user_data["sisbel_company"] = company
        context.user_data["awaiting_sisbel_cert"] = True
        await update.message.reply_text("Now enter the *certificate number*:", parse_mode=ParseMode.MARKDOWN)
        return

    if context.user_data.get("awaiting_sisbel_cert"):
        company = context.user_data.get("sisbel_company")
        cert_no = text
        # clear flow but keep verified
        clear_flow_keep_verified(context.user_data)
        await update.message.reply_text(PROCESSING)

        payload = {
            "firmaaranan": company,
            "belgenoaranan": cert_no,
            "captcha": {"sayi1": 0, "sayi2": 0, "operator": "*", "cevap": 0}
        }

        try:
            r = requests.post(SISBEL_API_URL, json=payload, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()

            if data.get("success") and data.get("data"):
                cert = data['data']
                formatted_message = (
                    f"✅ *Certificate Details:*\n\n"
                    f"🏢 *Company:* {cert.get('firma')}\n"
                    f"🏙 *City/Province:* {cert.get('il')}\n"
                    f"📍 *Address:* {cert.get('adres')}\n"
                    f"🏳 *Country:* {cert.get('ulke')}\n"
                    f"🎖 *Standard:* {cert.get('belge')}\n"
                    f"🔑 *Certificate Number:* {cert.get('sertifikaNo')}\n"
                    f"📋 *Scope:* {cert.get('kapsam')}\n"
                    f"📅 *Validity Date:* {cert.get('belgegecerliliktarihi')}\n"
                    f"✔ *Validity Status:* {cert.get('belgegecerlilikdurumu')}"
                )
                await update.message.reply_text(formatted_message, parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text(NOT_FOUND)

        except Exception as e:
            logger.exception("SISBEL API error: %s", e)
            await update.message.reply_text(f"❌ Error fetching SISBEL data: {e}")

        # end SISBEL flow - present main menu options again
        await update.message.reply_text(THANK_YOU_BRIEF)
        await update.message.reply_text("—", reply_markup=AGAIN_KB)
        return

    # ----- FSSC flow: company-name -> attempt auto-COID resolution ----- 
    if context.user_data.get("awaiting_fssc_company"):
        company_name = text.strip()
        context.user_data.pop("awaiting_fssc_company", None)
        context_user = context.user_data
        context_user["last_searched_company"] = company_name
        # attempt to resolve COID
        await update.message.reply_text(PROCESSING)
        coid = await asyncio.to_thread(find_coid_by_company_name, company_name)
        if not coid:
            # couldn't auto-resolve, ask user to provide COID
            context_user["awaiting_coid"] = True
            # keep fssc flow marker set
            context_user["in_fssc_flow"] = True
            await update.message.reply_text("I couldn't automatically find the COID for that company.\n\nPlease provide the COID ID so I can fetch the certificate.", parse_mode=ParseMode.MARKDOWN)
            return
        # got COID -> fetch FSSC details
        res = await asyncio.to_thread(fetch_fssc_by_coid, coid)
        # clear flow but keep verified
        clear_flow_keep_verified(context.user_data)
        if isinstance(res, dict) and res.get("error"):
            await update.message.reply_text(f"{ERROR_MSG}\n\n{html.escape(res.get('error'))}")
            await update.message.reply_text(THANK_YOU_BRIEF)
            await update.message.reply_text("—", reply_markup=AGAIN_KB)
            return
        if res == "not_found":
            await update.message.reply_text(f"❌ No certificate found for COID: {html.escape(coid)}")
            await update.message.reply_text(THANK_YOU_BRIEF)
            await update.message.reply_text("—", reply_markup=AGAIN_KB)
            return
        formatted = format_fssc_result(res) or NOT_FOUND
        await safe_send_text(update.message.reply_text, formatted, disable_web_page_preview=False)
        cb_name = "FSSC Public Register"
        ab = "FSSC / Not provided"
        await update.message.reply_text(f"🔎 *Source:* {cb_name}\n🏷️ *Accreditation Body:* {ab}", parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text(THANK_YOU_BRIEF)
        await update.message.reply_text("—", reply_markup=AGAIN_KB)
        return

    # ----- FSSC COID flow (used both when user selects Verify by COID or after company name fallback) -----
    if context.user_data.get("awaiting_coid"):
        coid_input = text.strip()
        # If user input contains "COID: ..." remove label
        coid_input = re.sub(r"(?i)^COID:\s*", "", coid_input).strip()
        # clear temporary flow while keeping verification
        # preserve marker whether we were in fssc flow
        in_fssc_flow = bool(context.user_data.get("in_fssc_flow"))
        clear_flow_keep_verified(context.user_data)
        if in_fssc_flow:
            # set nothing else, we will fetch fssc_by_coid
            await update.message.reply_text(PROCESSING)
            res = await asyncio.to_thread(fetch_fssc_by_coid, coid_input)
            if isinstance(res, dict) and res.get("error"):
                await update.message.reply_text(f"{ERROR_MSG}\n\n{html.escape(res.get('error'))}")
                await update.message.reply_text(THANK_YOU_BRIEF)
                await update.message.reply_text("—", reply_markup=AGAIN_KB)
                return
            if res == "not_found":
                await update.message.reply_text(f"❌ No certificate found for COID: {html.escape(coid_input)}")
                await update.message.reply_text(THANK_YOU_BRIEF)
                await update.message.reply_text("—", reply_markup=AGAIN_KB)
                return
            formatted = format_fssc_result(res) or NOT_FOUND
            await safe_send_text(update.message.reply_text, formatted, disable_web_page_preview=False)
            cb_name = "FSSC Public Register"
            ab = "FSSC / Not provided"
            await update.message.reply_text(f"🔎 *Source:* {cb_name}\n🏷️ *Accreditation Body:* {ab}", parse_mode=ParseMode.MARKDOWN)
            await update.message.reply_text(THANK_YOU_BRIEF)
            await update.message.reply_text("—", reply_markup=AGAIN_KB)
            return
        else:
            # Not an fssc-specific coid prompt: handle as general COID (original behavior)
            # clear temporary flow while keeping verification (done already)
            await update.message.reply_text(PROCESSING)
            res = await asyncio.to_thread(fetch_fssc_by_coid, coid_input)
            if isinstance(res, dict) and res.get("error"):
                await update.message.reply_text(f"{ERROR_MSG}\n\n{html.escape(res.get('error'))}")
                await update.message.reply_text(THANK_YOU_BRIEF)
                await update.message.reply_text("—", reply_markup=AGAIN_KB)
                return
            if res == "not_found":
                await update.message.reply_text(f"❌ No certificate found for COID: {html.escape(coid_input)}")
                await update.message.reply_text(THANK_YOU_BRIEF)
                await update.message.reply_text("—", reply_markup=AGAIN_KB)
                return
            formatted = format_fssc_result(res) or NOT_FOUND
            await safe_send_text(update.message.reply_text, formatted, disable_web_page_preview=False)
            cb_name = "FSSC Public Register"
            ab = "FSSC / Not provided"
            await update.message.reply_text(f"🔎 *Source:* {cb_name}\n🏷️ *Accreditation Body:* {ab}", parse_mode=ParseMode.MARKDOWN)
            await update.message.reply_text(THANK_YOU_BRIEF)
            await update.message.reply_text("—", reply_markup=AGAIN_KB)
            return

    # awaiting certificate number (other CBs)
    if context.user_data.get("awaiting_cert_no"):
        cert_no = text
        context.user_data["cert_no"] = cert_no
        context.user_data.pop("awaiting_cert_no", None)
        cb = context.user_data.get("cb")

        if cb == "infinity":
            await update.message.reply_text(PROCESSING)
            res = await asyncio.to_thread(infinity_post_cert, cert_no)
            if not res:
                await update.message.reply_text(NOT_FOUND)
                await update.message.reply_text(THANK_YOU_BRIEF)
                await update.message.reply_text("—", reply_markup=AGAIN_KB)
                # clear flow but keep verified
                clear_flow_keep_verified(context.user_data)
                return
            await safe_send_text(update.message.reply_text, format_infty(res) or NOT_FOUND, disable_web_page_preview=False)
            cb_name = "Infinity Cert International"
            ab = res.get("dob") or "Not provided"
            await update.message.reply_text(f"🔎 *Source:* {cb_name}\n🏷️ *Accreditation Body:* {ab}", parse_mode=ParseMode.MARKDOWN)
            await update.message.reply_text(THANK_YOU_BRIEF)
            await update.message.reply_text("—", reply_markup=AGAIN_KB)
            # clear flow but keep verified
            clear_flow_keep_verified(context.user_data)
            return

        if cb == "qsi":
            await update.message.reply_text(PROCESSING)
            parsed = await asyncio.to_thread(fetch_qsi_simple, cert_no)
            if isinstance(parsed, dict) and parsed.get("error"):
                await update.message.reply_text(f"{ERROR_MSG}\n\n{parsed.get('error')}")
                await update.message.reply_text(THANK_YOU_BRIEF)
                await update.message.reply_text("—", reply_markup=AGAIN_KB)
                # clear flow but keep verified
                clear_flow_keep_verified(context.user_data)
                return
            if not parsed or not any(parsed.get(k) for k in ("name", "certificate_id", "standard")):
                await update.message.reply_text(NOT_FOUND)
                await update.message.reply_text(THANK_YOU_BRIEF)
                await update.message.reply_text("—", reply_markup=AGAIN_KB)
                # clear flow but keep verified
                clear_flow_keep_verified(context.user_data)
                return
            await safe_send_text(update.message.reply_text, format_qsi_simple(parsed) or NOT_FOUND, disable_web_page_preview=False)
            cb_name = "QSI (qsicert.ca)"
            ab = parsed.get("accreditation") or parsed.get("accreditation_body") or "Not provided"
            await update.message.reply_text(f"🔎 *Source:* {cb_name}\n🏷️ *Accreditation Body:* {ab}", parse_mode=ParseMode.MARKDOWN)
            await update.message.reply_text(THANK_YOU_BRIEF)
            await update.message.reply_text("—", reply_markup=AGAIN_KB)
            # clear flow but keep verified
            clear_flow_keep_verified(context.user_data)
            return

        # For QRO and Other: ask for issue date typed by user
        context.user_data["awaiting_issue_date"] = True
        await update.message.reply_text(PROMPT_ISSUE_DATE_TEXT)
        return

    # awaiting issue date typed by user
    if context.user_data.get("awaiting_issue_date"):
        date_conv = validate_date_input(text)
        if not date_conv:
            await update.message.reply_text("Date format not recognized. Please use `YYYY-MM-DD` or `DD/MM/YYYY`. Example: 2025-10-10", parse_mode=ParseMode.MARKDOWN)
            return
        cert_no = context.user_data.get("cert_no")
        cb = context.user_data.get("cb") or "other"
        # clear flow but keep verified
        clear_flow_keep_verified(context.user_data)
        await update.message.reply_text(PROCESSING)
        msg, ok, meta = await verify_for_cb(cb, cert_no, date_conv)
        await safe_send_text(update.message.reply_text, msg or NOT_FOUND, disable_web_page_preview=False)
        cb_name = meta.get("cb", "Unknown")
        ab = meta.get("ab", "Not provided")
        await update.message.reply_text(f"🔎 *Source:* {cb_name}\n🏷️ *Accreditation Body:* {ab}", parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text(THANK_YOU_BRIEF)
        await update.message.reply_text("—", reply_markup=AGAIN_KB)
        return

    # default fallback
    await update.message.reply_text("Please use the main menu. /start", reply_markup=MAIN_MENU_KB)

# ---------------- Main ----------------
def main() -> None:
    app = ApplicationBuilder().token(TOKEN).build()

    # Main menu handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern=r"^main:"))
    # Certificate flow handlers
    app.add_handler(CallbackQueryHandler(type_selected_callback, pattern=r"^type:"))
    app.add_handler(CallbackQueryHandler(cb_selected_callback, pattern=r"^cb:"))
    app.add_handler(CallbackQueryHandler(cb_again_handler, pattern=r"^again$"))
    # FSSC handlers
    app.add_handler(CallbackQueryHandler(fssc_selected_callback, pattern=r"^fssc:"))
    app.add_handler(CallbackQueryHandler(fssc_method_callback, pattern=r"^fssc_method:"))
    # Text handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    logger.info("TajCert Bot (main menu + SISBEL + FSSC fixes) starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
