"""
Режим чата с ИИ (OpenRouter) с выбором режима работы.

Как это работает (для 5-классника):
  1. Пользователь нажимает /chat — видит 3 кнопки (режима).
  2. Нажимает на кнопку — бот запоминает выбранный режим.
  3. Пользователь пишет сообщения — бот отвечает по-разному в зависимости от режима:
     - «Ассистент»  — обычный ИИ-помощник.
     - «ASCII-арт»  — ИИ рисует картинку символами.
     - «Переводчик» — бот переводит текст с русского на английский.
  4. /exit — выход из чата.

Суперлогирование:
  - Логируем вход/выход, выбор режима
  - Логируем каждое сообщение пользователя
  - Логируем ответ ИИ (модель, текст)
  - Сохраняем всё в SQLite
"""
import asyncio
import logging

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import get_openrouter_api_key
from bot.database import save_message
from bot.keyboards.common import (
    BTN_CHAT,
    MODE_ASCII,
    MODE_ASSISTANT,
    MODE_TRANSLATE,
    get_chat_mode_keyboard,
)
from bot.services.llm import chat_completion

router = Router(name="chat")
logger = logging.getLogger(__name__)


# ─── FSM-состояния ───────────────────────────────────────────────────────────

class ChatState(StatesGroup):
    """
    Два состояния:
      choosing_mode — пользователь выбирает режим (видит инлайн-кнопки).
      chatting      — пользователь уже в чате, сообщения уходят в ИИ.
    """
    choosing_mode = State()
    chatting = State()


# ─── Системные промпты для каждого режима ─────────────────────────────────────
# Системный промпт — это скрытая инструкция для ИИ, которая задаёт «характер».

SYSTEM_PROMPTS: dict[str, str] = {
    "assistant": (
        "Ты — полезный ассистент. Отвечай на вопросы пользователя по делу, "
        "понятно и дружелюбно. Отвечай на том же языке, на котором задан вопрос."
    ),
    "ascii": (
        "Ты — художник ASCII-арта. На КАЖДЫЙ запрос пользователя ты ДОЛЖЕН нарисовать "
        "изображение из символов ASCII (текстовая графика). "
        "Сначала нарисуй ASCII-арт, а потом можешь добавить короткий комментарий. "
        "Используй только моноширинные символы. Старайся делать картинки красивыми и детальными."
    ),
    "translate": (
        "Ты — профессиональный переводчик с русского на английский. "
        "Пользователь пишет тебе на русском языке. Ты ДОЛЖЕН перевести его текст на английский язык. "
        "Не добавляй никаких пояснений, не отвечай на вопросы — только переводи. "
        "Перевод должен быть точным, литературным и естественным."
    ),
}

# Названия режимов для логов и сообщений пользователю
MODE_NAMES: dict[str, str] = {
    "assistant": "💬 Ассистент",
    "ascii": "🎨 ASCII-арт",
    "translate": "🌐 Переводчик (RU → EN)",
}


# ─── Текстовые константы ─────────────────────────────────────────────────────

CHAT_CHOOSE_MODE = "Выбери режим чата с ИИ:"
CHAT_NO_KEY = "Режим чата с ИИ не настроен: в .env нужен OPENROUTER_API_KEY."
CHAT_EXIT = "Режим чата с ИИ выключен. Пиши снова /chat, чтобы вернуться."
MAX_HISTORY_PAIRS = 10
TYPING_INTERVAL = 4


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def _user_tag(message: Message) -> str:
    """Строка для лога: user_id + username."""
    name = message.from_user.username if message.from_user else "?"
    uid = message.from_user.id if message.from_user else 0
    return f"user_id={uid} @{name}"


def _user_tag_cb(callback: CallbackQuery) -> str:
    """Строка для лога из callback-запроса."""
    user = callback.from_user
    return f"user_id={user.id} @{user.username}" if user else "user_id=? @?"


async def _typing_until_done(bot, chat_id: int, done: asyncio.Event) -> None:
    """В фоне шлёт «печатает» каждые TYPING_INTERVAL сек, пока не установят done."""
    while True:
        if done.is_set():
            break
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        try:
            await asyncio.wait_for(done.wait(), timeout=TYPING_INTERVAL)
        except asyncio.TimeoutError:
            pass


# ─── Команда /chat — показываем выбор режима ──────────────────────────────────

@router.message(F.text.in_(["/chat", BTN_CHAT]))
async def cmd_chat(message: Message, state: FSMContext) -> None:
    """Вход в режим чата: показываем инлайн-кнопки выбора режима."""
    tag = _user_tag(message)
    logger.info("[CHAT] %s — команда /chat", tag)

    api_key = get_openrouter_api_key()
    if not api_key:
        logger.warning("[CHAT] %s — OPENROUTER_API_KEY не задан", tag)
        await message.answer(CHAT_NO_KEY)
        return

    # Если уже в чате — выходим
    current = await state.get_state()
    if current == ChatState.chatting.state:
        logger.info("[CHAT] %s — выход из режима чата (повторный /chat)", tag)
        await state.clear()
        await message.answer(CHAT_EXIT)
        return

    # Показываем меню выбора режима
    await state.set_state(ChatState.choosing_mode)
    logger.info("[CHAT] %s — показываем выбор режима", tag)
    await message.answer(CHAT_CHOOSE_MODE, reply_markup=get_chat_mode_keyboard())


# ─── Callback: пользователь нажал кнопку режима ──────────────────────────────

@router.callback_query(F.data.startswith("chat_mode:"))
async def on_mode_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Обработчик нажатия инлайн-кнопки режима.
    callback.data = "chat_mode:assistant" / "chat_mode:ascii" / "chat_mode:translate"
    """
    tag = _user_tag_cb(callback)

    # Достаём ключ режима из callback_data (после двоеточия)
    mode_key = (callback.data or "").split(":")[-1]
    if mode_key not in SYSTEM_PROMPTS:
        logger.warning("[CHAT] %s — неизвестный режим: %s", tag, callback.data)
        await callback.answer("Неизвестный режим")
        return

    mode_name = MODE_NAMES.get(mode_key, mode_key)
    logger.info("[CHAT] %s — выбран режим: %s", tag, mode_name)

    # Сохраняем режим и переходим в состояние «chatting»
    await state.set_state(ChatState.chatting)
    await state.set_data({"history": [], "mode": mode_key})

    # Убираем инлайн-кнопки и показываем подтверждение
    enter_text = (
        f"Режим «{mode_name}» включён.\n"
        "Пиши сообщения — буду отвечать.\n"
        "Выйти: /exit или снова /chat."
    )
    await callback.message.edit_text(enter_text)
    await callback.answer()


# ─── Команда /exit ────────────────────────────────────────────────────────────

@router.message(F.text == "/exit")
async def cmd_exit_chat(message: Message, state: FSMContext) -> None:
    """Выход из режима чата."""
    tag = _user_tag(message)
    current = await state.get_state()
    if current not in (ChatState.chatting.state, ChatState.choosing_mode.state):
        logger.info("[CHAT] %s — /exit, но не в режиме чата", tag)
        await message.answer("Ты не в режиме чата с ИИ. Войти: /chat")
        return
    await state.clear()
    logger.info("[CHAT] %s — вышел из режима чата (/exit)", tag)
    await message.answer(CHAT_EXIT)


# ─── Сообщение в режиме чата ─────────────────────────────────────────────────

@router.message(ChatState.chatting, F.text)
async def chat_message(message: Message, state: FSMContext) -> None:
    """В режиме чата отправляем текст в LLM с учётом выбранного режима."""
    tag = _user_tag(message)
    user_id = message.from_user.id if message.from_user else 0
    username = message.from_user.username if message.from_user else None
    full_name = message.from_user.full_name if message.from_user else None

    api_key = get_openrouter_api_key()
    if not api_key:
        await state.clear()
        await message.answer(CHAT_NO_KEY)
        return

    user_text = (message.text or "").strip()
    if not user_text:
        await message.answer("Напиши текст сообщения.")
        return

    # Достаём режим и историю из состояния
    data = await state.get_data()
    mode_key: str = data.get("mode", "assistant")
    history: list[dict[str, str]] = data.get("history") or []
    system_prompt: str = SYSTEM_PROMPTS.get(mode_key, SYSTEM_PROMPTS["assistant"])

    logger.info(
        "[CHAT] %s — режим=%s, сообщение: %s",
        tag, mode_key, user_text[:300],
    )

    # Сохраняем сообщение пользователя в SQLite
    save_message(
        user_id=user_id,
        role="user",
        content=user_text,
        username=username,
        full_name=full_name,
    )

    # «Думаю…» + цикл typing
    wait = await message.answer("Думаю…")
    typing_done = asyncio.Event()
    typing_task = asyncio.create_task(
        _typing_until_done(message.bot, message.chat.id, typing_done)
    )

    model_used: str | None = None
    raw_response: str | None = None

    try:
        reply, model_used, raw_response = await chat_completion(
            api_key,
            user_text,
            history=history,
            system_prompt=system_prompt,
        )
    except Exception as e:
        reply = f"Ошибка запроса к ИИ: {e!s}"
        logger.error("[CHAT] %s — ошибка LLM: %s", tag, e)
    finally:
        typing_done.set()
        await typing_task

    logger.info(
        "[CHAT] %s — ответ ИИ (режим=%s, модель=%s, длина=%d): %s",
        tag, mode_key, model_used, len(reply), reply[:300],
    )

    # Ответ — обычный текст, без HTML
    await wait.edit_text(
        reply[:4000] if len(reply) > 4000 else reply,
        parse_mode=None,
    )

    # Сохраняем ответ ИИ в SQLite
    save_message(
        user_id=user_id,
        role="assistant",
        content=reply,
        username=username,
        full_name=full_name,
        model=model_used,
        raw_response=raw_response,
    )

    # Обновляем историю для контекста следующего запроса
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": reply})
    if len(history) > MAX_HISTORY_PAIRS * 2:
        history = history[-MAX_HISTORY_PAIRS * 2 :]
    await state.update_data(history=history)
