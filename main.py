import logging
import asyncio
import requests
import xml.etree.ElementTree as ET
import re
import mysql.connector
from datetime import datetime
from html import escape
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

# === КОНФИГ ===
load_dotenv()

TOKEN = os.getenv("TOKEN")
SOAP_URL = os.getenv("SOAP_URL")
SOAP_USER = os.getenv("SOAP_USER")
SOAP_PASS = os.getenv("SOAP_PASS")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "acore"),
    "password": os.getenv("DB_PASSWORD", "acore"),
    "database": os.getenv("DB_DATABASE", "acore_auth")
}

# === ЛОГИ ===
logging.basicConfig(
    level=logging.INFO,
    filename="bot.log",
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)

# === FSM ===
class RegState(StatesGroup):
    credentials = State()

class PasswordChangeState(StatesGroup):
    new_password = State()

class AdminCommandState(StatesGroup):
    command = State()

class ServiceState(StatesGroup):
    character_name = State()
    service_type = State()

# === SOAP ===
def send_soap_command(command: str) -> str:
    headers = {'Content-Type': 'text/xml'}
    payload = f"""<?xml version="1.0" encoding="utf-8"?>
    <soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                   xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
      <soap:Body>
        <executeCommand xmlns="urn:AC">
          <command>{command}</command>
        </executeCommand>
      </soap:Body>
    </soap:Envelope>"""

    try:
        response = requests.post(
            SOAP_URL,
            auth=(SOAP_USER, SOAP_PASS),
            data=payload,
            headers=headers,
            timeout=5
        )

        if not response.ok:
            return f"❌ Ошибка сервера: {response.status_code} — {response.reason}"

        root = ET.fromstring(response.content)
        result_element = root.find('.//result')
        if result_element is None:
            return f"❌ Ошибка: <result> не найден."
        return result_element.text.strip() if result_element.text else ""

    except Exception as e:
        return f"❌ SOAP ошибка: {e}"

# === PARSE INFO ===
def parse_server_info(result: str) -> str:
    players = re.search(r"Connected players:\s*(\d+)", result)
    characters = re.search(r"Characters in world:\s*(\d+)", result)
    uptime = re.search(r"Server uptime:\s*(.+?)\r", result)

    players_text = f"👥 Онлайн игроков: {players.group(1)}" if players else "❓ Игроки: ?"
    chars_text = f"🌍 Персонажей в мире: {characters.group(1)}" if characters else "❓ Персонажи: ?"
    uptime_text = f"⏱ Аптайм: {uptime.group(1)}" if uptime else "❓ Аптайм: ?"

    return f"{players_text}\n{chars_text}\n{uptime_text}"

# === MYSQL ===
def is_account_exists(username: str) -> bool:
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM account WHERE username = %s", (username,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    except Exception as e:
        logging.error(f"MySQL check error: {e}")
        return False

def set_telegram_email(username: str, telegram_id: int):
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE account SET email = %s WHERE username = %s",
            (str(telegram_id), username)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"MySQL update error: {e}")

def get_username_by_telegram_id(telegram_id: int) -> str | None:
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM account WHERE email = %s", (str(telegram_id),))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        logging.error(f"MySQL lookup error: {e}")
        return None

def get_characters_by_telegram_id(telegram_id: int) -> list[tuple[str, int]]:
    """Return list of (name, level) tuples for characters owned by telegram user."""
    try:
        conn_auth = mysql.connector.connect(**DB_CONFIG)
        cursor_auth = conn_auth.cursor()
        cursor_auth.execute(
            "SELECT id FROM account WHERE email = %s",
            (str(telegram_id),),
        )
        row = cursor_auth.fetchone()
        conn_auth.close()

        if not row:
            return []

        account_id = row[0]
        char_config = DB_CONFIG.copy()
        char_config["database"] = "acore_characters"
        conn_chars = mysql.connector.connect(**char_config)
        cursor_chars = conn_chars.cursor()
        cursor_chars.execute(
            "SELECT name, level FROM characters WHERE account = %s",
            (account_id,),
        )
        chars = [(row[0], row[1]) for row in cursor_chars.fetchall()]
        conn_chars.close()

        return chars
    except Exception as e:
        logging.error(f"Ошибка при получении персонажей: {e}")
        return []

def is_character_owned_by_user(char_name: str, telegram_id: int) -> bool:
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM account WHERE email = %s", (str(telegram_id),))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return False

        account_id = row[0]
        char_config = DB_CONFIG.copy()
        char_config["database"] = "acore_characters"
        conn_chars = mysql.connector.connect(**char_config)
        cursor_chars = conn_chars.cursor()
        cursor_chars.execute("SELECT COUNT(*) FROM characters WHERE name = %s AND account = %s", (char_name, account_id))
        result = cursor_chars.fetchone()
        conn_chars.close()

        return result[0] > 0
    except Exception as e:
        logging.error(f"Ошибка при проверке владельца персонажа: {e}")
        return False

def has_gm_access(telegram_id: int, level: int = 3) -> bool:
    """Check if user has GM access level >= level in account_access table."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM account WHERE email = %s", (str(telegram_id),))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False
        account_id = row[0]
        cursor.execute("SELECT gmlevel FROM account_access WHERE id = %s", (account_id,))
        gm_row = cursor.fetchone()
        conn.close()
        return bool(gm_row and gm_row[0] >= level)
    except Exception as e:
        logging.error(f"MySQL GM level check error: {e}")
        return False

# === ХЕНДЛЕРЫ ===
router = Router()

@router.message(F.text == "🛎 Услуги")
async def handle_services(msg: Message):
    buttons = [
        [KeyboardButton(text="🔁 Смена пола"), KeyboardButton(text="🔄 Смена фракции")],
        [KeyboardButton(text="🧑‍🎨 Смена внешности"), KeyboardButton(text="📍 Телепортация")]
    ]
    kb = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    await msg.answer("Выберите услугу:", reply_markup=kb)

@router.message(F.text.in_(["🔁 Смена пола", "🔄 Смена фракции", "🧑‍🎨 Смена внешности", "📍 Телепортация"]))
async def handle_service_selection(msg: Message, state: FSMContext):
    service_map = {
        "🔁 Смена пола": "gender",
        "🔄 Смена фракции": "faction",
        "🧑‍🎨 Смена внешности": "customize",
        "📍 Телепортация": "teleport"
    }
    service = service_map[msg.text]
    await state.update_data(service=service)
    await msg.answer("Введите имя персонажа:")
    await state.set_state(ServiceState.character_name)

@router.message(ServiceState.character_name)
async def handle_apply_service(msg: Message, state: FSMContext):
    data = await state.get_data()
    char_name = msg.text.strip()
    service = data.get("service")

    if not is_character_owned_by_user(char_name, msg.from_user.id):
        await msg.answer("❌ Этот персонаж не принадлежит вашему аккаунту.")
        await state.clear()
        return

    service_map = {
        "gender": "character customize",
        "faction": "character changefaction",
        "customize": "character customize",
        "teleport": "teleport name $home"
    }

    command = service_map.get(service)
    if not command:
        await msg.answer("❌ Неизвестная услуга.")
        await state.clear()
        return

    full_command = (
        f"teleport name {char_name} $home" if service == "teleport"
        else f"{command} {char_name}"
    )

    result = send_soap_command(full_command)

    if "does not exist" in result.lower():
        await msg.answer("❌ Персонаж не найден.")
    elif "500" in result.lower():
        await msg.answer("❌ Внутренняя ошибка сервера. Попробуйте позже.")
    else:
       await msg.answer(f"✅ Услуга применена к <b>{char_name}</b>.")

    await state.clear()

@router.message(Command("start"))
async def cmd_start(msg: Message):
    telegram_id = msg.from_user.id
    is_registered = get_username_by_telegram_id(telegram_id) is not None

    if not is_registered:
        buttons = [[KeyboardButton(text="📥 Регистрация")]]
    else:
        buttons = [
            [KeyboardButton(text="🔐 Смена пароля")],
            [KeyboardButton(text="👥 Онлайн игроки")],
            [KeyboardButton(text="📜 Мои персонажи")],
            [KeyboardButton(text="🛎 Услуги"), KeyboardButton(text="🛠️ Админ панель")]
        ]

    reply_kb = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    await msg.answer("Привет! Это бот WoWSeRVeR (set realmlist wowserver.ru) выберете действие:", reply_markup=reply_kb)

@router.message(F.text == "📜 Мои персонажи")
async def handle_my_chars(msg: Message):
    chars = get_characters_by_telegram_id(msg.from_user.id)
    if not chars:
        await msg.answer("❌ У вас нет персонажей или вы не зарегистрированы.")
    else:
        lines = [f"• {name} — {level} ур." for name, level in chars]
        await msg.answer("👤 Ваши персонажи:\n" + "\n".join(lines))

@router.message(F.text == "👥 Онлайн игроки")
async def handle_online_players(msg: Message):
    result = send_soap_command("server info")
    parsed = parse_server_info(result)
    await msg.answer(parsed)

@router.message(F.text == "📥 Регистрация")
async def handle_register(msg: Message, state: FSMContext):
    await msg.answer("Введите логин и пароль через пробел:")
    await state.set_state(RegState.credentials)

@router.message(RegState.credentials)
async def process_register(msg: Message, state: FSMContext):
    parts = msg.text.strip().split()
    if len(parts) != 2:
        await msg.answer("❌ Формат: логин пароль")
        return

    login, password = parts
    telegram_id = msg.from_user.id
    existing_login = get_username_by_telegram_id(telegram_id)

    if existing_login:
        await msg.answer(f"🔐 Вы уже зарегистрированы под логином <b>{existing_login}</b>.")
    elif is_account_exists(login):
        await msg.answer("❌ Логин уже занят.")
    else:
        result = send_soap_command(f"account create {login} {password}")
        set_telegram_email(login, telegram_id)
        await msg.answer(f"✅ Аккаунт создан:\n{escape(result)}")
    await state.clear()

@router.message(F.text == "🔐 Смена пароля")
async def handle_change_pass(msg: Message, state: FSMContext):
    username = get_username_by_telegram_id(msg.from_user.id)
    if not username:
        await msg.answer("❌ Сначала зарегистрируйтесь.")
        return
    await msg.answer("Введите новый пароль:")
    await state.set_state(PasswordChangeState.new_password)

@router.message(PasswordChangeState.new_password)
async def process_change_pass(msg: Message, state: FSMContext):
    username = get_username_by_telegram_id(msg.from_user.id)
    password = msg.text.strip()
    result = send_soap_command(f"account set password {username} {password} {password}")
    if "The password was changed" in result:
        result = "✅ Пароль успешно изменён."
    await msg.answer(result)
    await state.clear()

@router.message(F.text == "🛠️ Админ панель")
async def handle_admin(msg: Message, state: FSMContext):
    if not has_gm_access(msg.from_user.id, 3):
        await msg.answer("❌ У вас нет прав.")
        return
    await msg.answer("Введите SOAP команду:")
    await state.set_state(AdminCommandState.command)

@router.message(AdminCommandState.command)
async def execute_admin_command(msg: Message, state: FSMContext):
    result = send_soap_command(msg.text.strip())
    await msg.answer(f"<pre>{escape(result)}</pre>")
    await state.clear()

# === ЗАПУСК ===
async def main():
    print("🚀 Бот запущен...")
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
