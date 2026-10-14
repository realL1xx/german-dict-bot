import asyncio
import os
import sqlite3
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

# ====================== НАСТРОЙКИ ======================
TOKEN = os.getenv("BOT_TOKEN") or "ВСТАВЬ_СЮДА_ТОКЕН"

TEACHERS = {"blinova0lga"}
ADMINS = {"l1xxoff"}

KGD_TZ = timezone(timedelta(hours=2))

# NEW: постоянное хранилище Bothost
DB_PATH = "/app/data/german_dict.db"


# ====================== БАЗА ДАННЫХ ======================
def init_db():
    # NEW: гарантируем наличие папки
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article TEXT NOT NULL,
            word TEXT NOT NULL UNIQUE,
            translation TEXT NOT NULL,
            added_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS homework (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            added_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def get_connection():
    return sqlite3.connect(DB_PATH)


def save_user(user_id: int, username: str | None, first_name: str | None):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now(KGD_TZ).isoformat()
    c.execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name, added_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, username, first_name, now),
    )
    conn.commit()
    conn.close()


def get_all_users():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]


# ====================== ХЕЛПЕРЫ ======================
def get_role(username: str | None) -> str:
    if not username:
        return "guest"
    u = username.lower()
    if u in ADMINS:
        return "admin"
    if u in TEACHERS:
        return "teacher"
    return "student"


def is_allowed(username: str | None) -> bool:
    return get_role(username) in ("admin", "teacher", "student")


def get_word_count():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM words")
    count = c.fetchone()[0]
    conn.close()
    return count


def get_last_update():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT added_at FROM words ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    word_time = row[0] if row else None

    c.execute("SELECT updated_at FROM homework ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    hw_time = row[0] if row else None
    conn.close()

    times = [t for t in (word_time, hw_time) if t]
    if not times:
        return None
    return max(times)


def get_homework():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT text FROM homework ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else "ДЗ пока не задано"


def add_word(article: str, word: str, translation: str) -> bool:
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now(KGD_TZ).isoformat()
    try:
        c.execute(
            "INSERT INTO words (article, word, translation, added_at) VALUES (?, ?, ?, ?)",
            (article, word, translation, now),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def add_homework(text: str):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now(KGD_TZ).isoformat()
    c.execute(
        "INSERT INTO homework (text, updated_at) VALUES (?, ?)",
        (text, now),
    )
    conn.commit()
    conn.close()


def get_letters():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT DISTINCT word FROM words")
    rows = c.fetchall()
    conn.close()
    return sorted({row[0][0].upper() for row in rows if row[0]})


def get_words_by_letter(letter: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT article, word, translation FROM words "
        "WHERE UPPER(word) LIKE ? ORDER BY word COLLATE NOCASE",
        (f"{letter}%",),
    )
    rows = c.fetchall()
    conn.close()
    return rows


# NEW: форматирование строки слова с учётом «нету»
def format_word(article: str, word: str, translation: str) -> str:
    if article == "none":
        return f"<b>{word}</b> — {translation}"
    return f"<b>{article} {word}</b> — {translation}"


# ====================== КЛАВИАТУРЫ ======================
def main_menu(role: str) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📖 Словарь")],
        [KeyboardButton(text="ℹ️ Информация")],
    ]
    if role in ("admin", "teacher"):
        buttons.append([KeyboardButton(text="➕ Добавить")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def letters_keyboard(letters: list[str]) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for letter in letters:
        row.append(InlineKeyboardButton(text=letter, callback_data=f"letter:{letter}"))
        if len(row) == 6:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# NEW: добавлена кнопка «нету»
def article_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="der", callback_data="article:der"),
                InlineKeyboardButton(text="die", callback_data="article:die"),
                InlineKeyboardButton(text="das", callback_data="article:das"),
            ],
            [
                InlineKeyboardButton(text="нету", callback_data="article:none"),
            ],
        ]
    )


def add_menu() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📝 Добавить слово")],
        [KeyboardButton(text="📚 Добавить ДЗ")],
        [KeyboardButton(text="🔙 Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )


# ====================== FSM ======================
class AddWord(StatesGroup):
    article = State()
    word = State()
    translation = State()


class AddHW(StatesGroup):
    text = State()


# ====================== БОТ ======================
bot = Bot(token=TOKEN)
dp = Dispatcher()


async def broadcast_new_word(article: str, word: str, translation: str):
    if article == "none":
        text = (
            f"🆕 <b>Добавлено новое слово!</b>\n\n"
            f"<b>{word}</b> — {translation}"
        )
    else:
        text = (
            f"🆕 <b>Добавлено новое слово!</b>\n\n"
            f"<b>{article} {word}</b> — {translation}"
        )

    user_ids = get_all_users()
    for uid in user_ids:
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
        except TelegramForbiddenError:
            pass
        except TelegramBadRequest:
            pass
        except Exception:
            pass
        await asyncio.sleep(0.05)


# ---------- /start ----------
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    save_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    username = message.from_user.username
    if not is_allowed(username):
        await message.answer(
            "⛔ Доступ запрещён.\n"
            "Этот бот только для учеников, учителей и админов."
        )
        return

    role = get_role(username)
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\n"
        f"Твоя роль: {role}",
        reply_markup=main_menu(role),
    )


# ---------- Словарь ----------
@dp.message(F.text == "📖 Словарь")
async def show_dictionary(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.username):
        return
    await state.clear()

    letters = get_letters()
    if not letters:
        await message.answer("Словарь пуст. Учитель ещё не добавил слова.")
        return

    await message.answer(
        "Выбери первую букву слова:",
        reply_markup=letters_keyboard(letters),
    )


@dp.callback_query(F.data.startswith("letter:"))
async def show_words(callback: types.CallbackQuery):
    if not is_allowed(callback.from_user.username):
        await callback.answer("Нет доступа", show_alert=True)
        return

    letter = callback.data.split(":")[1]
    words = get_words_by_letter(letter)

    if not words:
        await callback.message.edit_text(f"На букву {letter} слов нет.")
        await callback.answer()
        return

    lines = [f"<b>Слова на «{letter}»</b>\n"]
    for article, word, translation in words:
        # NEW: используем format_word
        lines.append(format_word(article, word, translation))

    text = "\n".join(lines)
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К буквам", callback_data="back_to_letters")]
        ]
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb)
    await callback.answer()


@dp.callback_query(F.data == "back_to_letters")
async def back_to_letters(callback: types.CallbackQuery):
    if not is_allowed(callback.from_user.username):
        return
    letters = get_letters()
    await callback.message.edit_text(
        "Выбери первую букву слова:",
        reply_markup=letters_keyboard(letters),
    )
    await callback.answer()


# ---------- Информация ----------
@dp.message(F.text == "ℹ️ Информация")
async def show_info(message: types.Message):
    if not is_allowed(message.from_user.username):
        return

    count = get_word_count()
    last_update = get_last_update()
    hw = get_homework()

    if last_update:
        try:
            dt = datetime.fromisoformat(last_update)
            last_update_str = dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            last_update_str = last_update
    else:
        last_update_str = "ещё не было"

    text = (
        f"📊 <b>Информация о словаре</b>\n\n"
        f"📖 Слов в словаре: <b>{count}</b>\n"
        f"🕒 Последнее обновление: <b>{last_update_str}</b> (Калининград)\n\n"
        f"📚 <b>ДЗ на следующий урок:</b>\n{hw}"
    )
    await message.answer(text, parse_mode="HTML")


# ---------- Меню «Добавить» ----------
@dp.message(F.text == "➕ Добавить")
async def add_menu_handler(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role not in ("admin", "teacher"):
        return
    await state.clear()
    await message.answer("Что добавить?", reply_markup=add_menu())


# ---------- Добавить слово ----------
@dp.message(F.text == "📝 Добавить слово")
async def add_word_start(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role not in ("admin", "teacher"):
        return

    await state.set_state(AddWord.article)
    await message.answer(
        "Выбери род нового слова (или «нету», если рода нет):",
        reply_markup=article_keyboard(),
    )


@dp.callback_query(F.data.startswith("article:"))
async def add_word_article(callback: types.CallbackQuery, state: FSMContext):
    role = get_role(callback.from_user.username)
    if role not in ("admin", "teacher"):
        await callback.answer("Нет доступа", show_alert=True)
        return

    current_state = await state.get_state()
    if current_state != AddWord.article.state:
        await callback.answer(
            "Сначала нажми «📝 Добавить слово»", show_alert=True
        )
        return

    article = callback.data.split(":")[1]
    await state.update_data(article=article)
    await state.set_state(AddWord.word)

    # NEW: разный текст в зависимости от выбора
    if article == "none":
        prompt = "Род: <b>нету</b>\n\nТеперь напиши само слово (можно с умлаутами):"
    else:
        prompt = f"Род: <b>{article}</b>\n\nТеперь напиши само слово (можно с умлаутами):"

    await callback.message.edit_text(prompt, parse_mode="HTML")
    await callback.answer()


@dp.message(AddWord.word)
async def add_word_word(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        role = get_role(message.from_user.username)
        await message.answer("Отменено.", reply_markup=main_menu(role))
        return

    await state.update_data(word=message.text.strip())
    await state.set_state(AddWord.translation)
    await message.answer(
        "Теперь напиши перевод:", reply_markup=cancel_keyboard()
    )


@dp.message(AddWord.translation)
async def add_word_translation(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        role = get_role(message.from_user.username)
        await message.answer("Отменено.", reply_markup=main_menu(role))
        return

    data = await state.get_data()
    translation = message.text.strip()
    article = data["article"]
    word = data["word"]

    success = add_word(article, word, translation)
    role = get_role(message.from_user.username)

    if success:
        # NEW: показываем слово с учётом «нету»
        if article == "none":
            display = f"<b>{word}</b> — {translation}"
        else:
            display = f"<b>{article} {word}</b> — {translation}"

        await message.answer(
            f"✅ Добавлено: {display}",
            parse_mode="HTML",
            reply_markup=main_menu(role),
        )
        await broadcast_new_word(article, word, translation)
    else:
        await message.answer(
            f"❌ Слово «{word}» уже есть в словаре.",
            reply_markup=main_menu(role),
        )
    await state.clear()


# ---------- Добавить ДЗ ----------
@dp.message(F.text == "📚 Добавить ДЗ")
async def add_hw_start(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role not in ("admin", "teacher"):
        return

    await state.set_state(AddHW.text)
    await message.answer(
        "Напиши ДЗ на следующий урок и дату одним сообщением.\n"
        "Пример: <i>Упражнение 5 стр. 23, повторить слова. К 15.11</i>\n\n"
        "Или нажми «❌ Отмена».",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )


@dp.message(AddHW.text)
async def add_hw_text(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        role = get_role(message.from_user.username)
        await message.answer("Отменено.", reply_markup=main_menu(role))
        return

    add_homework(message.text.strip())
    role = get_role(message.from_user.username)
    await message.answer(
        "✅ ДЗ сохранено.",
        reply_markup=main_menu(role),
    )
    await state.clear()


# ---------- Назад ----------
@dp.message(F.text == "🔙 Назад")
async def back_to_main(message: types.Message, state: FSMContext):
    await state.clear()
    role = get_role(message.from_user.username)
    await message.answer("Главное меню", reply_markup=main_menu(role))


# ---------- Фоллбэк ----------
@dp.message()
async def fallback(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return

    save_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    if not is_allowed(message.from_user.username):
        return

    role = get_role(message.from_user.username)
    await message.answer(
        "Не понял команду. Используй кнопки ниже.",
        reply_markup=main_menu(role),
    )


# ====================== ЗАПУСК ======================
async def main():
    init_db()
    print("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
