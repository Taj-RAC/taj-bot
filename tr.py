#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
TAJ Training Bot — updated:
- Enforce English UPPERCASE input for user info (first/last/job/company).
- Tell user to enter info only in English (both languages).
- After entering all fields, show confirmation with Confirm / Edit.
- Edit allows selecting a field to re-enter; then back to confirmation.
- Automatically converts names/job/company to UPPERCASE (stores in DB).
- Minimal robustness improvement to show_registration_confirmation to accept
  Update / CallbackQuery / Message.
"""

import os
import json
import random
import uuid
import logging
import html
import re
from datetime import datetime
from typing import Dict, Any, Optional

from fpdf import FPDF
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, Message, CallbackQuery
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("taj_training_bot_refactor")

# ---------------- CONFIG ----------------
TOKEN = "8474514020:AAEQMa0vYHBUlfkH0SGq8Lk0cw_uBNu4L3g"   # <-- جای توکن خود را اینجا بگذارید
DB_FILE = "taj_bot_final_db.json"
CERTS_DIR = "taj_certs_final"
PASS_TOTAL = 50            # required sum (pre + post) to issue certificate
PRE_PASS = 25              # pre-test pass threshold (for a "pre-pass" feedback)

VALID_SERIALS = {
    "ISO 9001": ["9001"],
    "ISO 22000": ["22000"],
    "HACCP": ["5712"],
    "GMP": ["GMP"],
    "FSSC 22000": ["FSSC22000"],
    "HALAL": ["HALAL"]
}

os.makedirs(CERTS_DIR, exist_ok=True)

if not os.path.exists(DB_FILE):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump({"users": {}}, f, ensure_ascii=False, indent=4)


def load_db() -> Dict[str, Any]:
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db(db: Dict[str, Any]) -> None:
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=4)


# ---------------- UI TEXTS & KEYBOARDS ----------------
def kb_lang():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("فارسی", callback_data="lang_fa"),
         InlineKeyboardButton("English", callback_data="lang_en")]
    ])


def kb_main(lang="fa"):
    if lang == "fa":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 اشتراک در تریننگ ها", callback_data="menu_register")],
            [InlineKeyboardButton("🔍 بررسی سِرتیفیکت", callback_data="menu_check")],
            [InlineKeyboardButton("📞 تماس با ما", callback_data="menu_contact")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_main")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Register for Trainings", callback_data="menu_register")],
        [InlineKeyboardButton("🔍 Check Certificate", callback_data="menu_check")],
        [InlineKeyboardButton("📞 Contact us", callback_data="menu_contact")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")],
    ])


def kb_trainings(lang="fa"):
    rows = []
    for i, t in enumerate(TRAININGS):
        rows.append([InlineKeyboardButton(t, callback_data=f"train|{i}")])
    rows.append([InlineKeyboardButton("↩️ بازگشت" if lang == "fa" else "↩️ Back", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def kb_after_registration(lang="fa"):
    if lang == "fa":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ تست اولیه", callback_data="start_pre")],
            [InlineKeyboardButton("▶️ تست نهایی", callback_data="start_post")],
            [InlineKeyboardButton("↩️ بازگشت به منو", callback_data="back_main")]
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Pre-test", callback_data="start_pre")],
        [InlineKeyboardButton("▶️ Post-test", callback_data="start_post")],
        [InlineKeyboardButton("↩️ Back to menu", callback_data="back_main")]
    ])


def kb_post_require_serial(lang="fa"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت" if lang == "fa" else "↩️ Back", callback_data="back_main")]])


def kb_reg_confirm(lang="fa"):
    if lang == "fa":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تایید اطلاعات", callback_data="reg_confirm")],
            [InlineKeyboardButton("✏️ ویرایش اطلاعات", callback_data="reg_edit")],
            [InlineKeyboardButton("↩️ بازگشت به منو", callback_data="back_main")]
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm data", callback_data="reg_confirm")],
        [InlineKeyboardButton("✏️ Edit data", callback_data="reg_edit")],
        [InlineKeyboardButton("↩️ Back to menu", callback_data="back_main")]
    ])


def kb_reg_edit_fields(lang="fa"):
    # Buttons for each field to edit
    if lang == "fa":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("نام", callback_data="edit_field|first_name")],
            [InlineKeyboardButton("تخلص", callback_data="edit_field|last_name")],
            [InlineKeyboardButton("عنوان وظیفه", callback_data="edit_field|job_title")],
            [InlineKeyboardButton("نام شرکت", callback_data="edit_field|company")],
            [InlineKeyboardButton("تاریخ", callback_data="edit_field|date")],
            [InlineKeyboardButton("↩️ بازگشت", callback_data="reg_back_to_confirm")]
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("First name", callback_data="edit_field|first_name")],
        [InlineKeyboardButton("Last name", callback_data="edit_field|last_name")],
        [InlineKeyboardButton("Job title", callback_data="edit_field|job_title")],
        [InlineKeyboardButton("Company", callback_data="edit_field|company")],
        [InlineKeyboardButton("Date", callback_data="edit_field|date")],
        [InlineKeyboardButton("↩️ Back", callback_data="reg_back_to_confirm")]
    ])


# ---------------- HELPERS (safe send / text) ----------------
async def safe_send_text(chat_send, text: str, parse_mode=ParseMode.MARKDOWN, **kwargs):
    """
    Try sending with parse_mode; on failure, send a stripped fallback.
    chat_send is a function like update.message.reply_text or context.bot.send_message (callable).
    """
    try:
        return await chat_send(text, parse_mode=parse_mode, **kwargs)
    except Exception:
        try:
            safe = html.unescape(re.sub(r"<[^>]+>", "", text))
            return await chat_send(safe, **kwargs)
        except Exception as e:
            logger.exception("Failed to send message: %s", e)
            return None


# safe text for PDF (replace non-latin1 -> '?')
def safe_text(s):
    if s is None:
        return ""
    return str(s).encode("latin-1", "replace").decode("latin-1")


# ---------------- CAPTCHA & flow helpers ----------------
def generate_captcha():
    ops = ['+', '-', '*', '/']
    op = random.choice(ops)
    if op == '+':
        a = random.randint(2, 50); b = random.randint(2, 50); q = f"{a} + {b} = ?"; ans = str(a + b)
    elif op == '-':
        a = random.randint(5, 80); b = random.randint(1, min(40, a - 1)); q = f"{a} - {b} = ?"; ans = str(a - b)
    elif op == '*':
        a = random.randint(2, 12); b = random.randint(2, 12); q = f"{a} × {b} = ?"; ans = str(a * b)
    else:
        b = random.randint(2, 12); c = random.randint(2, 12); a = b * c; q = f"{a} ÷ {b} = ?"; ans = str(c)
    return q, ans


def start_captcha_for_user(context_user_data: Dict[str, Any]):
    q, ans = generate_captcha()
    keys_to_keep = {}
    if "lang" in context_user_data:
        keys_to_keep["lang"] = context_user_data["lang"]
    context_user_data.clear()
    context_user_data.update(keys_to_keep)
    context_user_data["awaiting_captcha"] = True
    context_user_data["captcha_question"] = q
    context_user_data["captcha_answer"] = ans
    context_user_data["captcha_attempts"] = 3
    context_user_data["verified"] = False
    return q


def clear_flow_keep_verified(user_data: Dict[str, Any]) -> None:
    verified = user_data.get("verified")
    lang = user_data.get("lang")
    user_data.clear()
    if verified:
        user_data["verified"] = True
    if lang:
        user_data["lang"] = lang


# ---------------- TRAININGS & QUESTION BANK (fa + en) ----------------
TRAININGS = ["ISO 9001", "ISO 22000", "HACCP", "GMP", "FSSC 22000", "HALAL"]

QUESTION_BANK = {
    "ISO 9001": {
        "fa": [
            {"q": "هدف اصلی ISO 9001 چیست؟",
             "opts": ["افزایش درآمد", "مدیریت کیفیت سازمان", "جذب مشتری خارجی", "تبلیغات محصولات"],
             "a_idx": 1},
            {"q": "ISO 9001 بیشتر به چه چیزی توجه دارد؟",
             "opts": ["مصئونیت غذا", "رضایت مشتری", "محیط زیست", "ترافیک سازمان"],
             "a_idx": 1},
            {"q": "ISO 9001 برای چه نوع سازمانی کاربرد دارد؟",
             "opts": ["فقط کارخانه‌ها", "فقط رستوران‌ها", "هر نوع سازمان", "فقط شفاخانه ها"],
             "a_idx": 2},
            {"q": "یکی از مزایای ISO 9001 چیست؟",
             "opts": ["کاهش هزینه‌ها", "افزایش خطاها", "کاهش رضایت مشتری", "توقف تولید"],
             "a_idx": 0},
            {"q": "ISO 9001 به چه چیزی نیاز دارد؟",
             "opts": ["تفتیش داخلی", "تبلیغات اینترنتی", "سفر خارجی", "استخدام کارکنان جدید"],
             "a_idx": 0},
            {"q": "ISO 9001 به کدام روند اهمیت می‌دهد؟",
             "opts": ["پروسه ها", "محصولات جدید", "سرمایه‌گذاری", "انبارداری"],
             "a_idx": 0},
            {"q": "ISO 9001 چه نوع استانداردی است؟",
             "opts": ["مصئونیت غذا", "مدیریت کیفیت", "محیط زیست", "انرژی"],
             "a_idx": 1},
            {"q": "یکی از فعالیت‌های اصلی ISO 9001 چیست؟",
             "opts": ["تحلیل داده‌ها", "طراحی لوگو", "تبلیغ محصولات", "استخدام کارکنان"],
             "a_idx": 0},
            {"q": "ISO 9001 به چه چیزی کمک می‌کند؟",
             "opts": ["بهبود مستمر", "کاهش آموزش", "توقف تولید", "افزایش خطا"],
             "a_idx": 0},
            {"q": "ISO 9001 چه چیزی را مستند می‌کند؟",
             "opts": ["پروسه ها و پروسیجر ها", "تبلیغات", "طراحی محصول", "فروش خارجی"],
             "a_idx": 0},
        ],
        "en": [
            {"q": "What is the main purpose of ISO 9001?",
             "opts": ["Increase revenue", "Quality management of the organization", "Attract foreign customers", "Product advertisement"],
             "a_idx": 1},
            {"q": "What does ISO 9001 mainly focus on?",
             "opts": ["Food safety", "Customer satisfaction", "Environment", "Organization traffic"],
             "a_idx": 1},
            {"q": "ISO 9001 is applicable to which type of organization?",
             "opts": ["Only factories", "Only restaurants", "Any type of organization", "Only hospitals"],
             "a_idx": 2},
            {"q": "One of the benefits of ISO 9001 is:",
             "opts": ["Reducing costs", "Increasing errors", "Decreasing customer satisfaction", "Stopping production"],
             "a_idx": 0},
            {"q": "What does ISO 9001 require?",
             "opts": ["Internal audit", "Online advertisement", "Foreign travel", "Hiring new staff"],
             "a_idx": 0},
            {"q": "ISO 9001 gives importance to which aspect?",
             "opts": ["Processes", "New products", "Investment", "Warehousing"],
             "a_idx": 0},
            {"q": "ISO 9001 is what type of standard?",
             "opts": ["Food safety", "Quality management", "Environment", "Energy"],
             "a_idx": 1},
            {"q": "One of the main activities in ISO 9001 is:",
             "opts": ["Data analysis", "Logo design", "Product advertisement", "Hiring staff"],
             "a_idx": 0},
            {"q": "ISO 9001 helps in:",
             "opts": ["Continuous improvement", "Reducing training", "Stopping production", "Increasing errors"],
             "a_idx": 0},
            {"q": "What does ISO 9001 document?",
             "opts": ["Processes and procedures", "Advertisement", "Product design", "Foreign sales"],
             "a_idx": 0},
        ]
    },
    "ISO 22000": {
        "fa": [
            {"q": "هدف اصلی ISO 22000 چیست؟",
             "opts": ["افزایش فروش", "مدیریت مصئونیت غذا", "تبلیغات محصولات", "استخدام کارکنان جدید"],
             "a_idx": 1},
            {"q": "ISO 22000 برای چه چیزی اهمیت دارد؟",
             "opts": ["کیفیت محصول", "مصئونیت غذا", "محیط زیست", "سرمایه گذاری"],
             "a_idx": 1},
            {"q": "یکی از فعالیت‌های اصلی ISO 22000 چیست؟",
             "opts": ["شناسایی مخاطرات غذایی", "تبلیغات آنلاین", "طراحی بسته‌بندی", "استخدام کارشناس فروش"],
             "a_idx": 0},
            {"q": "ISO 22000 به چه چیزی نیاز دارد؟",
             "opts": ["تفتیش داخلی", "افزایش سرمایه", "تبلیغات", "سفر خارجی"],
             "a_idx": 0},
            {"q": "ISO 22000 برای چه نوع سازمانی مناسب است؟",
             "opts": ["فقط رستوران‌ها", "تمام سازمان‌های غذایی", "فقط کارخانه‌ها", "فقط فروشگاه‌ها"],
             "a_idx": 1},
            {"q": "ISO 22000 چه چیزی را مستند می‌کند؟",
             "opts": ["فرآیندهای مصئونیت غذا", "تبلیغات محصولات", "میزان فروش", "استخدام کارکنان"],
             "a_idx": 0},
            {"q": "ISO 22000 چه نوع استانداردی است؟",
             "opts": ["کیفیت محیط", "مصئونیت غذا", "مدیریت انرژی", "مالی"],
             "a_idx": 1},
            {"q": "ISO 22000 به چه چیزی کمک می‌کند؟",
             "opts": ["کاهش خطرات غذایی", "افزایش تبلیغات", "افزایش هزینه‌ها", "توقف تولید"],
             "a_idx": 0},
            {"q": "ISO 22000 به چه چیزی اهمیت می‌دهد؟",
             "opts": ["کنترل مخاطرات غذایی", "طراحی لوگو", "سرمایه گذاری خارجی", "استخدام فروشنده"],
             "a_idx": 0},
            {"q": "یکی از نتایج اجرای ISO 22000 چیست؟",
             "opts": ["مصئونیت و سلامت غذا", "افزایش تبلیغات", "کاهش مشتری", "توقف تولید"],
             "a_idx": 0},
        ],
        "en": [
            {"q": "What is the main purpose of ISO 22000?",
             "opts": ["Increase sales", "Food safety management", "Product advertisement", "Hiring new staff"],
             "a_idx": 1},
            {"q": "ISO 22000 focuses on which aspect?",
             "opts": ["Product quality", "Food safety", "Environment", "Investment"],
             "a_idx": 1},
            {"q": "One of the main activities in ISO 22000 is:",
             "opts": ["Identifying food hazards", "Online advertisement", "Packaging design", "Hiring sales expert"],
             "a_idx": 0},
            {"q": "ISO 22000 requires:",
             "opts": ["Internal audit", "Increasing investment", "Advertisement", "Foreign travel"],
             "a_idx": 0},
            {"q": "ISO 22000 is suitable for which organization?",
             "opts": ["Only restaurants", "All food organizations", "Only factories", "Only shops"],
             "a_idx": 1},
            {"q": "What does ISO 22000 document?",
             "opts": ["Food safety processes", "Product advertisement", "Sales figures", "Staff recruitment"],
             "a_idx": 0},
            {"q": "ISO 22000 is what type of standard?",
             "opts": ["Environmental quality", "Food safety", "Energy management", "Financial"],
             "a_idx": 1},
            {"q": "ISO 22000 helps to:",
             "opts": ["Reduce food hazards", "Increase advertisement", "Increase costs", "Stop production"],
             "a_idx": 0},
            {"q": "ISO 22000 gives importance to:",
             "opts": ["Controlling food hazards", "Logo design", "Foreign investment", "Hiring salesperson"],
             "a_idx": 0},
            {"q": "One result of implementing ISO 22000 is:",
             "opts": ["Food safety and health", "Increase advertisement", "Reduce customers", "Stop production"],
             "a_idx": 0},
        ]
    },
    "HACCP": {
        "fa": [
            {"q": "هدف اصلی HACCP چیست؟",
             "opts": ["افزایش فروش", "شناسایی و کنترل مخاطرات غذایی", "طراحی بسته‌بندی", "تبلیغات محصولات"],
             "a_idx": 1},
            {"q": "HACCP بیشتر روی چه چیزی تمرکز دارد؟",
             "opts": ["قیمت محصول", "مصئونیت غذا", "تبلیغات", "فروشگاه"],
             "a_idx": 1},
            {"q": "یکی از مراحل HACCP چیست؟",
             "opts": ["شناسایی مخاطرات", "طراحی لوگو", "استخدام کارکنان جدید", "تبلیغات آنلاین"],
             "a_idx": 0},
            {"q": "HACCP به چه چیزی نیاز دارد؟",
             "opts": ["کنترل نقاط بحرانی", "افزایش سرمایه", "تبلیغات", "طراحی محصول"],
             "a_idx": 0},
            {"q": "HACCP برای چه نوع سازمانی مناسب است؟",
             "opts": ["همه سازمان‌های غذایی", "فقط رستوران‌ها", "فقط فروشگاه‌ها", "فقط کارخانه‌ها"],
             "a_idx": 0},
            {"q": "HACCP چه چیزی را مستند می‌کند؟",
             "opts": ["مخاطرات و نقاط کنترل بحرانی", "تبلیغات", "میزان فروش", "طراحی لوگو"],
             "a_idx": 0},
            {"q": "HACCP چه نوع استانداردی است؟",
             "opts": ["مصئونیت غذا", "کیفیت محیط", "انرژی", "مالی"],
             "a_idx": 0},
            {"q": "HACCP به چه چیزی کمک می‌کند؟",
             "opts": ["کاهش مخاطرات غذایی", "افزایش تبلیغات", "کاهش مشتریان", "توقف تولید"],
             "a_idx": 0},
            {"q": "یکی از نتایج اجرای HACCP چیست؟",
             "opts": ["سلامت و مصئونیت غذا", "افزایش تبلیغات", "کاهش کیفیت", "توقف تولید"],
             "a_idx": 0},
            {"q": "HACCP چه چیزی را کنترل می‌کند؟",
             "opts": ["مخاطرات غذایی در نقاط بحرانی", "تبلیغات", "فروش محصولات", "طراحی محصول"],
             "a_idx": 0},
        ],
        "en": [
            {"q": "What is the main purpose of HACCP?",
             "opts": ["Increase sales", "Identify and control food hazards", "Packaging design", "Product advertisement"],
             "a_idx": 1},
            {"q": "HACCP mainly focuses on:",
             "opts": ["Product price", "Food safety", "Advertisement", "Store"],
             "a_idx": 1},
            {"q": "One of the steps in HACCP is:",
             "opts": ["Hazard identification", "Logo design", "Hiring new staff", "Online advertisement"],
             "a_idx": 0},
            {"q": "HACCP requires:",
             "opts": ["Critical control points", "Increasing investment", "Advertisement", "Product design"],
             "a_idx": 0},
            {"q": "HACCP is suitable for:",
             "opts": ["All food organizations", "Only restaurants", "Only shops", "Only factories"],
             "a_idx": 0},
            {"q": "HACCP documents:",
             "opts": ["Hazards and critical control points", "Advertisement", "Sales figures", "Logo design"],
             "a_idx": 0},
            {"q": "HACCP is what type of standard?",
             "opts": ["Food safety", "Environmental quality", "Energy", "Financial"],
             "a_idx": 0},
            {"q": "HACCP helps to:",
             "opts": ["Reduce food hazards", "Increase advertisement", "Reduce customers", "Stop production"],
             "a_idx": 0},
            {"q": "One result of implementing HACCP is:",
             "opts": ["Food safety and health", "Increase advertisement", "Reduce quality", "Stop production"],
             "a_idx": 0},
            {"q": "HACCP controls:",
             "opts": ["Food hazards at critical points", "Advertisement", "Product sales", "Product design"],
             "a_idx": 0},
        ]
    },
    "GMP": {
        "fa": [
            {"q": "هدف اصلی GMP چیست؟",
             "opts": ["افزایش فروش", "تضمین تولید بهداشتی و ایمن", "طراحی بسته‌بندی", "تبلیغات محصولات"],
             "a_idx": 1},
            {"q": "GMP بیشتر به چه چیزی توجه دارد؟",
             "opts": ["مصئونیت و بهداشت تولید", "قیمت محصول", "تبلیغات", "فروشگاه"],
             "a_idx": 0},
            {"q": "یکی از اصول GMP چیست؟",
             "opts": ["تمیز نگه داشتن محیط تولید", "افزایش فروش آنلاین", "طراحی لوگو", "استخدام کارکنان جدید"],
             "a_idx": 0},
            {"q": "GMP به چه چیزی نیاز دارد؟",
             "opts": ["رعایت بهداشت کارکنان و محیط", "افزایش تبلیغات", "کاهش تولید", "سرمایه گذاری"],
             "a_idx": 0},
            {"q": "GMP برای چه نوع سازمانی مناسب است؟",
             "opts": ["کارخانه‌ها و تولیدکنندگان غذایی", "فقط رستوران‌ها", "فروشگاه‌ها", "سازمان‌های خدماتی"],
             "a_idx": 0},
            {"q": "GMP چه چیزی را مستند می‌کند؟",
             "opts": ["فرآیندهای تولید و بهداشت", "تبلیغات محصولات", "میزان فروش", "طراحی لوگو"],
             "a_idx": 0},
            {"q": "اجرای GMP چه چیزی را تضمین می‌کند؟",
             "opts": ["تولید ایمن و بهداشتی", "افزایش تبلیغات", "کاهش مشتری", "توقف تولید"],
             "a_idx": 0},
            {"q": "GMP به چه چیزی کمک می‌کند؟",
             "opts": ["جلوگیری از آلودگی محصولات", "افزایش تبلیغات", "کاهش کیفیت", "توقف تولید"],
             "a_idx": 0},
            {"q": "یکی از نتایج اجرای GMP چیست؟",
             "opts": ["تولید محصول سالم و ایمن", "افزایش تبلیغات", "کاهش کیفیت", "توقف تولید"],
             "a_idx": 0},
            {"q": "GMP بر چه چیزی تمرکز دارد؟",
             "opts": ["بهداشت محیط، تجهیزات و کارکنان", "تبلیغات و فروش", "سرمایه گذاری", "طراحی لوگو"],
             "a_idx": 0},
        ],
        "en": [
            {"q": "What is the main purpose of GMP?",
             "opts": ["Increase sales", "Ensure hygienic and safe production", "Packaging design", "Product advertisement"],
             "a_idx": 1},
            {"q": "GMP mainly focuses on:",
             "opts": ["Production hygiene and safety", "Product price", "Advertisement", "Store"],
             "a_idx": 0},
            {"q": "One of the GMP principles is:",
             "opts": ["Keeping production environment clean", "Increasing online sales", "Logo design", "Hiring new staff"],
             "a_idx": 0},
            {"q": "GMP requires:",
             "opts": ["Maintaining hygiene of staff and environment", "Increase advertisement", "Reduce production", "Investment"],
             "a_idx": 0},
            {"q": "GMP is suitable for:",
             "opts": ["Factories and food manufacturers", "Only restaurants", "Shops", "Service organizations"],
             "a_idx": 0},
            {"q": "GMP documents:",
             "opts": ["Production and hygiene processes", "Product advertisement", "Sales figures", "Logo design"],
             "a_idx": 0},
            {"q": "Implementing GMP ensures:",
             "opts": ["Safe and hygienic production", "Increase advertisement", "Reduce customers", "Stop production"],
             "a_idx": 0},
            {"q": "GMP helps to:",
             "opts": ["Prevent product contamination", "Increase advertisement", "Reduce quality", "Stop production"],
             "a_idx": 0},
            {"q": "One result of implementing GMP is:",
             "opts": ["Producing safe and healthy products", "Increase advertisement", "Reduce quality", "Stop production"],
             "a_idx": 0},
            {"q": "GMP focuses on:",
             "opts": ["Hygiene of environment, equipment, and staff", "Advertisement and sales", "Investment", "Logo design"],
             "a_idx": 0},
        ]
    },
    "FSSC 22000": {
        "fa": [
            {"q": "هدف اصلی FSSC 22000 چیست؟",
             "opts": ["افزایش فروش", "اطمینان از مصئونیت غذا و سلامت مصرف‌کننده", "تبلیغات محصولات", "طراحی بسته‌بندی"],
             "a_idx": 1},
            {"q": "FSSC 22000 بیشتر به چه چیزی توجه دارد؟",
             "opts": ["مصئونیت غذا", "قیمت محصول", "تبلیغات", "فروشگاه"],
             "a_idx": 0},
            {"q": "یکی از ویژگی‌های FSSC 22000 چیست؟",
             "opts": ["ترکیب ISO 22000 با برنامه‌های پیش‌نیاز (PRPs)", "طراحی لوگو", "استخدام فروشنده", "تبلیغات آنلاین"],
             "a_idx": 0},
            {"q": "FSSC 22000 به چه چیزی نیاز دارد؟",
             "opts": ["تفتیش و ارزیابی ریسک", "افزایش تبلیغات", "کاهش تولید", "سرمایه گذاری"],
             "a_idx": 0},
            {"q": "FSSC 22000 برای چه نوع سازمانی مناسب است؟",
             "opts": ["تمام سازمان‌های غذایی", "فقط رستوران‌ها", "فروشگاه‌ها", "کارخانه‌های تولید غیر غذایی"],
             "a_idx": 0},
            {"q": "FSSC 22000 چه چیزی را مستند می‌کند؟",
             "opts": ["سیستم مدیریت مصئونیت غذا و رویه‌های مربوط", "تبلیغات محصولات", "میزان فروش", "طراحی لوگو"],
             "a_idx": 0},
            {"q": "اجرای FSSC 22000 چه چیزی را تضمین می‌کند؟",
             "opts": ["مصئونیت و کیفیت غذا", "افزایش تبلیغات", "کاهش مشتری", "توقف تولید"],
             "a_idx": 0},
            {"q": "FSSC 22000 به چه چیزی کمک می‌کند؟",
             "opts": ["کاهش خطرات غذایی و حفظ سلامت مصرف‌کننده", "افزایش تبلیغات", "کاهش کیفیت", "توقف تولید"],
             "a_idx": 0},
            {"q": "یکی از نتایج اجرای FSSC 22000 چیست؟",
             "opts": ["افزایش اعتماد مشتریان به محصولات غذایی", "افزایش تبلیغات", "کاهش کیفیت", "توقف تولید"],
             "a_idx": 0},
            {"q": "FSSC 22000 بر چه چیزی تمرکز دارد؟",
             "opts": ["سیستم مدیریت مصئونیت غذا، کنترل خطرات و برنامه‌های پیش‌نیاز", "تبلیغات و فروش", "سرمایه گذاری", "طراحی لوگو"],
             "a_idx": 0},
        ],
        "en": [
            {"q": "What is the main purpose of FSSC 22000?",
             "opts": ["Increase sales", "Ensure food safety and consumer health", "Product advertisement", "Packaging design"],
             "a_idx": 1},
            {"q": "FSSC 22000 mainly focuses on:",
             "opts": ["Food safety", "Product price", "Advertisement", "Store"],
             "a_idx": 0},
            {"q": "One feature of FSSC 22000 is:",
             "opts": ["Combining ISO 22000 with prerequisite programs (PRPs)", "Logo design", "Hiring sales staff", "Online advertisement"],
             "a_idx": 0},
            {"q": "FSSC 22000 requires:",
             "opts": ["Audits and risk assessment", "Increase advertisement", "Reduce production", "Investment"],
             "a_idx": 0},
            {"q": "FSSC 22000 is suitable for:",
             "opts": ["All food organizations", "Only restaurants", "Shops", "Non-food factories"],
             "a_idx": 0},
            {"q": "FSSC 22000 documents:",
             "opts": ["Food safety management system and related procedures", "Product advertisement", "Sales figures", "Logo design"],
             "a_idx": 0},
            {"q": "Implementing FSSC 22000 ensures:",
             "opts": ["Food safety and quality", "Increase advertisement", "Reduce customers", "Stop production"],
             "a_idx": 0},
            {"q": "FSSC 22000 helps to:",
             "opts": ["Reduce food hazards and protect consumer health", "Increase advertisement", "Reduce quality", "Stop production"],
             "a_idx": 0},
            {"q": "One result of implementing FSSC 22000 is:",
             "opts": ["Increased customer trust in food products", "Increase advertisement", "Reduce quality", "Stop production"],
             "a_idx": 0},
            {"q": "FSSC 22000 focuses on:",
             "opts": ["Food safety management system, hazard control, and prerequisite programs", "Advertisement and sales", "Investment", "Logo design"],
             "a_idx": 0},
        ]
    },
    "HALAL": {
        "fa": [
            {"q": "HALAL مربوط به چیست؟",
             "opts": ["مصئونیت غذا", "اعتقادات و مطابقت غذایی", "تبلیغات", "فروش"],
             "a_idx": 1},
        ],
        "en": [
            {"q": "HALAL related to:",
             "opts": ["Food safety", "Religious compliance and food suitability", "Advertisement", "Sales"],
             "a_idx": 1},
        ]
    }
}

# Ensure we didn't accidentally remove other banks — if they are missing, fallback to English empty lists
for t in TRAININGS:
    QUESTION_BANK.setdefault(t, {"fa": [], "en": []})


# ---------------- Validation helpers ----------------
def is_ascii_name_anycase(s: str) -> bool:
    """
    Accept ASCII letters (A-Z, a-z), spaces, hyphen and apostrophe.
    Accepts any case (we'll convert to UPPER when storing).
    Must contain at least one letter.
    """
    if not s:
        return False
    s = s.strip()
    try:
        s.encode("ascii")
    except Exception:
        return False
    return bool(re.match(r"^[A-Za-z]+(?:[ \-'][A-Za-z]+)*$", s))


def is_ascii_generic_anycase(s: str) -> bool:
    """
    Allow ASCII characters (printable), require at least one ASCII letter.
    Accepts any case (we'll convert to UPPER when storing).
    """
    if not s:
        return False
    s = s.strip()
    try:
        s.encode("ascii")
    except Exception:
        return False
    return bool(re.search(r"[A-Za-z]", s))


# ---------------- HANDLERS ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("verified", False)
    if not context.user_data.get("verified", False):
        q = start_captcha_for_user(context.user_data)
        msg = f"ابتدا باید کپچا را حل کنید:\n{q}\n\nPlease solve captcha to continue."
        await safe_send_text(update.message.reply_text, msg)
        return

    if "lang" not in context.user_data:
        context.user_data["awaiting_language"] = True
        await safe_send_text(update.message.reply_text, "لطفاً زبان را انتخاب کنید:\nPlease select your language:", reply_markup=kb_lang())
        return

    lang = context.user_data.get("lang", "fa")
    main_text = "✨ منوی اصلی TAJ ✨" if lang == "fa" else "✨ TAJ Main Menu ✨"
    await safe_send_text(update.message.reply_text, main_text, reply_markup=kb_main(lang))


async def message_handler_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.message.from_user.id)
    text = (update.message.text or "").strip()
    lang = context.user_data.get("lang", "fa")

    # CAPTCHA handling
    if context.user_data.get("awaiting_captcha"):
        answer_expected = context.user_data.get("captcha_answer", "")
        attempts = context.user_data.get("captcha_attempts", 0)
        if text == answer_expected:
            context.user_data["verified"] = True
            context.user_data.pop("awaiting_captcha", None)
            context.user_data.pop("captcha_question", None)
            context.user_data.pop("captcha_answer", None)
            context.user_data.pop("captcha_attempts", None)
            context.user_data["awaiting_language"] = True
            await safe_send_text(update.message.reply_text, "کپچا با موفقیت حل شد ✅\nلطفاً زبان را انتخاب کنید:\nPlease select your language:", reply_markup=kb_lang())
            return
        else:
            attempts -= 1
            context.user_data["captcha_attempts"] = attempts
            if attempts <= 0:
                await safe_send_text(update.message.reply_text, "❌ تلاش‌های کپچا به پایان رسید. لطفاً دوباره /start را وارد کنید." if lang == "fa" else "❌ Captcha attempts exhausted. Please /start again.")
                clear_flow_keep_verified(context.user_data)
                return
            else:
                await safe_send_text(update.message.reply_text, f"❌ پاسخ اشتباه. تلاش‌های باقیمانده: {attempts}\n{context.user_data.get('captcha_question')}" if lang == "fa" else f"❌ Wrong answer. Attempts left: {attempts}\n{context.user_data.get('captcha_question')}")
                return

    # LANGUAGE typed fallback
    if context.user_data.get("awaiting_language"):
        lower = text.lower()
        if lower in ("fa", "فارسی", "persian", "farsi"):
            lang_choice = "fa"
        elif lower in ("en", "english"):
            lang_choice = "en"
        else:
            await safe_send_text(update.message.reply_text, "لطفاً از دکمه‌ها برای انتخاب زبان استفاده کنید." if lang == "fa" else "Please use the buttons to choose your language.", reply_markup=kb_lang())
            return

        context.user_data["lang"] = lang_choice
        context.user_data.pop("awaiting_language", None)
        main_text = "✨ منوی اصلی TAJ ✨" if lang_choice == "fa" else "✨ TAJ Main Menu ✨"
        await safe_send_text(update.message.reply_text, main_text, reply_markup=kb_main(lang_choice))
        return

    # Awaiting serial / cert flows kept as-is...
    if context.user_data.get("awaiting_cert"):
        context.user_data["awaiting_cert"] = False
        cert_num = text
        db = load_db()
        for uid_db, u in db["users"].items():
            cert = u.get("certificate")
            if cert and cert.get("number") == cert_num:
                info = u.get("info", {})
                training = cert.get("training", "")
                issued = cert.get("issued_at", "")
                reply = (f"Name: {info.get('first_name','')} {info.get('last_name','')}\n"
                         f"Company Name: {info.get('company','')}\n"
                         f"Training Title: {training}\n"
                         f"Training Certificate: {cert_num}\n"
                         f"Issue Date: {issued}")
                await safe_send_text(update.message.reply_text, reply)
                return
        await safe_send_text(update.message.reply_text, "سِرتیفیکت یافت نشد." if lang == "fa" else "Certificate not found.")
        return

    if context.user_data.get("awaiting_serial"):
        context.user_data["awaiting_serial"] = False
        serial = text.strip()
        db = load_db()
        user = db["users"].get(uid, {})
        training = user.get("training")
        valid_list = VALID_SERIALS.get(training, [])
        if serial in valid_list:
            db["users"].setdefault(uid, {})
            db["users"][uid]["serial_used"] = serial
            save_db(db)
            await safe_send_text(update.message.reply_text, "✅ سریال معتبر است. تست نهایی آغاز می‌شود." if lang == "fa" else "✅ Serial valid. Final test will start now.")
            await start_post_test_for_user(uid, context)
        else:
            await safe_send_text(update.message.reply_text, "❌ سریال نامعتبر است. بدون سریال معتبر نمی‌توانید تست نهایی را انجام دهید." if lang == "fa" else "❌ Invalid serial. You cannot take the Final test without a valid serial.", reply_markup=kb_post_require_serial(lang))
        return

    # REGISTRATION: editing a single field (after pressing Edit)
    if context.user_data.get("reg_edit_field"):
        field = context.user_data.get("reg_edit_field")
        lang = context.user_data.get("lang", "fa")

        # Validate ASCII (any case) and auto-convert to UPPER for storage
        if field in ("first_name", "last_name"):
            if not is_ascii_name_anycase(text):
                msg = ("لطفاً فقط حروف انگلیسی وارد کنید (مثال: ALI)\nOnly English letters allowed (e.g. ALI)." if lang == "fa"
                       else "Please type only English letters (e.g. ALI).")
                await safe_send_text(update.message.reply_text, msg)
                return
            value = text.strip().upper()
        elif field in ("job_title", "company"):
            if not is_ascii_generic_anycase(text):
                msg = ("لطفاً فقط متن انگلیسی وارد کنید." if lang == "fa"
                       else "Please type only English text for this field.")
                await safe_send_text(update.message.reply_text, msg)
                return
            value = text.strip().upper()
        else:
            # date field (no uppercase constraint)
            value = text.strip()

        context.user_data.setdefault("reg_data", {})[field] = value
        context.user_data.pop("reg_edit_field", None)
        # show updated confirmation
        await show_registration_confirmation(update.message, context)
        return

    # REGISTRATION: normal step-by-step entry
    if "reg_step" in context.user_data:
        step = context.user_data.get("reg_step", 0)
        fields = ["first_name", "last_name", "job_title", "company", "date"]
        prompts_fa = [
            "لطفاً نام خود را وارد کنید:",
            "لطفاً تخلص خود را وارد کنید:",
            "لطفاً عنوان وظیفه را وارد کنید:",
            "لطفاً نام شرکت را وارد کنید:",
            "لطفاً تاریخ را وارد کنید (مثلاً 01-01-2024):"
        ]
        prompts_en = [
            "Please enter your First name:",
            "Please enter your Last name:",
            "Please enter your Job title:",
            "Please enter your Company name:",
            "Please enter Date (YYYY-MM-DD):"
        ]

        lang = context.user_data.get("lang", "fa")
        current_field = fields[step]

        # The flow here assumes the bot already prompted and now receives the answer for fields[step].
        value_text = text

        # Apply validation according to field (accept ASCII in any case; then uppercase)
        valid = True
        reason_msg = None
        if current_field in ("first_name", "last_name"):
            if not is_ascii_name_anycase(value_text):
                valid = False
                reason_msg = ("نام باید فقط شامل حروف انگلیسی باشد، مثال: ALI" if lang == "fa"
                              else "Name must contain only English letters (e.g. ALI).")
        elif current_field in ("job_title", "company"):
            if not is_ascii_generic_anycase(value_text):
                valid = False
                reason_msg = ("این فیلد باید فقط شامل متن انگلیسی باشد." if lang == "fa"
                              else "This field must contain only English text.")
        else:
            # date: basic format check YYYY-MM-DD
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", value_text):
                valid = False
                reason_msg = ("فرمت تاریخ نادرست است. مثال: 2024-01-01" if lang == "fa" else "Invalid date format. Example: 2024-01-01")

        if not valid:
            # ask user to re-enter same field with instruction
            await safe_send_text(update.message.reply_text, reason_msg)
            # re-prompt same field (do not advance step)
            await safe_send_text(update.message.reply_text, prompts_fa[step] if lang == "fa" else prompts_en[step])
            return

        # if valid, store normalized value (uppercase for text fields)
        if current_field in ("first_name", "last_name", "job_title", "company"):
            store_val = value_text.strip().upper()
        else:
            store_val = value_text.strip()

        context.user_data.setdefault("reg_data", {})[current_field] = store_val
        # advance step
        step += 1
        context.user_data["reg_step"] = step

        # if more fields remain, prompt next
        if step < len(fields):
            await safe_send_text(update.message.reply_text, prompts_fa[step] if lang == "fa" else prompts_en[step])
            return
        # all fields entered -> show confirmation summary with Confirm/Edit buttons
        else:
            # do not immediately save; show confirmation message
            await show_registration_confirmation(update.message, context)
            return

    # fallback
    await safe_send_text(update.message.reply_text, "لطفاً از منوی اصلی استفاده کنید یا /start را وارد کنید." if lang == "fa" else "Please use the main menu or type /start.")


async def show_registration_confirmation(dest_message_or_query, context: ContextTypes.DEFAULT_TYPE):
    """
    Show a confirmation summary of reg_data and ask Confirm/Edit.
    Accepts: Message, CallbackQuery, or Update (which contains callback_query or message).
    """
    lang = context.user_data.get("lang", "fa")
    reg = context.user_data.get("reg_data", {})
    first = reg.get("first_name", "—")
    last = reg.get("last_name", "—")
    job = reg.get("job_title", "—")
    comp = reg.get("company", "—")
    date = reg.get("date", "—")

    if lang == "fa":
        text = (f"لطفاً اطلاعات زیر را بررسی کنید:\n\n"
                f"نام: {first}\n"
                f"تخلص: {last}\n"
                f"عنوان وظیفه: {job}\n"
                f"نام شرکت: {comp}\n"
                f"تاریخ: {date}\n\n"
                "اگر اطلاعات صحیح است ✅ 'تایید اطلاعات' را بزنید؛ در غیر این صورت ✏️ 'ویرایش اطلاعات' را انتخاب کنید.")
    else:
        text = (f"Please review your information:\n\n"
                f"First name: {first}\n"
                f"Last name: {last}\n"
                f"Job title: {job}\n"
                f"Company: {comp}\n"
                f"Date: {date}\n\n"
                "If everything is correct press ✅ 'Confirm data', otherwise press ✏️ 'Edit data'.")

    # Handle different destination types
    # If dest is Update, try to use its callback_query or message
    if isinstance(dest_message_or_query, Update):
        if dest_message_or_query.callback_query:
            cq = dest_message_or_query.callback_query
            await cq.answer()
            await cq.edit_message_text(text, reply_markup=kb_reg_confirm(lang))
            return
        elif dest_message_or_query.message:
            await safe_send_text(dest_message_or_query.message.reply_text, text, reply_markup=kb_reg_confirm(lang))
            return

    # If it's a CallbackQuery:
    if isinstance(dest_message_or_query, CallbackQuery):
        await dest_message_or_query.answer()
        await dest_message_or_query.edit_message_text(text, reply_markup=kb_reg_confirm(lang))
        return

    # If it's a Message:
    if isinstance(dest_message_or_query, Message):
        await safe_send_text(dest_message_or_query.reply_text, text, reply_markup=kb_reg_confirm(lang))
        return

    # Fallback: try attributes (old usage patterns)
    if hasattr(dest_message_or_query, "answer") and hasattr(dest_message_or_query, "edit_message_text"):
        await dest_message_or_query.answer()
        await dest_message_or_query.edit_message_text(text, reply_markup=kb_reg_confirm(lang))
    elif hasattr(dest_message_or_query, "reply_text"):
        await safe_send_text(dest_message_or_query.reply_text, text, reply_markup=kb_reg_confirm(lang))
    else:
        # last resort: log and ignore
        logger.warning("show_registration_confirmation: unknown destination type")


# ---------------- CALLBACKS ----------------
async def cb_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = query.data.split("_")[1]
    context.user_data["lang"] = lang
    context.user_data.pop("awaiting_language", None)
    await query.edit_message_text("✨ منوی اصلی TAJ ✨" if lang == "fa" else "✨ TAJ Main Menu ✨", reply_markup=kb_main(lang))


async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    lang = context.user_data.get("lang", "fa")

    if data == "menu_register":
        # start registration: pick training first (existing flow)
        await query.edit_message_text("💼 لطفاً دورهٔ مورد نظر را انتخاب کنید:" if lang == "fa" else "💼 Please select a training:", reply_markup=kb_trainings(lang))
        return
    if data == "menu_check":
        context.user_data["awaiting_cert"] = True
        await query.edit_message_text("🔎 لطفاً شمارهٔ سِرتیفیکت را ارسال کنید:" if lang == "fa" else "🔎 Please send your certificate number:")
        return
    if data == "menu_contact":
        contact = "ایمیل: info@taj-ra.com\nتلفن: +93796367367" if lang == "fa" else "Email: info@taj-ra.com\nPhone: +93796367367"
        await query.edit_message_text(contact, reply_markup=kb_main(lang))
        return
    await query.edit_message_text("...")


async def cb_training_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, idx = query.data.split("|", 1)
    idx = int(idx)
    training = TRAININGS[idx]
    context.user_data["selected_training"] = training
    # initialize registration flow
    context.user_data["reg_step"] = 0
    context.user_data["reg_data"] = {}
    lang = context.user_data.get("lang", "fa")
    # prompt for first name and include instruction about English uppercase
    prompt = ("لطفاً نام خود را وارد کنید:" if lang == "fa"
              else "Please enter your First name:")
    await query.edit_message_text((f"✅ دوره انتخاب شد: {training}\n\n{prompt}" if lang == "fa" else f"✅ Selected training: {training}\n\n{prompt}"))


# Registration confirm / edit callbacks
async def cb_reg_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "fa")
    uid = str(query.from_user.id)
    reg = context.user_data.get("reg_data", {})
    # Save to DB
    db = load_db()
    db["users"].setdefault(uid, {})
    db["users"][uid]["info"] = reg
    db["users"][uid]["training"] = context.user_data.get("selected_training")
    db["users"][uid]["pre_test"] = None
    db["users"][uid]["post_test"] = None
    db["users"][uid]["certificate"] = None
    db["users"][uid]["serial_used"] = None
    save_db(db)
    # clear registration transient data
    context.user_data.pop("reg_step", None)
    context.user_data.pop("reg_data", None)
    # notify user
    await query.edit_message_text("🎉 ثبت‌نام کامل شد! می‌توانید تست اولیه یا تست نهایی را انتخاب کنید." if lang == "fa" else "🎉 Registration complete! You may choose Pre-test or Post-test.", reply_markup=kb_after_registration(lang))


async def cb_reg_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "fa")
    # Show field selection keyboard
    await query.edit_message_text("کدام فیلد را می‌خواهید ویرایش کنید؟" if lang == "fa" else "Which field would you like to edit?", reply_markup=kb_reg_edit_fields(lang))


async def cb_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, field = query.data.split("|", 1)
    context.user_data["reg_edit_field"] = field
    lang = context.user_data.get("lang", "fa")
    # Prompt for new value with instruction
    if field == "first_name":
        prompt = ("لطفاً نام جدید را فقط با حروف بزرگ انگلیسی وارد کنید (مثال: ALI):" if lang == "fa"
                  else "Please send NEW First name using only UPPERCASE English letters (e.g. ALI):")
    elif field == "last_name":
        prompt = ("لطفاً تخلص جدید را فقط با حروف بزرگ انگلیسی وارد کنید (مثال: AHMADI):" if lang == "fa"
                  else "Please send NEW Last name using only UPPERCASE English letters (e.g. AHMADI):")
    elif field == "job_title":
        prompt = ("لطفاً عنوان وظیفه جدید را فقط با حروف بزرگ انگلیسی وارد کنید:" if lang == "fa"
                  else "Please send NEW Job title using only UPPERCASE English text:")
    elif field == "company":
        prompt = ("لطفاً نام شرکت جدید را فقط با حروف بزرگ انگلیسی وارد کنید:" if lang == "fa"
                  else "Please send NEW Company name using only UPPERCASE English text:")
    else:
        prompt = ("لطفاً تاریخ جدید را در فرمت YYYY-MM-DD وارد کنید:" if lang == "fa"
                  else "Please send NEW Date in format YYYY-MM-DD:")
    # ask user to type new value (use send_message so it appears as a new message)
    try:
        await query.edit_message_text(prompt)
    except:
        # fallback
        await safe_send_text(context.bot.send_message, prompt, chat_id=query.from_user.id)


# ---------------- QUIZ HELPERS (unchanged) ----------------
def prepare_quiz(training: str, lang: str):
    bank = QUESTION_BANK.get(training, {}).get(lang, [])
    if not bank:
        bank = QUESTION_BANK.get(training, {}).get("en", [])
    if len(bank) >= 10:
        sample = random.sample(bank, 10)
    else:
        sample = [random.choice(bank) for _ in range(10)] if bank else []
    return sample


async def cb_start_pre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    lang = context.user_data.get("lang", "fa")
    db = load_db()
    user = db["users"].get(uid)
    if not user:
        await query.edit_message_text("❗ ابتدا ثبت‌نام کنید." if lang == "fa" else "❗ You must register first.")
        return
    training = user.get("training")
    questions = prepare_quiz(training, lang)
    context.user_data["quiz"] = {"questions": questions, "index": 0, "score": 0, "type": "pre"}
    await query.edit_message_text("🚀 تست اولیه آغاز شد — به سوالات پاسخ دهید." if lang == "fa" else "🚀 Pre-test started — answer the questions.")
    await send_next_question_for_user(context, chat_id=int(uid))


async def cb_start_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    lang = context.user_data.get("lang", "fa")
    db = load_db()
    user = db["users"].get(uid)
    if not user:
        await query.edit_message_text("❗ ابتدا ثبت‌نام کنید." if lang == "fa" else "❗ You must register first.")
        return
    if user.get("pre_test") is None:
        await query.edit_message_text("⚠️ تست اولیه را تکمیل نکرده‌اید. ابتدا تست اولیه را انجام دهید." if lang == "fa" else "⚠️ You haven't completed the Pre-test. Please do Pre-test first.")
        return
    context.user_data["awaiting_serial"] = True
    await query.edit_message_text("🔐 لطفاً سریال دورهٔ آموزشی را وارد کنید تا تست نهایی فعال شود:" if lang == "fa" else "🔐 Please enter the training serial number to enable Final test:", reply_markup=kb_post_require_serial(lang))


async def start_post_test_for_user(uid: str, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang", "fa")
    db = load_db()
    user = db["users"].get(uid)
    if not user:
        return
    training = user.get("training")
    questions = prepare_quiz(training, lang)
    context.user_data["quiz"] = {"questions": questions, "index": 0, "score": 0, "type": "post"}
    try:
        await context.bot.send_message(chat_id=int(uid), text=("🚀 آزمون نهایی آغاز شد — به سوالات پاسخ دهید." if lang == "fa" else "🚀 Final test started — answer the questions."))
        await send_next_question_for_user(context, chat_id=int(uid))
    except Exception as e:
        logger.exception("Failed to start post test: %s", e)
        return


async def send_next_question_for_user(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    lang = context.user_data.get("lang", "fa")
    quiz = context.user_data.get("quiz")
    if not quiz:
        await context.bot.send_message(chat_id=chat_id, text=("هیچ آزمونی در حال اجرا نیست." if lang == "fa" else "No active quiz."))
        return
    idx = quiz["index"]
    if idx >= len(quiz["questions"]):
        await finish_quiz_and_store(chat_id, context)
        return
    q = quiz["questions"][idx]
    rows = []
    for opt_i, opt_text in enumerate(q["opts"]):
        rows.append([InlineKeyboardButton(opt_text, callback_data=f"ans|{opt_i}")])
    await context.bot.send_message(chat_id=chat_id, text=(f"❓ سوال {idx+1}:\n{q['q']}" if lang == "fa" else f"❓ Q{idx+1}:\n{q['q']}"), reply_markup=InlineKeyboardMarkup(rows))


async def cb_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "fa")

    try:
        _, opt_str = query.data.split("|", 1)
        chosen_idx = int(opt_str)
    except Exception:
        try:
            await query.edit_message_text("⚠️ خطا در دریافت پاسخ." if lang == "fa" else "⚠️ Answer data error.")
        except:
            pass
        return

    quiz = context.user_data.get("quiz")
    if not quiz:
        try:
            await query.edit_message_text("⚠️ هیچ آزمونی در حال اجرا نیست." if lang == "fa" else "⚠️ No active quiz.")
        except:
            pass
        return

    idx = quiz["index"]
    if idx >= len(quiz["questions"]):
        await context.bot.send_message(chat_id=query.from_user.id, text="✅ تمامی سوالات پاسخ داده شده‌اند." if lang == "fa" else "✅ All questions have been answered.")
        return

    current_q = quiz["questions"][idx]
    correct_idx = current_q["a_idx"]
    correct_text = current_q["opts"][correct_idx]
    chosen_text = current_q["opts"][chosen_idx] if 0 <= chosen_idx < len(current_q["opts"]) else "—"

    if chosen_idx == correct_idx:
        quiz["score"] += 5
        await context.bot.send_message(chat_id=query.from_user.id, text="✅ درست! تبریک — پاسخ صحیح ثبت شد." if lang == "fa" else "✅ Correct! Well done — answer recorded.")
    else:
        await context.bot.send_message(chat_id=query.from_user.id, text=(f"❌ غلط — جواب درست: {correct_text}" if lang == "fa" else f"❌ Incorrect — Correct answer: {correct_text}"))

    try:
        await query.delete_message()
    except:
        pass

    quiz["index"] += 1
    await send_next_question_for_user(context, chat_id=query.from_user.id)


async def finish_quiz_and_store(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    uid = str(chat_id)
    db = load_db()
    user = db["users"].get(uid)
    quiz = context.user_data.get("quiz")
    lang = context.user_data.get("lang", "fa")

    if not quiz or user is None:
        await context.bot.send_message(chat_id=chat_id, text=("خطا: اطلاعات آزمون یا کاربر موجود نیست." if lang == "fa" else "Error: quiz or user data missing."))
        context.user_data.pop("quiz", None)
        return

    score = quiz["score"]
    qtype = quiz["type"]

    if qtype == "pre":
        user["pre_test"] = score
        db["users"][uid] = user
        save_db(db)
        context.user_data.pop("quiz", None)

        if score >= PRE_PASS:
            msg = (f"🎯 پایان تست اولیه. نمره شما: {score}/50\n🎉 شما از پیش‌آزمون موفق بیرون آمده‌اید. آماده شرکت در تست نهایی باشید." if lang == "fa"
                   else f"🎯 Pre-test finished. Your score: {score}/50\n🎉 You passed the Pre-test. Get ready for the Final test.")
        else:
            msg = (f"🎯 پایان تست اولیه. نمره شما: {score}/50\n⚠️ نتیجه: قبول نشدید. لطفاً بیشتر مطالعه کنید و دوباره تلاش کنید." if lang == "fa"
                   else f"🎯 Pre-test finished. Your score: {score}/50\n⚠️ Result: Not passed. Please review the materials and try again.")

        await context.bot.send_message(
            chat_id=chat_id,
            text=msg,
            reply_markup=kb_after_registration(lang)
        )
        return

    if qtype == "post":
        user["post_test"] = score
        db["users"][uid] = user
        save_db(db)
        pre = user.get("pre_test", 0) or 0
        post = score
        total = pre + post

        if total > PASS_TOTAL:
            cert_no = str(uuid.uuid4())[:8].upper()
            issued = datetime.utcnow().strftime("%Y-%m-%d")
            user["certificate"] = {"number": cert_no, "training": user.get("training"), "issued_at": issued}
            db["users"][uid] = user
            save_db(db)

            # Build ASCII-safe (latin-1 fallback) PDF certificate
            info = user.get("info", {})
            name_safe = safe_text(info.get("first_name", "")) + " " + safe_text(info.get("last_name", ""))
            company_safe = safe_text(info.get("company", ""))
            training_safe = safe_text(user.get("training", ""))
            cert_no_safe = safe_text(cert_no)
            issued_safe = safe_text(issued)
            pre_safe = safe_text(str(pre))
            post_safe = safe_text(str(post))
            total_safe = safe_text(str(total))

            pdf_path = os.path.join(CERTS_DIR, f"cert_{uid}_{cert_no}.pdf")
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Arial", size=16)
            pdf.cell(0, 10, "TAJ Research & Audit Services Company", ln=True, align="C")
            pdf.ln(6)
            pdf.set_font("Arial", size=14)
            pdf.cell(0, 10, "Certificate of Training", ln=True, align="C")
            pdf.ln(10)
            pdf.set_font("Arial", size=12)
            pdf.multi_cell(0, 8, (
                f"Name: {name_safe}\n"
                f"Company: {company_safe}\n"
                f"Training: {training_safe}\n"
                f"Certificate Number: {cert_no_safe}\n"
                f"Issued at: {issued_safe}\n\n"
                f"Pre-test: {pre_safe}/50\nPost-test: {post_safe}/50\nTotal: {total_safe}/100"
            ))
            pdf.output(pdf_path)

            # send pdf
            try:
                with open(pdf_path, "rb") as fh:
                    await context.bot.send_document(chat_id=chat_id, document=fh)
            except Exception as e:
                logger.exception("Failed to send certificate PDF: %s", e)
                await context.bot.send_message(chat_id=chat_id, text=("⚠️ صدور یا ارسال سِرتیفیکت با خطا مواجه شد." if lang == "fa" else "⚠️ Certificate generation/sending failed."), reply_markup=kb_main(lang))
                context.user_data.pop("quiz", None)
                return

            await context.bot.send_message(chat_id=chat_id, text=("🎉 تبریک! سِرتیفیکت شما صادر شد." if lang == "fa" else "🎉 Congratulations! Your certificate has been issued."), reply_markup=kb_main(lang))
        else:
            fail_msg = (f"🔔 مجموع نمرات شما: {total}/100.\n❌ نتیجه: قبول نشدید. حد نصاب برای صدور سِرتیفیکت > {PASS_TOTAL} است. لطفاً دوباره تلاش کنید." if lang == "fa"
                        else f"🔔 Your total score: {total}/100.\n❌ Result: Not passed. Pass mark is > {PASS_TOTAL}. Please try again.")
            await context.bot.send_message(chat_id=chat_id, text=fail_msg, reply_markup=kb_main(lang))

        context.user_data.pop("quiz", None)
        return


# ---------------- MISC ROUTER ----------------
async def cb_misc_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    lang = context.user_data.get("lang", "fa")
    if data == "back_main":
        await query.edit_message_text("✨ منوی اصلی TAJ ✨" if lang == "fa" else "✨ TAJ Main Menu ✨", reply_markup=kb_main(lang))
    elif data == "start_pre":
        await cb_start_pre(update, context)
    elif data == "start_post":
        await cb_start_post(update, context)
    # reg_back_to_confirm: go back to confirmation screen
    elif data == "reg_back_to_confirm":
        # pass callback_query to helper so it can edit the same message
        await show_registration_confirmation(query, context)


# ---------------- UTIL ----------------
async def cmd_myinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.message.from_user.id)
    db = load_db()
    user = db["users"].get(uid)
    lang = context.user_data.get("lang", "fa")
    if not user:
        await safe_send_text(update.message.reply_text, "اطلاعاتی ثبت نشده است. ابتدا ثبت‌نام کنید." if lang == "fa" else "No data. Please register first.")
        return
    info = user.get("info", {})
    pre = user.get("pre_test")
    post = user.get("post_test")
    cert = user.get("certificate")
    serial_used = user.get("serial_used")
    await safe_send_text(update.message.reply_text,
                         f"Name: {info.get('first_name','')}\nCompany: {info.get('company','')}\nPre: {pre}\nPost: {post}\nSerial: {serial_used}\nCertificate: {cert.get('number') if cert else '—'}"
                         )


# ---------------- MAIN ----------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cb_lang, pattern=r"^lang_"))
    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern=r"^menu_"))
    app.add_handler(CallbackQueryHandler(cb_training_select, pattern=r"^train\|"))
    app.add_handler(CallbackQueryHandler(cb_misc_router, pattern=r"^(back_main|start_pre|start_post|reg_back_to_confirm)$"))
    app.add_handler(CallbackQueryHandler(cb_answer_handler, pattern=r"^ans\|"))
    # new registration callbacks
    app.add_handler(CallbackQueryHandler(cb_reg_confirm, pattern=r"^reg_confirm$"))
    app.add_handler(CallbackQueryHandler(cb_reg_edit, pattern=r"^reg_edit$"))
    app.add_handler(CallbackQueryHandler(cb_edit_field, pattern=r"^edit_field\|"))
    # messages handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler_all))
    app.add_handler(CommandHandler("myinfo", cmd_myinfo))

    logger.info("TAJ Training Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
