#!/usr/bin/env python3
"""
cert_bot_qrocom_submit.py (updated infinity_post_cert)

Unified certificate verification bot — updated qrocert.com submission method + adaptive CB list.
This variant contains a corrected `infinity_post_cert` which sends JSON to the Infinity API
and accepts JSON responses (fixes 415 Unsupported Media Type).
"""

import logging
import asyncio
import html
import re
from typing import Optional, Dict, Any, Tuple

import requests
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# ---------------- CONFIG ----------------
TOKEN = "8219317787:AAHuYrGVtY8U8gA5mvJVjJtOw487Z2Lhwsg"  # replace with your actual token if needed
REQUEST_TIMEOUT = 18.0
INFINITY_API_URL = "https://infinitycert.com/wp-json/v2/ssr_find_all"
# Note: global INFINITY_HEADERS preserved but function will override with JSON headers as needed
INFINITY_HEADERS = {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "CertCheckBot/1.0"}
QRO_COM_VERIFY = "https://qrocert.com/verify-certification.aspx"
QRO_COM_PAGE = "https://qrocert.com/checkcert.aspx"   # fallback if needed
QRO_ORG_PAGE = "https://qrocert.org/verify.aspx"

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("cert_bot_qrocom_submit")

# ---------------- UI STRINGS ----------------
START_TEXT = (
    "Welcome to TajCert Bot! I am your professional certificate verification assistant, here to help you quickly and securely verify the authenticity of your certificates, including ISO, FSSC, and other professional certifications. Simply provide the required details such as COID, certificate number, or issue date, and I will fetch accurate information from trusted sources. For assistance or to report any issues, you can contact the developer at @jamshidiyan. Thank you for choosing TajCert Bot, your reliable partner in certificate verification!.\n\n"
    "Please select the type of certificate you would like to verify."
)
TYPE_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔎 Verify FSSC Certificate", callback_data="type:fssc")],
    [InlineKeyboardButton("🔎 Verify ISO Certificate", callback_data="type:iso")],
    [InlineKeyboardButton("🔎 Verify HACCP Certificate", callback_data="type:haccp")],
    [InlineKeyboardButton("🔎 Verify GMP Certificate", callback_data="type:gmp")],
    [InlineKeyboardButton("🔎 Verify Halal Certificate", callback_data="type:halal")]
])

# base CB options — will be adapted per certificate type
CB_BASE = [
    ("QRO (qrocert.com)", "cb:qro_com"),
    ("QRO (qrocert.org)", "cb:qro_org"),
    ("Infinity (infinitycert.com)", "cb:infinity"),
    ("FSSC (fssc.com public register)", "cb:fssc"),
    ("Other", "cb:other"),
]

AGAIN_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Next", callback_data="again")]])

PROMPT_COID = "Please provide the COID number associated with the certificate you wish to verify. The COID is a unique identifier that helps TajCert Bot accurately locate and verify your certificate in our trusted sources. Providing the correct COID ensures a fast and reliable verification process. Once entered, I will process your request and fetch the relevant certificate details for you."
PROMPT_CERT_NO = "Please provide the certificate number of the document you wish to verify. The certificate number is a unique identifier that allows TajCert Bot to accurately locate and confirm the authenticity of your certificate. Entering the correct certificate number ensures a smooth and reliable verification process. Once provided, I will process your request and retrieve the relevant certificate information from trusted sources."
PROMPT_ISSUE_DATE_TEXT = "Please type the issue date of the certificate in either YYYY-MM-DD or DD/MM/YYYY format. The issue date is important to accurately verify your certificate, as it helps TajCert Bot match your certificate with trusted sources. Make sure to enter the date correctly to ensure a smooth and reliable verification process. Once provided, I will process your request and retrieve the certificate details for you."

PROCESSING = "🔄 Processing your request — please wait a moment while TajCert Bot securely fetches and verifies the certificate information from trusted sources. This may take a few seconds, so thank you for your patience."
NOT_FOUND = "❌ No certificate information could be found on the selected source for the details you provided. Please double-check the information you entered, such as COID, certificate number, or issue date, and try again. If the details are correct and you still face issues, you may contact the developer for assistance at @jamshidiyan."
ERROR_MSG = "⚠️ An internal error occurred while processing your request. TajCert Bot was unable to complete the verification at this time. Please try again in a few moments. If the problem persists, you can contact the developer for assistance at @jamshidiyan."

THANK_YOU_TEXT = (
    "Certificate verification completed! Thank you for using TajCert Bot. For assistance or feedback, please contact @jamshidiyan\n\n"
)

# ---------------- simple in-memory cache (runtime) ----------------
_cache: Dict[str, Any] = {}


def cache_get(key: str) -> Optional[Any]:
    return _cache.get(key)


def cache_set(key: str, value: Any) -> None:
    _cache[key] = value


# ---------------- Generic helpers & parsers ----------------

def validate_date_input(s: str) -> Optional[str]:
    s = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        y, m, d = s.split("-")
        return f"{int(d):02d}/{int(m):02d}/{int(y):04d}"
    if re.match(r"^\d{2}/\d{2}/\d{4}$", s):
        return s
    return None


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
    data["address"] = address_h6.find_next("address").get_text(" ", strip=True) if address_h6 and address_h6.find_next("address") else find_h6_value("address")
    # product types
    ptypes_h6 = article.find("h6", string=lambda s: s and "product types" in s.lower())
    if ptypes_h6:
        ul = ptypes_h6.find_next_sibling("ul")
        data["product_types"] = [li.get_text(" ", strip=True) for li in ul.find_all("li")] if ul else []
    else:
        data["product_types"] = []
    # scope
    scope_h6 = article.find("h6", string=lambda s: s and "scope statement" in s.lower())
    data["scope_statement"] = (scope_h6.find_next_sibling("p").get_text(" ", strip=True) if scope_h6 and scope_h6.find_next_sibling("p") else None)
    # categories
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
    return data


def format_fssc_result(data: dict) -> str:
    esc = html.escape
    if not data or data == "not_found":
        return None
    lines = [f"<b>{esc(data.get('organization') or '-')}</b>", ""]
    lines.append(f"📜 <b>Scheme:</b> {esc(data.get('scheme') or '-')}")
    lines.append(f"✅ <b>Valid until:</b> {esc(data.get('valid_until') or '-')}")
    lines.append(f"🆔 <b>COID:</b> {esc(data.get('coid') or '-')}")
    lines.append(f"📍 <b>Address:</b> {esc(data.get('address') or '-')}")
    ptypes = ", ".join(data.get("product_types") or []) or "-"
    lines.append(f"🛒 <b>Product types:</b> {esc(ptypes)}")
    lines.append(f"📝 <b>Scope:</b> {esc(data.get('scope_statement') or '-')}")
    if data.get("categories"):
        lines.append("")
        lines.append("<b>📂 FSSC Categories:</b>")
        for c in data.get("categories", []):
            lines.append(f" - {esc(c.get('code') or '-') } — {esc(c.get('title') or '-')}")
    lines.append("")
    if data.get("fssc_url"):
        lines.append(f"🔗 View on FSSC: <a href=\"{esc(data['fssc_url'])}\">{esc(data['fssc_url'])}</a>")
    return "\n".join(lines)


# ---------- Infinity ----------
def infinity_post_cert(cert_no: str) -> Optional[Dict]:
    """
    Fixed Infinity Cert API function – sends JSON payload and accepts JSON.
    Returns parsed dict on success or None on failure.
    """
    key = f"infty:{cert_no}"
    cached = cache_get(key)
    if cached:
        return cached

    try:
        # Use JSON content-type (the API returns 415 when form-encoded)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "CertCheckBot/1.0"
        }
        payload = {"postID": cert_no}
        # send as JSON
        r = requests.post(INFINITY_API_URL, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        j = r.json()

        # Response can contain 'success' and either numeric keys or 'data'
        if isinstance(j, dict):
            # If 'data' is a dict, prefer it
            data_part = j.get("data")
            if isinstance(data_part, dict) and data_part:
                cache_set(key, data_part)
                return data_part
            # Otherwise, some responses have numbered keys (e.g. "0": {...}, "1": {...})
            for k, v in j.items():
                if k.isdigit() and isinstance(v, dict):
                    cache_set(key, v)
                    return v
            # Sometimes the API returns list under 'data' or root — try to return first dict found
            if isinstance(j.get("data"), list) and j.get("data"):
                first = j["data"][0]
                if isinstance(first, dict):
                    cache_set(key, first)
                    return first
            # Try scanning top-level items to find a dict
            for v in j.values():
                if isinstance(v, dict) and any(isinstance(val, (str, int)) for val in v.values()):
                    cache_set(key, v)
                    return v

        return None

    except Exception as e:
        logger.exception("Infinity API error: %s", e)
        return None


def format_infty(obj: dict) -> str:
    esc = html.escape
    if not obj:
        return None
    lines = ["<b>🔹 Infinity Cert International - Certificate Information</b>\n"]
    fields = [("rid", "Certificate No"), ("stdname", "Issue Date"), ("fathersname", "Expiry Date"),
              ("subject", "Standard"), ("dob", "Accreditation Body"), ("gender", "Organization"),
              ("address", "Address"), ("mnam", "Scope"), ("c1", "Status")]
    for k, label in fields:
        val = obj.get(k)
        lines.append(f"<b>{esc(label)}:</b> {esc(str(val)) if val not in (None, '') else '-'}")
    return "\n".join(lines)


# ---------- QRO (qrocert.com) simple submit ----------
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
        "standard": g(5)
    }
    for k, v in parsed.items():
        if v is not None:
            parsed[k] = v.strip() or None
    return parsed


def submit_qro_com(cert_no: str, issue_date: str) -> Tuple[bool, Dict[str, Optional[str]]]:
    """
    Simpler submission path for qrocert.com:
    - GET verify-certification.aspx to read hidden inputs
    - fill TextBox1, TextBox2 and Button1
    - POST back to verify-certification.aspx and parse result
    """
    try:
        sess = requests.Session()
        sess.headers.update({"User-Agent": "CertCheckBot/1.0"})
        # GET the verify page (contains the form)
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
    # Keep previous generic ASP submit for qrocert.org
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
    lines = ["<b>🔷 QRO Certification Company</b>"]
    lines.append(f"<b>🏢 Company:</b> {esc(parsed.get('company') or '-')}")
    lines.append(f"<b>🔢 Certificate No:</b> {esc(parsed.get('certificate_no') or '-')}")
    lines.append(f"<b>📅 Issue Date:</b> {esc(parsed.get('issue_date') or '-')}")
    lines.append(f"<b>⏳ Expiry Date:</b> {esc(parsed.get('expiry_date') or '-')}")
    lines.append(f"<b>✅ Status:</b> {esc(parsed.get('status') or '-')}")
    lines.append(f"<b>🔖 Standard:</b> {esc(parsed.get('standard') or '-')}")
    lines.append(f"<b>📍 Address:</b> {esc(parsed.get('address') or '-')}")
    return "\n".join(lines)


# ---------------- Dispatcher ----------------
async def verify_for_cb(cb: str, cert_no: str, issue_date_qro: Optional[str]) -> Tuple[str, bool]:
    """
    cb: 'qro_com', 'qro_org', 'infinity', 'fssc', 'other'
    issue_date_qro is DD/MM/YYYY when provided (for QRO)
    """
    # Infinity
    if cb == "infinity":
        inf = await asyncio.to_thread(infinity_post_cert, cert_no)
        if inf:
            return (format_infty(inf) or "Found (Infinity) but unable to format.", True)
        return (NOT_FOUND, False)

    # QRO (qrocert.com)
    if cb == "qro_com":
        if not issue_date_qro:
            return ("QRO (qrocert.com) requires an issue date. Please provide it in `YYYY-MM-DD` or `DD/MM/YYYY` format.", False)
        ok, parsed = await asyncio.get_event_loop().run_in_executor(None, submit_qro_com, cert_no, issue_date_qro)
        if ok and any(parsed.get(k) for k in ("company", "status", "standard", "issue_date")):
            return ("<b>Source: qrocert.com</b>\n\n" + (format_qro(parsed) or ""), True)
        if not ok and parsed and parsed.get("error"):
            return (ERROR_MSG + "\n\n" + parsed.get("error"), False)
        return (NOT_FOUND, False)

    # QRO (qrocert.org)
    if cb == "qro_org":
        if not issue_date_qro:
            return ("QRO (qrocert.org) requires an issue date. Please provide it in `YYYY-MM-DD` or `DD/MM/YYYY` format.", False)
        ok, parsed = await asyncio.get_event_loop().run_in_executor(None, submit_qro_org, cert_no, issue_date_qro)
        if ok and any(parsed.get(k) for k in ("company", "status", "standard", "issue_date")):
            return ("<b>Source: qrocert.org</b>\n\n" + (format_qro(parsed) or ""), True)
        if not ok and parsed and parsed.get("error"):
            return (ERROR_MSG + "\n\n" + parsed.get("error"), False)
        return (NOT_FOUND, False)

    # FSSC
    if cb == "fssc":
        res = await asyncio.to_thread(fetch_fssc_by_coid, cert_no)
        if isinstance(res, dict) and res.get("error"):
            return (ERROR_MSG + "\n\n" + html.escape(res.get('error')), False)
        if res == "not_found":
            return (NOT_FOUND, False)
        return (format_fssc_result(res) or NOT_FOUND, True)

    # Other: try Infinity -> QRO_com -> QRO_org -> FSSC
    inf = await asyncio.to_thread(infinity_post_cert, cert_no)
    if inf:
        return (format_infty(inf) or "Found (Infinity) but unable to format.", True)
    if issue_date_qro:
        ok, p = await asyncio.get_event_loop().run_in_executor(None, submit_qro_com, cert_no, issue_date_qro)
        if ok and any(p.get(k) for k in ("company", "status", "standard", "issue_date")):
            return ("<b>Source: qrocert.com</b>\n\n" + (format_qro(p) or ""), True)
        ok2, p2 = await asyncio.get_event_loop().run_in_executor(None, submit_qro_org, cert_no, issue_date_qro)
        if ok2 and any(p2.get(k) for k in ("company", "status", "standard", "issue_date")):
            return ("<b>Source: qrocert.org</b>\n\n" + (format_qro(p2) or ""), True)
    fssc_res = await asyncio.to_thread(fetch_fssc_by_coid, cert_no)
    if isinstance(fssc_res, dict) and fssc_res.get("error"):
        return (ERROR_MSG + "\n\n" + html.escape(fssc_res.get("error")), False)
    if fssc_res != "not_found":
        return (format_fssc_result(fssc_res) or NOT_FOUND, True)
    return (NOT_FOUND, False)


# ---------------- Telegram handlers ----------------

def make_cb_keyboard_for_type(ctype: str) -> InlineKeyboardMarkup:
    """
    Build certification-body keyboard dynamically:
    - For ISO: include both qro_com and qro_org
    - For other types (except FSSC): include only qro_com (no qro_org)
    """
    kb = []
    if ctype == "iso":
        # keep both QRO options for ISO
        kb.append([InlineKeyboardButton("QRO - IAF", callback_data="cb:qro_com")])
        kb.append([InlineKeyboardButton("QRO - Non-IAF", callback_data="cb:qro_org")])
    else:
        # include only qrocert.com (except FSSC which doesn't reach here)
        kb.append([InlineKeyboardButton("QRO Cerification LLP", callback_data="cb:qro_com")])
    # add common others
    kb.append([InlineKeyboardButton("Infinity Cert International", callback_data="cb:infinity")])
    return InlineKeyboardMarkup(kb)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(START_TEXT, parse_mode="Markdown", reply_markup=TYPE_KB)


async def type_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data  # e.g., "type:iso"
    _, ctype = data.split(":", 1)
    context.user_data.clear()
    context.user_data["cert_type"] = ctype

    # If FSSC chosen -> directly ask COID (skip CB selection)
    if ctype == "fssc":
        context.user_data["awaiting_coid"] = True
        await q.edit_message_text(PROMPT_COID)
        return

    # else show CB keyboard built for the certificate type
    kb = make_cb_keyboard_for_type(ctype)
    await q.edit_message_text(f"You selected *{ctype.upper()}*.\n\nPlease select the Certification Body to proceed:", parse_mode="Markdown", reply_markup=kb)


async def cb_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data  # e.g., "cb:qro_com"
    _, cb = data.split(":", 1)
    context.user_data["cb"] = cb

    if cb == "fssc":
        context.user_data["awaiting_coid"] = True
        await q.edit_message_text(PROMPT_COID)
        return

    if cb == "infinity":
        context.user_data["awaiting_cert_no"] = True
        await q.edit_message_text("You selected *Infinity*.\n\n" + PROMPT_CERT_NO, parse_mode="Markdown")
        return

    if cb in ("qro_com", "qro_org"):
        context.user_data["awaiting_cert_no"] = True
        await q.edit_message_text("You selected *QRO*.\n\n" + PROMPT_CERT_NO, parse_mode="Markdown")
        return

    # Other
    context.user_data["awaiting_cert_no"] = True
    await q.edit_message_text("You selected *Other*.\n\n" + PROMPT_CERT_NO, parse_mode="Markdown")


async def cb_again_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.clear()
    await q.edit_message_text(START_TEXT, parse_mode="Markdown", reply_markup=TYPE_KB)


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # FSSC flow: COID directly
    if context.user_data.get("awaiting_coid"):
        coid = text
        context.user_data.clear()
        await update.message.reply_text(PROCESSING)
        res = await asyncio.to_thread(fetch_fssc_by_coid, coid)
        if isinstance(res, dict) and res.get("error"):
            await update.message.reply_text(f"{ERROR_MSG}\n\n{html.escape(res.get('error'))}")
            await update.message.reply_text(THANK_YOU_TEXT, parse_mode="Markdown")
            await update.message.reply_text("—", reply_markup=AGAIN_KB)
            return
        if res == "not_found":
            await update.message.reply_text(f"❌ No certificate found for COID: {html.escape(coid)}")
            await update.message.reply_text(THANK_YOU_TEXT, parse_mode="Markdown")
            await update.message.reply_text("—", reply_markup=AGAIN_KB)
            return
        await update.message.reply_text(format_fssc_result(res), parse_mode="HTML", disable_web_page_preview=False)
        await update.message.reply_text(THANK_YOU_TEXT, parse_mode="Markdown")
        await update.message.reply_text("—", reply_markup=AGAIN_KB)
        return

    # awaiting certificate number
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
                await update.message.reply_text(THANK_YOU_TEXT, parse_mode="Markdown")
                await update.message.reply_text("—", reply_markup=AGAIN_KB)
                context.user_data.clear()
                return
            await update.message.reply_text(format_infty(res), parse_mode="HTML", disable_web_page_preview=False)
            await update.message.reply_text(THANK_YOU_TEXT, parse_mode="Markdown")
            await update.message.reply_text("—", reply_markup=AGAIN_KB)
            context.user_data.clear()
            return

        # For QRO and Other: ask for issue date typed by user
        context.user_data["awaiting_issue_date"] = True
        await update.message.reply_text(PROMPT_ISSUE_DATE_TEXT)
        return

    # awaiting issue date typed by user
    if context.user_data.get("awaiting_issue_date"):
        date_conv = validate_date_input(text)
        if not date_conv:
            await update.message.reply_text("Date format not recognized. Please use `YYYY-MM-DD` or `DD/MM/YYYY`. Example: 2025-10-10", parse_mode="Markdown")
            return
        cert_no = context.user_data.get("cert_no")
        cb = context.user_data.get("cb") or "other"
        context.user_data.clear()
        await update.message.reply_text(PROCESSING)
        msg, ok = await verify_for_cb(cb, cert_no, date_conv)
        await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=False)
        await update.message.reply_text(THANK_YOU_TEXT, parse_mode="Markdown")
        await update.message.reply_text("—", reply_markup=AGAIN_KB)
        return

    # default fallback
    await update.message.reply_text("Please start with /start and choose certificate type.", reply_markup=TYPE_KB)


# ---------------- Main ----------------
def main() -> None:
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(type_selected_callback, pattern=r"^type:"))
    app.add_handler(CallbackQueryHandler(cb_selected_callback, pattern=r"^cb:"))
    app.add_handler(CallbackQueryHandler(cb_again_handler, pattern=r"^again$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    logger.info("CertCheck Bot (qrocom submit) starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
