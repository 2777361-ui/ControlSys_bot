"""
Клавиатуры для школьного бота: родительское меню и кнопки по платежам/балансу.
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from bot import school_db


# Тексты кнопок для родителей
BTN_MY_CHILDREN = "👶 Мои дети"
BTN_CANTEEN_BALANCE = "🍽 Баланс столовой"
BTN_PAYMENTS = "📋 История платежей"
BTN_EVENTS = "📅 Мероприятия"
BTN_ADD_PARENT = "➕ Добавить второго родителя"
BTN_I_PAID = "💳 Я совершил платёж"
BTN_PAY = "⭐ Оплатить"
BTN_BACK = "◀️ Назад"

# Текущие дела (для сотрудников с доступом)
BTN_TASKS = "📋 Текущие дела"
BTN_ADD_TASK = "➕ Добавить дело"

# Префиксы callback_data
CB_STUDENT = "school:student:"
CB_PAYMENT = "school:payment:"
CB_EVENT = "school:event:"
CB_ADD_PARENT = "school:addparent:"   # + student_id:role (mom/dad)
CB_I_PAID = "school:ipaid:"           # + student_id
CB_PAY_STUDENT = "school:pay_student:"  # + student_id
CB_PAY_PURPOSE = "school:pay_purpose:"  # + purpose_code
CB_PAY_METHOD = "school:pay_method:"    # stars | provider
CB_TASK_OPEN = "task:open:"
CB_TASK_COMMENT = "task:comment:"
CB_TASK_BACK_LIST = "task:back"


def get_parent_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню для родителя: дети, столовая, платежи, мероприятия, добавить родителя."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MY_CHILDREN)],
            [
                KeyboardButton(text=BTN_CANTEEN_BALANCE),
                KeyboardButton(text=BTN_PAYMENTS),
            ],
            [KeyboardButton(text=BTN_EVENTS)],
            [KeyboardButton(text=BTN_I_PAID), KeyboardButton(text=BTN_ADD_PARENT)],
            [KeyboardButton(text=BTN_PAY)],
        ],
        resize_keyboard=True,
    )


def get_student_buttons(students: list[dict], callback_prefix: str = None) -> InlineKeyboardMarkup:
    """Инлайн-кнопки: выбор ученика по id. callback_prefix: CB_STUDENT или CB_I_PAID."""
    prefix = callback_prefix or CB_STUDENT
    buttons = [
        [InlineKeyboardButton(
            text=f"{s['full_name']} ({school_db.format_class_grade(s['class_grade'])})",
            callback_data=f"{prefix}{s['id']}",
        )]
        for s in students
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_back_button(callback_data: str = "school:back") -> InlineKeyboardMarkup:
    """Кнопка «Назад» в инлайн-меню."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BTN_BACK, callback_data=callback_data)]]
    )


def get_pay_purpose_buttons(purposes: list[dict]) -> InlineKeyboardMarkup:
    """Инлайн-кнопки выбора назначения платежа (Обучение, Питание и т.д.)."""
    buttons = [
        [InlineKeyboardButton(text=p.get("name", p.get("code", "")), callback_data=f"{CB_PAY_PURPOSE}{p.get('code', '')}")]
        for p in purposes
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_pay_method_buttons() -> InlineKeyboardMarkup:
    """Кнопки способа оплаты: Stars (активна); карта — пока в разработке (серый, без доступа)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"{CB_PAY_METHOD}stars")],
        [InlineKeyboardButton(text="💳 Карта (в разработке)", callback_data=f"{CB_PAY_METHOD}provider")],
    ])


def get_add_parent_buttons(students: list[dict]) -> InlineKeyboardMarkup:
    """Кнопки: выбор ребёнка и роли (добавить маму / добавить папу)."""
    buttons = []
    for s in students:
        buttons.append([
            InlineKeyboardButton(text=f"{s['full_name']} — добавить маму", callback_data=f"{CB_ADD_PARENT}{s['id']}:mom"),
            InlineKeyboardButton(text=f"{s['full_name']} — добавить папу", callback_data=f"{CB_ADD_PARENT}{s['id']}:dad"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_tasks_staff_keyboard() -> ReplyKeyboardMarkup:
    """Меню для сотрудника с доступом к текущим делам: список дел и добавить дело."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TASKS), KeyboardButton(text=BTN_ADD_TASK)],
        ],
        resize_keyboard=True,
    )


def get_task_list_inline(tasks: list[dict]) -> InlineKeyboardMarkup:
    """Инлайн-кнопки: список задач — открыть задачу или добавить комментарий."""
    buttons = []
    for t in tasks[:25]:  # не более 25 кнопок
        title_short = (t.get("title") or "Без названия")[:30]
        buttons.append([
            InlineKeyboardButton(text=f"📌 {title_short}", callback_data=f"{CB_TASK_OPEN}{t['id']}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_task_detail_inline(task_id: int) -> InlineKeyboardMarkup:
    """Кнопки на странице задачи: добавить комментарий, назад к списку."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Добавить комментарий", callback_data=f"{CB_TASK_COMMENT}{task_id}")],
            [InlineKeyboardButton(text=BTN_BACK + " к списку", callback_data=CB_TASK_BACK_LIST)],
        ]
    )
