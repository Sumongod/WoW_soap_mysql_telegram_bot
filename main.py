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
    login = State()
    password = State()

class PasswordChangeState(StatesGroup):
    new_password = State()

class AdminCommandState(StatesGroup):
    command = State()

class AdminPanelState(StatesGroup):
    choice = State()

class ServiceState(StatesGroup):
    character_name = State()
    service_type = State()

class BanState(StatesGroup):
    character_name = State()
    bantime = State()
    reason = State()

class UnbanState(StatesGroup):

    character_name = State()
class SendMailState(StatesGroup):
    character_name = State()
    subject = State()
    text = State()

class SendMoneyState(StatesGroup):
    character_name = State()
    subject = State()
    text = State()
    amount = State()

class SendItemsState(StatesGroup):
    character_name = State()
    subject = State()
    text = State()
    items = State()

class RestartServerState(StatesGroup):
    delay = State()
    exit_code = State()

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
    try:
        conn_auth = mysql.connector.connect(**DB_CONFIG)
        cursor_auth = conn_auth.cursor()
        cursor_auth.execute("SELECT id FROM account WHERE email = %s", (str(telegram_id),))
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
            (account_id,)
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
async def handle_services(msg: Message, state: FSMContext):
    chars = get_characters_by_telegram_id(msg.from_user.id)
    if not chars:
        await msg.answer("❌ У вас нет персонажей или вы не зарегистрированы.")
        return

    buttons = [[KeyboardButton(text=name)] for name, _ in chars]
    kb = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    await msg.answer("Выберите персонажа:", reply_markup=kb)
    await state.set_state(ServiceState.character_name)

@router.message(ServiceState.character_name)
async def handle_service_menu(msg: Message, state: FSMContext):
    char_name = msg.text.strip()
    if not is_character_owned_by_user(char_name, msg.from_user.id):
        await msg.answer("❌ Этот персонаж не принадлежит вашему аккаунту.")
        await state.clear()
        return

    await state.update_data(character_name=char_name)

    buttons = [
        [KeyboardButton(text="🔁 Смена пола"), KeyboardButton(text="🔄 Смена фракции")],
        [KeyboardButton(text="🧑‍🎨 Смена внешности"), KeyboardButton(text="📍 Телепортация")]
    ]
    kb = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    await msg.answer("Выберите услугу:", reply_markup=kb)
    await state.set_state(ServiceState.service_type)

@router.message(ServiceState.service_type)
async def handle_apply_service(msg: Message, state: FSMContext):
    data = await state.get_data()
    char_name = data.get("character_name")
    service_map = {
        "🔁 Смена пола": "gender",
        "🔄 Смена фракции": "faction",
        "🧑‍🎨 Смена внешности": "customize",
        "📍 Телепортация": "teleport"
    }
    service = service_map.get(msg.text)

    if not service:
        await msg.answer("❌ Неизвестная услуга.")
        await state.clear()
        return

    if not char_name or not is_character_owned_by_user(char_name, msg.from_user.id):
        await msg.answer("❌ Этот персонаж не принадлежит вашему аккаунту.")
        await state.clear()
        return
    command_map = {
        "gender": "character customize",
        "faction": "character changefaction",
        "customize": "character customize",
        "teleport": "teleport name $home"
    }

    command = command_map.get(service)
    full_command = (
        f"teleport name {char_name} $home" if service == "teleport" else f"{command} {char_name}"
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
    username = get_username_by_telegram_id(telegram_id)

    if not username:
        buttons = [[KeyboardButton(text="📥 Регистрация")]]
        greeting = (
            "Добро Пожаловать в регистрационный бот игры World Of Warcraft на сервере WoWSeRVeR!"
        )
    else:
        buttons = [
            [KeyboardButton(text="🔐 Смена пароля")],
            [KeyboardButton(text="👥 Онлайн игроки")],
            [KeyboardButton(text="📜 Мои персонажи")],
            [KeyboardButton(text="🛎 Услуги")]
        ]
        if has_gm_access(telegram_id, 3):
            buttons[-1].append(KeyboardButton(text="🛠️ Админ панель"))
        greeting = f"Добро Пожаловать снова {username}"

    reply_kb = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    await msg.answer(greeting, reply_markup=reply_kb)

@router.message(F.text == "📜 Мои персонажи")
async def handle_my_chars(msg: Message):
    chars = get_characters_by_telegram_id(msg.from_user.id)
    if not chars:
        await msg.answer("❌ У вас нет персонажей или вы не зарегистрированы.")
    else:
        lines = [f"• {name} (ур. {lvl})" for name, lvl in chars]
        await msg.answer("👤 Ваши персонажи:\n" + "\n".join(lines))

@router.message(F.text == "👥 Онлайн игроки")
async def handle_online_players(msg: Message):
    result = send_soap_command("server info")
    parsed = parse_server_info(result)
    await msg.answer(parsed)

@router.message(F.text == "📥 Регистрация")
async def handle_register(msg: Message, state: FSMContext):
    await msg.answer("Введите логин:")
    await state.set_state(RegState.login)

@router.message(RegState.login)
async def process_register_login(msg: Message, state: FSMContext):
    login = msg.text.strip()
    telegram_id = msg.from_user.id
    existing_login = get_username_by_telegram_id(telegram_id)

    if existing_login:
        await msg.answer(f"🔐 Вы уже зарегистрированы под логином <b>{existing_login}</b>.")
        await state.clear()
        return
    if is_account_exists(login):
        await msg.answer("❌ Логин уже занят. Введите другой логин:")
        return
    await state.update_data(login=login)
    await msg.answer("Введите пароль:")
    await state.set_state(RegState.password)

@router.message(RegState.password)
async def process_register_password(msg: Message, state: FSMContext):
    password = msg.text.strip()
    data = await state.get_data()
    login = data.get("login")
    telegram_id = msg.from_user.id
    existing_login = get_username_by_telegram_id(telegram_id)

    if existing_login:
        await msg.answer(f"🔐 Вы уже зарегистрированы под логином <b>{existing_login}</b>.")
        await state.clear()
    elif is_account_exists(login):
        await msg.answer("❌ Логин уже занят.")
        await state.clear()
    else:
        result = send_soap_command(f"account create {login} {password}")
        set_telegram_email(login, telegram_id)
        match = re.search(r"Account created: (\S+)", result)
        if match:
            result = f"Аккаунт создан: {match.group(1)}"
        await msg.answer(f"✅ {escape(result)}")
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
    buttons = [
        [KeyboardButton(text="✉️ Отправить письмо"), KeyboardButton(text="💰 Отправить золото")],
        [KeyboardButton(text="🎁 Отправить предмет"), KeyboardButton(text="⛔ Забанить")],
        [KeyboardButton(text="👢 Кикнуть с сервера"), KeyboardButton(text="🔓 Разбанить")],
        [KeyboardButton(text="🔄 Рестарт сервера")],
        [KeyboardButton(text="⌨️ Выполнить команду")]
    ]
    kb = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    await msg.answer("Выберите действие:", reply_markup=kb)
    await state.set_state(AdminPanelState.choice)

@router.message(AdminPanelState.choice)
async def handle_admin_choice(msg: Message, state: FSMContext):
    action = msg.text.strip()
    if action == "⌨️ Выполнить команду":
        await msg.answer("Введите SOAP команду:")
        await state.set_state(AdminCommandState.command)
        return
    if action == "✉️ Отправить письмо":
        await msg.answer("Введите имя персонажа:")
        await state.set_state(SendMailState.character_name)
        return
    if action == "💰 Отправить золото":
        await msg.answer("Введите имя персонажа:")
        await state.set_state(SendMoneyState.character_name)
        return
    if action == "🎁 Отправить предмет":
        await msg.answer("Введите имя персонажа:")
        await state.set_state(SendItemsState.character_name)
        return
    if action == "⛔ Забанить":
        await msg.answer("Введите имя персонажа:")
        await state.set_state(BanState.character_name)
        return
    if action == "🔓 Разбанить":
        await msg.answer("Введите имя персонажа:")
        await state.set_state(UnbanState.character_name)
        return
    if action == "🔄 Рестарт сервера":
        await msg.answer("Введите задержку в секундах:")
        await state.set_state(RestartServerState.delay)
        return
    await msg.answer(f"Функция <b>{escape(action)}</b> пока не реализована.")
    await state.clear()

@router.message(BanState.character_name)
async def process_ban_character(msg: Message, state: FSMContext):
    await state.update_data(character_name=msg.text.strip())
    await msg.answer("Введите время бана в секундах:")
    await state.set_state(BanState.bantime)

@router.message(BanState.bantime)
async def process_ban_time(msg: Message, state: FSMContext):
    bantime = msg.text.strip()
    if not bantime.isdigit():
        await msg.answer("Введите число секунд:")
        return
    await state.update_data(bantime=bantime)
    await msg.answer("Введите причину бана:")
    await state.set_state(BanState.reason)

@router.message(BanState.reason)
async def process_ban_reason(msg: Message, state: FSMContext):
    data = await state.get_data()
    char_name = data.get("character_name")
    bantime = data.get("bantime")
    reason = msg.text.strip()
    result = send_soap_command(f"ban character {char_name} {bantime} {reason}")
    await msg.answer(f"<pre>{escape(result)}</pre>")
    await state.clear()

@router.message(UnbanState.character_name)
async def process_unban_character(msg: Message, state: FSMContext):
    char_name = msg.text.strip()
    result = send_soap_command(f"unban character {char_name}")
    await msg.answer(f"<pre>{escape(result)}</pre>")
    await state.clear()
@router.message(SendMailState.character_name)
async def process_mail_name(msg: Message, state: FSMContext):
    await state.update_data(character_name=msg.text.strip())
    await msg.answer("Введите тему письма:")
    await state.set_state(SendMailState.subject)

@router.message(SendMailState.subject)
async def process_mail_subject(msg: Message, state: FSMContext):
    await state.update_data(subject=msg.text.strip())
    await msg.answer("Введите текст письма:")
    await state.set_state(SendMailState.text)

@router.message(SendMailState.text)
async def process_send_mail(msg: Message, state: FSMContext):
    data = await state.get_data()
    char_name = data.get("character_name")
    subject = data.get("subject", "").replace('"', '\\"')
    text = msg.text.strip().replace('"', '\\"')
    cmd = f'send mail {char_name} "{subject}" "{text}"'
    result = send_soap_command(cmd)
    await msg.answer(f"<pre>{escape(result)}</pre>")
    await state.clear()

@router.message(SendMoneyState.character_name)
async def process_money_name(msg: Message, state: FSMContext):
    await state.update_data(character_name=msg.text.strip())
    await msg.answer("Введите тему письма:")
    await state.set_state(SendMoneyState.subject)

@router.message(SendMoneyState.subject)
async def process_money_subject(msg: Message, state: FSMContext):
    await state.update_data(subject=msg.text.strip())
    await msg.answer("Введите текст письма:")
    await state.set_state(SendMoneyState.text)

@router.message(SendMoneyState.text)
async def process_money_text(msg: Message, state: FSMContext):
    await state.update_data(text=msg.text.strip())
    await msg.answer("Введите количество золота:")
    await state.set_state(SendMoneyState.amount)

@router.message(SendMoneyState.amount)
async def process_send_money(msg: Message, state: FSMContext):
    if not msg.text.strip().isdigit():
        await msg.answer("Введите число золота:")
        return
    data = await state.get_data()
    char_name = data.get("character_name")
    subject = data.get("subject", "").replace('"', '\\"')
    text = data.get("text", "").replace('"', '\\"')
    amount = msg.text.strip()
    cmd = f'send money {char_name} "{subject}" "{text}" {amount}'
    result = send_soap_command(cmd)
    await msg.answer(f"<pre>{escape(result)}</pre>")
    await state.clear()

@router.message(SendItemsState.character_name)
async def process_items_name(msg: Message, state: FSMContext):
    await state.update_data(character_name=msg.text.strip())
    await msg.answer("Введите тему письма:")
    await state.set_state(SendItemsState.subject)

@router.message(SendItemsState.subject)
async def process_items_subject(msg: Message, state: FSMContext):
    await state.update_data(subject=msg.text.strip())
    await msg.answer("Введите текст письма:")
    await state.set_state(SendItemsState.text)

@router.message(SendItemsState.text)
async def process_items_text(msg: Message, state: FSMContext):
    await state.update_data(text=msg.text.strip())
    await msg.answer("Введите предметы (id[:кол-во] через пробел):")
    await state.set_state(SendItemsState.items)

@router.message(SendItemsState.items)
async def process_send_items(msg: Message, state: FSMContext):
    data = await state.get_data()
    char_name = data.get("character_name")
    subject = data.get("subject", "").replace('"', '\\"')
    text = data.get("text", "").replace('"', '\\"')
    items = msg.text.strip()
    cmd = f'send items {char_name} "{subject}" "{text}" {items}'
    result = send_soap_command(cmd)
    await msg.answer(f"<pre>{escape(result)}</pre>")
    await state.clear()

@router.message(RestartServerState.delay)
async def process_restart_delay(msg: Message, state: FSMContext):
    delay = msg.text.strip()
    if not delay.isdigit():
        await msg.answer("Введите число секунд:")
        return
    await state.update_data(delay=delay)
    await msg.answer("Введите код завершения (по умолчанию 0):")
    await state.set_state(RestartServerState.exit_code)

@router.message(RestartServerState.exit_code)
async def process_restart_exit_code(msg: Message, state: FSMContext):
    exit_code = msg.text.strip()
    if not exit_code.isdigit():
        exit_code = "0"
    data = await state.get_data()
    delay = data.get("delay", "0")
    cmd = f'server restart {delay} {exit_code}'
    result = send_soap_command(cmd)
    await msg.answer(f"<pre>{escape(result)}</pre>")
    await state.clear()
@router.message(AdminCommandState.command)
async def execute_admin_command(msg: Message, state: FSMContext):
    result = send_soap_command(msg.text.strip())
    await msg.answer(f"<pre>{escape(result)}</pre>")
    await state.clear()

# === ЗАПУСК ===
async def main():
    print("🚀 Бот запущен...")
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await storage.close()

if __name__ == "__main__":
    asyncio.run(main())
