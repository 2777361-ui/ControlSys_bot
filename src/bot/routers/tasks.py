"""
Роутер «Текущие дела» в боте: для пользователей с доступом к разделу — список дел,
просмотр задачи с комментариями, добавление комментария, создание нового дела.
"""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.keyboards.school import (
    BTN_ADD_TASK,
    BTN_TASKS,
    CB_TASK_BACK_LIST,
    CB_TASK_COMMENT,
    CB_TASK_OPEN,
    get_task_detail_inline,
    get_task_list_inline,
    get_tasks_staff_keyboard,
)
from bot.school_db import (
    task_by_id,
    task_comment_add,
    task_comments_list,
    task_create,
    task_list,
    user_can_access_tasks,
)
from bot.utils.logging import audit_log

router = Router(name="tasks")
logger = logging.getLogger(__name__)

# Состояния при добавлении нового дела
class AddTaskStates(StatesGroup):
    title = State()
    description = State()
    contact_info = State()


# Состояния при добавлении комментария к задаче
class AddCommentStates(StatesGroup):
    comment_text = State()


def _status_label(status: str) -> str:
    """Человекочитаемый статус задачи."""
    return {"open": "🟢 Открыто", "in_progress": "🟡 В работе", "done": "✅ Выполнено"}.get(status, status)


def _check_tasks_access(school_user: dict | None) -> bool:
    """Проверка доступа к текущим делам по данным пользователя из БД."""
    if not school_user:
        return False
    return user_can_access_tasks(school_user["id"])


@router.message(F.text == BTN_TASKS)
async def btn_tasks_list(message: Message, school_user: dict | None, state: FSMContext) -> None:
    """Показать список текущих дел (только для пользователей с доступом)."""
    await state.clear()
    if not _check_tasks_access(school_user):
        await message.answer("У вас нет доступа к разделу «Текущие дела».")
        return
    tasks = task_list()
    if not tasks:
        await message.answer(
            "📋 Текущие дела\n\nПока нет ни одного дела. Нажмите «➕ Добавить дело», чтобы создать.",
            reply_markup=get_tasks_staff_keyboard(),
        )
        return
    lines = ["📋 Текущие дела:\n"]
    for t in tasks[:20]:
        status = _status_label(t.get("status", "open"))
        lines.append(f"• {t.get('title', '—')} — {status}")
    text = "\n".join(lines)
    if len(tasks) > 20:
        text += f"\n\n... и ещё {len(tasks) - 20} дел. Выберите ниже для просмотра."
    await message.answer(text, reply_markup=get_task_list_inline(tasks))


@router.callback_query(F.data == CB_TASK_BACK_LIST)
async def cb_task_back(callback: CallbackQuery, school_user: dict | None) -> None:
    """Вернуться к списку дел."""
    if not _check_tasks_access(school_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    tasks = task_list()
    if not tasks:
        await callback.message.edit_text(
            "📋 Текущие дела\n\nПока нет ни одного дела. Добавьте дело через кнопку «➕ Добавить дело»."
        )
        await callback.answer()
        return
    lines = ["📋 Текущие дела:\n"]
    for t in tasks[:20]:
        status = _status_label(t.get("status", "open"))
        lines.append(f"• {t.get('title', '—')} — {status}")
    text = "\n".join(lines)
    if len(tasks) > 20:
        text += f"\n\n... и ещё {len(tasks) - 20} дел."
    await callback.message.edit_text(text, reply_markup=get_task_list_inline(tasks))
    await callback.answer()


@router.callback_query(F.data.startswith(CB_TASK_OPEN))
async def cb_task_open(callback: CallbackQuery, school_user: dict | None) -> None:
    """Открыть задачу: показать описание и комментарии, кнопки «Добавить комментарий» и «Назад»."""
    if not _check_tasks_access(school_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    try:
        task_id = int(callback.data[len(CB_TASK_OPEN) :])
    except ValueError:
        await callback.answer("Ошибка.", show_alert=True)
        return
    task = task_by_id(task_id)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return
    status = _status_label(task.get("status", "open"))
    lines = [
        f"📌 {task.get('title', '—')}",
        f"Статус: {status}",
        f"Создал: {task.get('created_by_name', '—')}",
        "",
    ]
    if task.get("description"):
        lines.append("Описание:\n" + task["description"])
    if task.get("contact_info"):
        lines.append("\nКонтакты: " + task["contact_info"])
    comments = task_comments_list(task_id)
    if comments:
        lines.append("\n💬 Комментарии:")
        for c in comments[-10:]:  # последние 10
            author = c.get("author_name", "—")
            created = (c.get("created_at") or "")[:16].replace("T", " ")
            lines.append(f"  • {author} ({created}): {c.get('comment_text', '')}")
    text = "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=get_task_detail_inline(task_id))
    await callback.answer()


@router.callback_query(F.data.startswith(CB_TASK_COMMENT))
async def cb_task_comment_start(callback: CallbackQuery, school_user: dict | None, state: FSMContext) -> None:
    """Начать добавление комментария к задаче: запросить текст."""
    if not _check_tasks_access(school_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    try:
        task_id = int(callback.data[len(CB_TASK_COMMENT) :])
    except ValueError:
        await callback.answer("Ошибка.", show_alert=True)
        return
    task = task_by_id(task_id)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return
    await state.set_state(AddCommentStates.comment_text)
    await state.update_data(task_comment_task_id=task_id)
    await callback.message.answer("Напишите текст комментария к задаче «{}»:".format(task.get("title", "")[:50]))
    await callback.answer()


@router.message(AddCommentStates.comment_text, F.text)
async def task_comment_submit(message: Message, school_user: dict | None, state: FSMContext) -> None:
    """Сохранить комментарий к задаче и показать задачу с обновлёнными комментариями."""
    if not _check_tasks_access(school_user):
        await state.clear()
        return
    data = await state.get_data()
    task_id = data.get("task_comment_task_id")
    await state.clear()
    if not task_id:
        await message.answer("Сессия сброшена. Выберите задачу снова из списка «Текущие дела».")
        return
    task = task_by_id(task_id)
    if not task:
        await message.answer("Задача не найдена.")
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Комментарий не может быть пустым. Напишите текст.")
        return
    task_comment_add(task_id, school_user["id"], text)
    audit_log(
        logger,
        "bot_task_comment_add",
        user_id=school_user["id"],
        extra={"task_id": task_id},
    )
    # Показать задачу с обновлёнными комментариями
    status = _status_label(task.get("status", "open"))
    lines = [
        f"📌 {task.get('title', '—')}",
        f"Статус: {status}",
        f"Создал: {task.get('created_by_name', '—')}",
        "",
    ]
    if task.get("description"):
        lines.append("Описание:\n" + task["description"])
    if task.get("contact_info"):
        lines.append("\nКонтакты: " + task["contact_info"])
    comments = task_comments_list(task_id)
    if comments:
        lines.append("\n💬 Комментарии:")
        for c in comments[-10:]:
            author = c.get("author_name", "—")
            created = (c.get("created_at") or "")[:16].replace("T", " ")
            lines.append(f"  • {author} ({created}): {c.get('comment_text', '')}")
    await message.answer("✅ Комментарий добавлен.")
    await message.answer("\n".join(lines), reply_markup=get_task_detail_inline(task_id))


# --- Добавить дело (FSM: название → описание → контакты) ---

@router.message(F.text == BTN_ADD_TASK)
async def btn_add_task(message: Message, school_user: dict | None, state: FSMContext) -> None:
    """Начать создание нового дела: запросить название."""
    await state.clear()
    if not _check_tasks_access(school_user):
        await message.answer("У вас нет доступа к разделу «Текущие дела».")
        return
    await state.set_state(AddTaskStates.title)
    await message.answer("Введите название дела:")


@router.message(AddTaskStates.title, F.text)
async def add_task_title(message: Message, school_user: dict | None, state: FSMContext) -> None:
    """Приняли название — запрашиваем описание."""
    if not _check_tasks_access(school_user):
        await state.clear()
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("Название не может быть пустым. Введите название дела:")
        return
    await state.update_data(add_task_title=title)
    await state.set_state(AddTaskStates.description)
    await message.answer("Введите описание дела (можно кратко или «—» чтобы пропустить):")


@router.message(AddTaskStates.description, F.text)
async def add_task_description(message: Message, school_user: dict | None, state: FSMContext) -> None:
    """Приняли описание — запрашиваем контакты."""
    if not _check_tasks_access(school_user):
        await state.clear()
        return
    await state.update_data(add_task_description=(message.text or "").strip())
    await state.set_state(AddTaskStates.contact_info)
    await message.answer("Введите контактную информацию (телефон, почта или «—» чтобы пропустить):")


@router.message(AddTaskStates.contact_info, F.text)
async def add_task_contact_info(message: Message, school_user: dict | None, state: FSMContext) -> None:
    """Сохраняем дело и показываем список."""
    if not _check_tasks_access(school_user):
        await state.clear()
        return
    data = await state.get_data()
    await state.clear()
    title = (data.get("add_task_title") or "").strip()
    description = (data.get("add_task_description") or "").strip()
    contact_info = (message.text or "").strip()
    if not title:
        await message.answer("Ошибка: название не задано. Начните заново — нажмите «➕ Добавить дело».")
        return
    task_id = task_create(
        title=title,
        description=description or "",
        contact_info=contact_info or "",
        created_by_id=school_user["id"],
    )
    audit_log(
        logger,
        "bot_task_create",
        user_id=school_user["id"],
        extra={"task_id": task_id, "title": title[:50]},
    )
    await message.answer(
        f"✅ Дело «{title[:50]}» создано. Можете открыть его в списке «Текущие дела».",
        reply_markup=get_tasks_staff_keyboard(),
    )
