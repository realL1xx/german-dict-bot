import asyncio
import sqlite3
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ====================== НАСТРОЙКИ ======================
TOKEN = "8858592699:AAGdD5BGqKwTqrNeROyT0DtaIwDFoYIyRUE"

# Юзернеймы (без @, в нижнем регистре)
TEACHERS = {"blinova0lga"}
ADMINS = {"l1xxoff"}

# Часовой пояс Калининграда (UTC+2)
KGD_TZ = timezone(timedelta(hours=2))

# ====================== БАЗА ДАННЫХ ======================
DB_PATH = "german_dict.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Слова: род + слово (уникально) + перевод
    c.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article TEXT NOT NULL,
            word TEXT NOT NULL UNIQUE,
            translation TEXT NOT NULL,
            added_at TEXT NOT NULL
        )
    """)
    # ДЗ: текст и дата (одна строка)
    c.execute("""
        CREATE TABLE IF NOT EXISTS homework (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def get_connection():
    return sqlite3.connect(DB_PATH)

# ====================== ХЕЛПЕРЫ ======================
def get_role(username: str | None) -> str:
    """Возвращает 'admin', 'teacher' или 'student' (или 'guest' если None)."""
    if not username:
        return "guest"
    u = username.lower()
    if u in ADMINS:
        return "admin"
    if u in TEACHERS:
        return "teacher"
    return "student"

def is_allowed(username: str | None) -> bool:
    """Пускать ли в бота."""
    role = get_role(username)
    return role in ("admin", "teacher", "student")

def get_word_count():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM words")
    count = c.fetchone()[0]
    conn.close()
    return count

def get_last_update():
    """Время последнего обновления (последнее добавленное слово или ДЗ)."""
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

def add_word(article: str, word: str, translation: str):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now(KGD_TZ).isoformat()
    try:
        c.execute(
            "INSERT INTO words (article, word, translation, added_at) VALUES (?, ?, ?, ?)",
            (article, word, translation, now)
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
        (text, now)
    )
    conn.commit()
    conn.close()

def get_letters():
    """Уникальные первые буквы слов (род не учитывается)."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT DISTINCT word FROM words")
    rows = c.fetchall()
    conn.close()
    letters = sorted({row[0][0].upper() for row in rows})
    return letters

def get_words_by_letter(letter: str):
    """Слова на букву (сортировка по слову без рода)."""
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT article, word, translation FROM words WHERE UPPER(word) LIKE ? ORDER BY word COLLATE NOCASE",
        (f"{letter}%",)
    )
    rows = c.fetchall()
    conn.close()
    return rows

# ====================== КЛАВИАТУРЫ ======================
def main_menu(role: str):
    buttons = [
        [KeyboardButton(text="📖 Словарь")],
        [KeyboardButton(text="ℹ️ Информация")],
    ]
    if role in ("admin", "teacher"):
        buttons.append([KeyboardButton(text="➕ Добавить")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def letters_keyboard(letters: list[str]):
    builder = InlineKeyboardBuilder()
    for letter in letters:
        builder.button(text=letter, callback_data=f"letter:{letter}")
    builder.adjust(6)  # 6 букв в ряд
    return builder.as_markup()

def word_action_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="der", callback_data="article:der")
    builder.button(text="die", callback_data="article:die")
    builder.button(text="das", callback_data="article:das")
    builder.adjust(3)
    return builder.as_markup()

def add_menu():
    buttons = [
        [KeyboardButton(text="📝 Добавить слово")],
        [KeyboardButton(text="📚 Добавить ДЗ")],
        [KeyboardButton(text="🔙 Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ====================== FSM ======================
class AddWord(StatesGroup):
    article = State()
    word = State()
    translation = State()

class AddHW(StatesGroup):
    text = State()

# ====================== ХЕНДЛЕРЫ ======================
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Проверка доступа на /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
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
        reply_markup=main_menu(role)
    )

# Главное меню: Словарь
@dp.message(F.text == "📖 Словарь")
async def show_dictionary(message: types.Message):
    if not is_allowed(message.from_user.username):
        return
    letters = get_letters()
    if not letters:
        await message.answer("Словарь пуст. Учитель ещё не добавил слова.")
        return
    await message.answer(
        "Выбери первую букву слова:",
        reply_markup=letters_keyboard(letters)
    )

# Callback: нажата буква
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

    # Формируем текст: перед словом род, но алфавитный порядок не нарушается
    lines = [f"<b>Слова на «{letter}»</b>\n"]
    for article, word, translation in words:
        lines.append(f"<b>{article}</b> {word} — {translation}")

    text = "\n".join(lines)
    # Добавляем кнопку назад
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К буквам", callback_data="back_to_letters")

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()

# Callback: назад к списку букв
@dp.callback_query(F.data == "back_to_letters")
async def back_to_letters(callback: types.CallbackQuery):
    if not is_allowed(callback.from_user.username):
        return
    letters = get_letters()
    await callback.message.edit_text(
        "Выбери первую букву слова:",
        reply_markup=letters_keyboard(letters)
    )
    await callback.answer()

# Информация
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

# Добавить (только учитель/админ)
@dp.message(F.text == "➕ Добавить")
async def add_menu_handler(message: types.Message):
    role = get_role(message.from_user.username)
    if role not in ("admin", "teacher"):
        return
    await message.answer("Что добавить?", reply_markup=add_menu())

@dp.message(F.text == "📝 Добавить слово")
async def add_word_start(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role not in ("admin", "teacher"):
        return
    await state.set_state(AddWord.article)
    await message.answer(
        "Выбери род нового слова:",
        reply_markup=word_action_keyboard()
    )

@dp.callback_query(F.data.startswith("article:"), AddWord.article)
async def add_word_article(callback: types.CallbackQuery, state: FSMContext):
    article = callback.data.split(":")[1]
    await state.update_data(article=article)
    await state.set_state(AddWord.word)
    await callback.message.edit_text(
        f"Род: <b>{article}</b>\n\nТеперь напиши само слово (можно с умлаутами):",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(AddWord.word)
async def add_word_word(message: types.Message, state: FSMContext):
    await state.update_data(word=message.text.strip())
    await state.set_state(AddWord.translation)
    await message.answer("Теперь напиши перевод:")

@dp.message(AddWord.translation)
async def add_word_translation(message: types.Message, state: FSMContext):
    data = await state.get_data()
    translation = message.text.strip()

    success = add_word(data["article"], data["word"], translation)

    if success:
        await message.answer(
            f"✅ Добавлено: <b>{data['article']} {data['word']}</b> — {translation}",
            parse_mode="HTML",
            reply_markup=main_menu(get_role(message.from_user.username))
        )
    else:
        await message.answer(
            f"❌ Слово «{data['word']}» уже есть в словаре.",
            reply_markup=main_menu(get_role(message.from_user.username))
        )
    await state.clear()

@dp.message(F.text == "📚 Добавить ДЗ")
async def add_hw_start(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role not in ("admin", "teacher"):
        return
    await state.set_state(AddHW.text)
    await message.answer(
        "Напиши ДЗ на следующий урок и дату одним сообщением.\n"
        "Пример: <i>Упражнение 5 стр. 23, повторить слова. К 15.11</i>",
        parse_mode="HTML"
    )

@dp.message(AddHW.text)
async def add_hw_text(message: types.Message, state: FSMContext):
    add_homework(message.text.strip())
    await message.answer(
        "✅ ДЗ сохранено.",
        reply_markup=main_menu(get_role(message.from_user.username))
    )
    await state.clear()

@dp.message(F.text == "🔙 Назад")
async def back_to_main(message: types.Message):
    role = get_role(message.from_user.username)
    await message.answer("Главное меню", reply_markup=main_menu(role))

# ====================== ЗАПУСК ======================
async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
