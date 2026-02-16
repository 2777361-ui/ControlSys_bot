"""
Режим чата с ИИ (OpenRouter).
Команда /chat — вход в режим, сообщения уходят в LLM. /exit — выход.
"""
import asyncio

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.config import get_openrouter_api_key
from bot.keyboards.common import BTN_CHAT
from bot.services.llm import chat_completion

router = Router(name="chat")


class ChatState(StatesGroup):
    """Состояние «в чате с ИИ»."""
    chatting = State()


CHAT_ENTER = (
    "Режим чата с ИИ включён. Пиши сообщения — буду отвечать как нейросеть.\n"
    "Выйти: /exit или снова /chat."
)
CHAT_NO_KEY = "Режим чата с ИИ не настроен: в .env нужен OPENROUTER_API_KEY."
CHAT_EXIT = "Режим чата с ИИ выключен. Пиши снова /chat, чтобы вернуться."
MAX_HISTORY_PAIRS = 10  # храним последние 10 пар user/assistant для контекста
TYPING_INTERVAL = 4  # секунд между отправками «печатает» (в Telegram статус живёт ~5 сек)


async def _typing_until_done(bot, chat_id: int, done: asyncio.Event) -> None:
    """В фоне шлёт «печатает» сразу и потом каждые TYPING_INTERVAL сек, пока не установят done."""
    while True:
        if done.is_set():
            break
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        try:
            await asyncio.wait_for(done.wait(), timeout=TYPING_INTERVAL)
        except asyncio.TimeoutError:
            pass  # цикл повторится и отправит typing снова


@router.message(F.text.in_(["/chat", BTN_CHAT]))
async def cmd_chat(message: Message, state: FSMContext) -> None:
    """Вход в режим чата с ИИ или выход, если уже в чате."""
    api_key = get_openrouter_api_key()
    if not api_key:
        await message.answer(CHAT_NO_KEY)
        return

    current = await state.get_state()
    if current == ChatState.chatting.state:
        await state.clear()
        await message.answer(CHAT_EXIT)
        return

    await state.set_state(ChatState.chatting)
    await state.set_data({"history": []})
    await message.answer(CHAT_ENTER)


@router.message(F.text == "/exit")
async def cmd_exit_chat(message: Message, state: FSMContext) -> None:
    """Выход из режима чата."""
    current = await state.get_state()
    if current != ChatState.chatting.state:
        await message.answer("Ты не в режиме чата с ИИ. Войти: /chat")
        return
    await state.clear()
    await message.answer(CHAT_EXIT)


@router.message(ChatState.chatting, F.text)
async def chat_message(message: Message, state: FSMContext) -> None:
    """В режиме чата отправляем текст в LLM и отвечаем."""
    api_key = get_openrouter_api_key()
    if not api_key:
        await state.clear()
        await message.answer(CHAT_NO_KEY)
        return

    user_text = (message.text or "").strip()
    if not user_text:
        await message.answer("Напиши текст сообщения.")
        return

    # Сначала текст «Думаю…» (из‑за него пропадёт «печатает», поэтому запускаем цикл после)
    wait = await message.answer("Думаю…")

    # Фоновая задача: раз в TYPING_INTERVAL шлёт «печатает», пока не скажем стоп
    typing_done = asyncio.Event()
    typing_task = asyncio.create_task(
        _typing_until_done(message.bot, message.chat.id, typing_done)
    )

    data = await state.get_data()
    history: list[dict[str, str]] = data.get("history") or []

    try:
        reply = await chat_completion(api_key, user_text, history=history)
    except Exception as e:
        reply = f"Ошибка запроса к ИИ: {e!s}"
        # историю не обновляем при ошибке
    finally:
        # Останавливаем цикл «печатает» (без cancel — иначе задача успевает отправить лишний typing)
        typing_done.set()
        await typing_task

    # Ответ ИИ — обычный текст, без HTML (чтобы < > и т.д. не ломали сообщение)
    await wait.edit_text(
        reply[:4000] if len(reply) > 4000 else reply,
        parse_mode=None,
    )

    # Добавляем в историю для контекста следующего запроса
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": reply})
    if len(history) > MAX_HISTORY_PAIRS * 2:
        history = history[-MAX_HISTORY_PAIRS * 2 :]
    await state.update_data(history=history)
