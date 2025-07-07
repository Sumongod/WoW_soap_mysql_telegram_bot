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
from aiogram.enums import ParseMode
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
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
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(',') if x]

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
# Отправляет SOAP-команду к серверу и возвращает текст результата
    # или сообщение об ошибке.
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
# Извлекает из SOAP-ответа количество игроков, персонажей и аптайм
    # и возвращает отформатированную строку.
def parse_server_info(result: str) -> str:
    players = re.search(r"Connected players:\s*(\d+)", result)
    characters = re.search(r"Characters in world:\s*(\d+)", result)
    uptime = re.search(r"Server uptime:\s*(.+?)\r", result)

    players_text = f"👥 Онлайн игроков: {players.group(1)}" if players else "❓ Игроки: ?"
    chars_text = f"🌍 Персонажей в мире: {characters.group(1)}" if characters else "❓ Персонажи: ?"
    uptime_text = f"⏱ Аптайм: {uptime.group(1)}" if uptime else "❓ Аптайм: ?"

    return f"{players_text}\n{chars_text}\n{uptime_text}"

# === MYSQL ===
# Проверяет, существует ли аккаунт с заданным логином в MySQL базе.
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

# Привязывает Telegram ID к аккаунту, записывая его в поле email в базе.
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

# Получает логин аккаунта по Telegram ID из поля email.
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

# === ХЕНДЛЕРЫ ===
router = Router()

reply_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📥 Регистрация"), KeyboardButton(text="🔐 Смена пароля")],
    [KeyboardButton(text="👥 Онлайн игроки")],
    [KeyboardButton(text="🛎 Услуги"), KeyboardButton(text="🛠️ Админ панель")]
], resize_keyboard=True)

@router.message(F.text == "/start")
async def cmd_start(msg: Message):
    telegram_id = msg.from_user.id
    is_registered = get_username_by_telegram_id(telegram_id) is not None

    if not is_registered:
        buttons = [[KeyboardButton(text="📥 Регистрация")]]
    else:
        buttons = [
            [KeyboardButton(text="🔐 Смена пароля")],
            [KeyboardButton(text="👥 Онлайн игроки")],
            [KeyboardButton(text="🛎 Услуги"), KeyboardButton(text="🛠️ Админ панель")]
        ]

    reply_kb = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

    await msg.answer("Привет! Это бот WoWSeRVeR (set realmlist wowserver.ru) выберете действие:", reply_markup=reply_kb)

# Начало процесса регистрации — ожидается ввод логина и пароля.
@router.message(F.text == "📥 Регистрация")
async def handle_register(msg: Message, state: FSMContext):
    await msg.answer("Введите логин и пароль через пробел:")
    await state.set_state(RegState.credentials)

@router.message(RegState.credentials)
async def process_registration(msg: Message, state: FSMContext):
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

@router.message(F.text == "👥 Онлайн игроки")
async def handle_online(msg: Message):
    result = send_soap_command("server info")
    parsed = parse_server_info(result)
    await msg.answer(parsed)

@router.message(F.text == "🛠️ Админ панель")
async def handle_admin(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        await msg.answer("❌ У вас нет прав.")
        return
    await msg.answer("Введите SOAP команду:")
    await state.set_state(AdminCommandState.command)

@router.message(AdminCommandState.command)
async def execute_admin_command(msg: Message, state: FSMContext):
    result = send_soap_command(msg.text.strip())
    await msg.answer(f"<pre>{escape(result)}</pre>")
    await state.clear()

@router.message(F.text == "🛎 Услуги")
async def show_services(msg: Message):
    await msg.answer("Выберите услугу:", reply_markup=ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔁 Смена пола"), KeyboardButton(text="🔄 Смена фракции")],
            [KeyboardButton(text="🧑‍🎨 Смена внешности"), KeyboardButton(text="📍 Телепортация")]
        ],
        resize_keyboard=True
    ))

@router.message(F.text.in_(["🔁 Смена пола", "🔄 Смена фракции", "🧑‍🎨 Смена внешности", "📍 Телепортация"]))
async def select_service(msg: Message, state: FSMContext):
    service_map = {
        "🔁 Смена пола": "gender",
        "🔄 Смена фракции": "faction",
        "🧑‍🎨 Смена внешности": "customize",
        "📍 Телепортация": "teleport"
    }
    service = service_map.get(msg.text)
    await state.update_data(service=service)
    await state.set_state(ServiceState.character_name)
    await msg.answer("Введите имя персонажа:")

@router.message(ServiceState.character_name)
async def apply_service(msg: Message, state: FSMContext):
    data = await state.get_data()
    char_name = msg.text.strip()
    service = data.get("service")

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

    result = send_soap_command(f"{command.replace('$home', '').strip()} {char_name} $home" if service == "teleport" else f"{command} {char_name}")

    if "does not exist" in result.lower():
        await msg.answer("❌ Персонаж не найден.")
    elif "500" in result.lower():
        await msg.answer("❌ Внутренняя ошибка сервера. Попробуйте позже.")
    else:
        await msg.answer(f"✅ Услуга применена к <b>{char_name}</b>:\n<pre>{escape(result)}</pre>")

    await state.clear()

# === ЗАПУСК ===
async def main():
    print("🚀 Бот запущен...")
    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=MemoryStorage())
    # Здесь регистрируются все хендлеры, так как логика вся в main.py
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
