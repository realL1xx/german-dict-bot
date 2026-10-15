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

DB_PATH = "/app/data/german_dict.db"

# NEW: сколько дней хранить в «недавних»
RECENT_DAYS = 8


# ====================== БАЗА ДАННЫХ ======================
def init_db():
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


# NEW: удаление слова по id
def delete_word_by_id(word_id: int) -> tuple[bool, str, str, str]:
    """Возвращает (success, article, word, translation)."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT article, word, translation FROM words WHERE id = ?", (word_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "", "", ""
    article, word, translation = row
    c.execute("DELETE FROM words WHERE id = ?", (word_id,))
    conn.commit()
    conn.close()
    return True, article, word, translation


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
        "SELECT id, article, word, translation FROM words "
        "WHERE UPPER(word) LIKE ? ORDER BY word COLLATE NOCASE",
        (f"{letter}%",),
    )
    rows = c.fetchall()
    conn.close()
    return rows


# NEW: все слова (для кнопки «Все слова»)
def get_all_words():
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, article, word, translation FROM words ORDER BY word COLLATE NOCASE"
    )
    rows = c.fetchall()
    conn.close()
    return rows


# NEW: недавние слова (за последние RECENT_DAYS дней), по алфавиту
def get_recent_words():
    conn = get_connection()
    c = conn.cursor()
    threshold = (datetime.now(KGD_TZ) - timedelta(days=RECENT_DAYS)).isoformat()
    c.execute(
        "SELECT id, article, word, translation FROM words "
        "WHERE added_at >= ? ORDER BY word COLLATE NOCASE",
        (threshold,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


# NEW: форматирование строки слова
def format_word(article: str, word: str, translation: str, html: bool = True) -> str:
    if article == "none":
        if html:
            return f"<b>{word}</b> — {translation}"
        return f"{word} — {translation}"
    if html:
        return f"<b>{article} {word}</b> — {translation}"
    return f"{article} {word} — {translation}"


# ====================== КЛАВИАТУРЫ ======================
def main_menu(role: str) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📖 Словарь")],
        [KeyboardButton(text="ℹ️ Информация")],
    ]
    if role in ("admin", "teacher"):
        buttons.append([KeyboardButton(text="➕ Добавить")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


# NEW: клавиатура словаря — добавлена кнопка «Все слова»
def dictionary_keyboard(letters: list[str], role: str) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for letter in letters:
        row.append(InlineKeyboardButton(text=letter, callback_data=f"letter:{letter}"))
        if len(row) == 6:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # Кнопка «Все слова» внизу
    rows.append([InlineKeyboardButton(text="📚 Все слова", callback_data="all_words")])

    # NEW: кнопка удаления — только для учителя/админа
    if role in ("admin", "teacher"):
        rows.append(
            [InlineKeyboardButton(text="🗑 Удалить слово", callback_data="delete_start")]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


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


# NEW: клавиатура списка слов для удаления
def delete_words_keyboard(words: list) -> InlineKeyboardMarkup:
    rows = []
    for word_id, article, word, translation in words:
        if article == "none":
            label = word
        else:
            label = f"{article} {word}"
        # Telegram ограничивает длину callback_data (64 байта) — id короткий, ок
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"del_word:{word_id}")]
        )
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="delete_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_delete_keyboard(word_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить", callback_data=f"del_confirm:{word_id}"
                ),
                InlineKeyboardButton(text="❌ Отмена", callback_data="delete_back"),
            ]
        ]
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
    text = (
        f"🆕 <b>Добавлено новое слово!</b>\n\n"
        f"{format_word(article, word, translation)}"
    )
    for uid in get_all_users():
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
        except Exception:
            pass
        await asyncio.sleep(0.05)


# NEW: рассылка об удалении
async def broadcast_deleted_word(article: str, word: str, translation: str):
    if article == "none":
        display = f"<b>{word}</b>"
    else:
        display = f"<b>{article} {word}</b>"
    text = (
        f"🗑 <b>Слово удалено из словаря</b>\n\n"
        f"{display} — {translation}"
    )
    for uid in get_all_users():
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
        except Exception:
            pass
        await asyncio.sleep(0.05)


# NEW: отправка длинного текста частями
async def send_long_message(message: types.Message, text: str, reply_markup=None):
    """Разбивает длинный текст на куски по 4000 символов."""
    LIMIT = 4000
    parts = []
    while len(text) > LIMIT:
        # ищем последний перенос строки в пределах лимита
        cut = text.rfind("\n", 0, LIMIT)
        if cut == -1:
            cut = LIMIT
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    parts.append(text)

    for i, part in enumerate(parts):
        if i == len(parts) - 1 and reply_markup is not None:
            await message.answer(part, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await message.answer(part, parse_mode="HTML")


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

    role = get_role(message.from_user.username)

    # NEW: сначала показываем недавние слова (за 8 дней)
    recent = get_recent_words()
    if recent:
        lines = [f"🕒 <b>Недавние слова (за {RECENT_DAYS} дней):</b>\n"]
        for _, article, word, translation in recent:
            lines.append(format_word(article, word, translation))
        recent_text = "\n".join(lines)
        await send_long_message(message, recent_text)

    letters = get_letters()
    if not letters:
        await message.answer("Словарь пуст. Учитель ещё не добавил слова.")
        return

    await message.answer(
        "Выбери первую букву слова или нажми «Все слова»:",
        reply_markup=dictionary_keyboard(letters, role),
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
    for _, article, word, translation in words:
        lines.append(format_word(article, word, translation))

    text = "\n".join(lines)
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К буквам", callback_data="back_to_letters")]
        ]
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb)
    await callback.answer()


# NEW: «Все слова» — выводим всё сразу
@dp.callback_query(F.data == "all_words")
async def show_all_words(callback: types.CallbackQuery):
    if not is_allowed(callback.from_user.username):
        await callback.answer("Нет доступа", show_alert=True)
        return

    words = get_all_words()
    if not words:
        await callback.message.edit_text("Словарь пуст.")
        await callback.answer()
        return

    lines = [f"📚 <b>Все слова ({len(words)})</b>\n"]

    # Группируем по первой букве
    current_letter = None
    for _, article, word, translation in words:
        first = word[0].upper()
        if first != current_letter:
            if current_letter is not None:
                lines.append("")  # пустая строка между группами
            lines.append(f"— <b>{first}</b> —")
            current_letter = first
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
    role = get_role(callback.from_user.username)
    letters = get_letters()

    if not letters:
        await callback.message.edit_text("Словарь пуст.")
        await callback.answer()
        return

    await callback.message.edit_text(
        "Выбери первую букву слова или нажми «Все слова»:",
        reply_markup=dictionary_keyboard(letters, role),
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
        display = format_word(article, word, translation)
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


# ---------- NEW: Удаление слов ----------
@dp.callback_query(F.data == "delete_start")
async def delete_start(callback: types.CallbackQuery):
    role = get_role(callback.from_user.username)
    if role not in ("admin", "teacher"):
        await callback.answer("Нет доступа", show_alert=True)
        return

    letters = get_letters()
    if not letters:
        await callback.answer("Словарь пуст", show_alert=True)
        return

    rows = []
    row = []
    for letter in letters:
        row.append(InlineKeyboardButton(text=letter, callback_data=f"del_letter:{letter}"))
        if len(row) == 6:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_letters")])

    await callback.message.edit_text(
        "Выбери букву слова, которое нужно удалить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("del_letter:"))
async def delete_letter(callback: types.CallbackQuery):
    role = get_role(callback.from_user.username)
    if role not in ("admin", "teacher"):
        await callback.answer("Нет доступа", show_alert=True)
        return

    letter = callback.data.split(":")[1]
    words = get_words_by_letter(letter)

    if not words:
        await callback.answer("На эту букву слов нет", show_alert=True)
        return

    await callback.message.edit_text(
        f"Выбери слово на «{letter}» для удаления:",
        reply_markup=delete_words_keyboard(words),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("del_word:"))
async def delete_word_confirm(callback: types.CallbackQuery):
    role = get_role(callback.from_user.username)
    if role not in ("admin", "teacher"):
        await callback.answer("Нет доступа", show_alert=True)
        return

    word_id = int(callback.data.split(":")[1])

    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT article, word, translation FROM words WHERE id = ?", (word_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        await callback.answer("Слово не найдено", show_alert=True)
        return

    article, word, translation = row
    display = format_word(article, word, translation)

    await callback.message.edit_text(
        f"Удалить это слово?\n\n{display}",
        parse_mode="HTML",
        reply_markup=confirm_delete_keyboard(word_id),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("del_confirm:"))
async def delete_word_do(callback: types.CallbackQuery):
    role = get_role(callback.from_user.username)
    if role not in ("admin", "teacher"):
        await callback.answer("Нет доступа", show_alert=True)
        return

    word_id = int(callback.data.split(":")[1])
    success, article, word, translation = delete_word_by_id(word_id)

    if not success:
        await callback.answer("Слово уже удалено", show_alert=True)
        return

    await callback.message.edit_text(
        f"🗑 Удалено: {format_word(article, word, translation)}",
        parse_mode="HTML",
    )
    await callback.answer("Удалено")
    await broadcast_deleted_word(article, word, translation)


@dp.callback_query(F.data == "delete_back")
async def delete_back(callback: types.CallbackQuery):
    if not is_allowed(callback.from_user.username):
        return
    role = get_role(callback.from_user.username)
    letters = get_letters()

    if not letters:
        await callback.message.edit_text("Словарь пуст.")
        await callback.answer()
        return

    await callback.message.edit_text(
        "Выбери первую букву слова или нажми «Все слова»:",
        reply_markup=dictionary_keyboard(letters, role),
    )
    await callback.answer()


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
