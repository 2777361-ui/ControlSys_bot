"""
Обработчики школьного бота: родители (мои дети, баланс столовой, платежи, мероприятия),
добавление второго родителя (мама/папа) без обращения в бухгалтерию.
Оплата через Telegram Stars (комиссия +35%) и платёжные системы из конфигурации.
"""
import logging
import math
import re

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from bot.config import (
    get_payment_provider_currency,
    get_payment_provider_token,
    get_stars_commission_percent,
    get_stars_rub_rate,
)
from bot.keyboards.school import (
    CB_ADD_PARENT,
    CB_I_PAID,
    CB_PAY_METHOD,
    CB_PAY_PURPOSE,
    CB_PAY_STUDENT,
    BTN_MY_CHILDREN,
    BTN_CANTEEN_BALANCE,
    BTN_PAYMENTS,
    BTN_EVENTS,
    BTN_ADD_PARENT,
    BTN_I_PAID,
    BTN_PAY,
    get_parent_keyboard,
    get_add_parent_buttons,
    get_pay_method_buttons,
    get_pay_purpose_buttons,
    get_student_buttons,
)
from bot.keyboards.school import get_tasks_staff_keyboard
from bot.school_db import (
    ROLE_PARENT,
    balance_canteen_for_student,
    events_list,
    parent_report_payment_create,
    payment_confirm,
    payment_create,
    payment_purpose_list,
    payments_by_student,
    student_parent_can_manage,
    student_parents_add,
    students_by_parent_id,
    user_by_telegram_id,
    user_can_access_tasks,
    user_create,
)
from bot.utils.logging import audit_log

router = Router(name="school")
logger = logging.getLogger(__name__)


class AddParentStates(StatesGroup):
    """Состояния при добавлении второго родителя к ребёнку."""
    waiting_telegram_id = State()


class PayStates(StatesGroup):
    """Состояния при оплате (Stars или платёжная система)."""
    choosing_student = State()
    choosing_purpose = State()
    entering_amount = State()
    choosing_method = State()


def _format_payment_status(status: str) -> str:
    """Человекочитаемый статус платежа."""
    return {"pending": "⏳ Ожидает подтверждения", "confirmed": "✅ Подтверждён", "rejected": "❌ Отклонён"}.get(
        status, status
    )


def _format_purpose(purpose: str) -> str:
    """Название назначения из справочника (бухгалтер может добавлять варианты)."""
    from bot.school_db import payment_purpose_name_by_code
    name = payment_purpose_name_by_code(purpose)
    icons = {"education": "📚", "food": "🍽", "consumables": "📦", "extra_classes": "🎯"}
    prefix = icons.get(purpose, "•")
    return f"{prefix} {name}" if name else purpose


# --- Приветствие для родителя при /start (обрабатывается в start.py или здесь) ---
# Мы обрабатываем /start в school, если пользователь — родитель, показываем школьное меню


@router.message(F.text == "/start")
async def cmd_start_school(message: Message, school_user: dict | None) -> None:
    """Если пользователь — родитель в базе, показываем школьное меню. Иначе — подсказка."""
    if school_user and school_user.get("role") == ROLE_PARENT:
        audit_log(
            logger,
            "bot_start_parent",
            user_id=school_user["id"],
            role=ROLE_PARENT,
            extra={"telegram_id": message.from_user.id if message.from_user else None},
        )
        name = school_user.get("full_name", "Родитель")
        await message.answer(
            f"Здравствуйте, {name}!\n\n"
            "Здесь вы можете посмотреть информацию по вашим детям: баланс столовой, "
            "историю платежей и предстоящие мероприятия.",
            reply_markup=get_parent_keyboard(),
        )
        return
    # Директор/бухгалтер/админ/зам с доступом к делам — меню «Текущие дела» в боте
    if school_user and user_can_access_tasks(school_user["id"]):
        await message.answer(
            "Вы вошли как сотрудник школы. Здесь доступен раздел «Текущие дела»: просмотр списка, "
            "добавление дел и комментариев. Для полного управления используйте веб-интерфейс.",
            reply_markup=get_tasks_staff_keyboard(),
        )
        return
    # Остальные сотрудники (учитель, столовая без доступа к делам) — подсказка про веб
    if school_user and school_user.get("role") in ("director", "accountant", "teacher", "canteen", "administrator", "deputy_director"):
        await message.answer(
            "Вы вошли как сотрудник школы. Для управления данными используйте веб-интерфейс.",
        )
        return
    # Не в системе — создаём запись «родитель» по Telegram, чтобы второго родителя можно было привязать по ID
    user = message.from_user
    if user:
        telegram_id = user.id
        full_name = (user.full_name or "Родитель").strip() or "Родитель"
        try:
            user_create(role=ROLE_PARENT, full_name=full_name, telegram_id=telegram_id)
            audit_log(
                logger,
                "bot_register_parent",
                extra={"telegram_id": telegram_id, "full_name": full_name},
            )
        except Exception:
            pass  # уже есть или ошибка уникальности
        hint = f"\n\nВаш Telegram ID: <code>{telegram_id}</code> — передайте его в школу или второму родителю, чтобы вас привязали к ребёнку."
        await message.answer(
            "Вы зарегистрированы как родитель. Чтобы видеть данные по детям, вас должны привязать к ученику (в школе или через второго родителя в боте)."
            + hint,
            reply_markup=get_parent_keyboard(),
        )
    else:
        await message.answer("Вас нет в системе. Обратитесь в школу или войдите на сайте по email.")


# --- Родительское меню ---

@router.message(F.text == BTN_MY_CHILDREN)
async def btn_my_children(message: Message, school_user: dict | None) -> None:
    """Список детей родителя."""
    if not school_user or school_user.get("role") != ROLE_PARENT:
        await message.answer("Эта функция доступна только родителям. Войдите в систему.")
        return
    students = students_by_parent_id(school_user["id"])
    if not students:
        await message.answer("У вас пока не добавлены дети. Обратитесь в школу.")
        return
    lines = []
    for s in students:
        balance = balance_canteen_for_student(s["id"])
        lines.append(
            f"• {s['full_name']} — {school_db.format_class_grade(s['class_grade'])}. "
            f"Баланс столовой: {balance:.2f} ₽"
        )
    await message.answer("👶 Ваши дети:\n\n" + "\n\n".join(lines))


@router.message(F.text == BTN_CANTEEN_BALANCE)
async def btn_canteen_balance(message: Message, school_user: dict | None) -> None:
    """Баланс столовой: показываем по каждому ребёнку."""
    if not school_user or school_user.get("role") != ROLE_PARENT:
        await message.answer("Доступ только для родителей.")
        return
    students = students_by_parent_id(school_user["id"])
    if not students:
        await message.answer("У вас пока не добавлены дети.")
        return
    lines = []
    for s in students:
        balance = balance_canteen_for_student(s["id"])
        lines.append(f"• {s['full_name']} ({school_db.format_class_grade(s['class_grade'])}): {balance:.2f} ₽")
    await message.answer("🍽 Баланс столовой по вашим детям:\n\n" + "\n".join(lines))


@router.message(F.text == BTN_PAYMENTS)
async def btn_payments(message: Message, school_user: dict | None) -> None:
    """История платежей по всем детям родителя."""
    if not school_user or school_user.get("role") != ROLE_PARENT:
        await message.answer("Доступ только для родителей.")
        return
    students = students_by_parent_id(school_user["id"])
    if not students:
        await message.answer("У вас пока не добавлены дети.")
        return
    for s in students:
        payments = payments_by_student(s["id"])
        if not payments:
            await message.answer(f"📋 {s['full_name']} — платежей пока нет.")
            continue
        parts = [f"📋 История платежей: {s['full_name']}\n"]
        for p in payments[:15]:  # последние 15
            date = p.get("created_at", "")[:10] if p.get("created_at") else ""
            parts.append(
                f"• {date} — {p['amount']:.2f} ₽ ({_format_purpose(p['purpose'])}) — "
                f"{_format_payment_status(p['status'])}"
            )
        await message.answer("\n".join(parts))


# --- Добавить второго родителя (мама/папа) без бухгалтерии ---

# --- Я совершил платёж (сообщение бухгалтеру и директору) ---

@router.message(F.text == BTN_I_PAID)
async def btn_i_paid(message: Message, school_user: dict | None) -> None:
    """Родитель нажал «Я совершил платёж» — выбирает ребёнка, сообщение уходит бухгалтеру и директору."""
    if not school_user or school_user.get("role") != ROLE_PARENT:
        await message.answer("Доступ только для родителей.")
        return
    students = students_by_parent_id(school_user["id"])
    if not students:
        await message.answer("У вас пока не добавлены дети.")
        return
    if len(students) == 1:
        parent_report_payment_create(school_user["id"], students[0]["id"])
        audit_log(
            logger,
            "bot_i_paid",
            user_id=school_user["id"],
            role=ROLE_PARENT,
            extra={"student_id": students[0]["id"], "student_name": students[0]["full_name"]},
        )
        await message.answer(
            f"Сообщение передано бухгалтерии и директору по ученику {students[0]['full_name']}. "
            "Если платёж ещё не отражён в системе (например, оплатили в кассу и забыли внести), его проверят и внесут."
        )
        return
    await message.answer(
        "Выберите ученика, по которому вы сообщаете о платеже:",
        reply_markup=get_student_buttons(students, callback_prefix=CB_I_PAID),
    )


@router.callback_query(F.data.startswith(CB_I_PAID))
async def cb_i_paid_student(callback: CallbackQuery, school_user: dict | None) -> None:
    """Родитель выбрал ученика в «Я совершил платёж» — создаём отчёт и уведомляем."""
    await callback.answer()
    if not school_user or school_user.get("role") != ROLE_PARENT:
        await callback.message.answer("Доступ только для родителей.")
        return
    try:
        student_id = int(callback.data.replace(CB_I_PAID, "").strip())
    except ValueError:
        return
    if not student_parent_can_manage(student_id, school_user["id"]):
        await callback.message.answer("Вы не можете отправить сообщение по этому ученику.")
        return
    parent_report_payment_create(school_user["id"], student_id)
    audit_log(
        logger,
        "bot_i_paid",
        user_id=school_user["id"],
        role=ROLE_PARENT,
        extra={"student_id": student_id},
    )
    from bot.school_db import student_by_id
    s = student_by_id(student_id)
    name = s["full_name"] if s else "ученик"
    await callback.message.answer(
        f"Сообщение передано бухгалтерии и директору по ученику {name}. "
        "Если платёж ещё не отражён в системе (например, оплатили в кассу и забыли внести), его проверят и внесут."
    )


# --- Оплата (Telegram Stars + платёжные системы) ---

def _make_payload(student_id: int, purpose: str, amount_rub: float, user_id: int, method: str) -> str:
    """Payload для инвойса (1–128 байт): для восстановления данных при successful_payment."""
    # amount в копейках (целое), укладываемся в 128 байт
    amount_kopecks = int(round(amount_rub * 100))
    s = f"s{student_id}p{purpose}a{amount_kopecks}u{user_id}m{method}"
    return s[:128]


def _parse_payload(payload: str) -> dict | None:
    """Разбор payload: student_id, purpose, amount_rub, user_id, method. Формат: s12pfooda100000u5mstars."""
    try:
        m = re.match(r"s(\d+)p([a-z_]+)a(\d+)u(\d+)m(\w+)$", payload.strip())
        if m:
            student_id, purpose, amount_kopecks, user_id, method = m.groups()
            return {
                "student_id": int(student_id),
                "purpose": purpose,
                "amount_kopecks": int(amount_kopecks),
                "amount_rub": int(amount_kopecks) / 100.0,
                "user_id": int(user_id),
                "method": method,
            }
    except (ValueError, IndexError):
        pass
    return None


@router.message(F.text == BTN_PAY)
async def btn_pay(message: Message, school_user: dict | None, state: FSMContext) -> None:
    """Родитель нажал «Оплатить» — выбор ребёнка, назначение, сумма, способ (Stars / карта)."""
    if not school_user or school_user.get("role") != ROLE_PARENT:
        await message.answer("Доступ только для родителей.")
        return
    await state.clear()
    students = students_by_parent_id(school_user["id"])
    if not students:
        await message.answer("У вас пока не добавлены дети.")
        return
    await state.set_state(PayStates.choosing_student)
    await state.update_data(pay_user_id=school_user["id"])
    await message.answer(
        "Выберите ученика, на счёт которого пополнить:",
        reply_markup=get_student_buttons(students, callback_prefix=CB_PAY_STUDENT),
    )


@router.callback_query(F.data.startswith(CB_PAY_STUDENT), PayStates.choosing_student)
async def cb_pay_student(callback: CallbackQuery, school_user: dict | None, state: FSMContext) -> None:
    """Выбран ученик — показываем выбор назначения платежа."""
    await callback.answer()
    if not school_user or school_user.get("role") != ROLE_PARENT:
        return
    try:
        student_id = int(callback.data.replace(CB_PAY_STUDENT, "").strip())
    except ValueError:
        return
    if not student_parent_can_manage(student_id, school_user["id"]):
        await callback.message.answer("Вы не можете оплачивать за этого ученика.")
        await state.clear()
        return
    purposes = payment_purpose_list()
    if not purposes:
        await callback.message.answer("Нет доступных назначений платежа. Обратитесь в школу.")
        await state.clear()
        return
    await state.update_data(pay_student_id=student_id)
    await state.set_state(PayStates.choosing_purpose)
    await callback.message.answer(
        "Выберите назначение платежа:",
        reply_markup=get_pay_purpose_buttons(purposes),
    )


@router.callback_query(F.data.startswith(CB_PAY_PURPOSE), PayStates.choosing_purpose)
async def cb_pay_purpose(callback: CallbackQuery, school_user: dict | None, state: FSMContext) -> None:
    """Выбрано назначение — запрашиваем сумму (руб)."""
    await callback.answer()
    if not school_user or school_user.get("role") != ROLE_PARENT:
        return
    purpose = callback.data.replace(CB_PAY_PURPOSE, "").strip()
    if not purpose:
        return
    await state.update_data(pay_purpose=purpose)
    await state.set_state(PayStates.entering_amount)
    await callback.message.answer(
        "Введите сумму в рублях (целое число или с копейками, например 500 или 1250.50):"
    )


@router.message(PayStates.entering_amount, F.text)
async def pay_amount_entered(message: Message, school_user: dict | None, state: FSMContext) -> None:
    """Родитель ввёл сумму — показываем способ оплаты (Stars / карта) и отправляем инвойс."""
    if not school_user or school_user.get("role") != ROLE_PARENT:
        await state.clear()
        return
    text = (message.text or "").strip().replace(",", ".")
    try:
        amount_rub = float(text)
    except ValueError:
        await message.answer("Введите число — сумму в рублях (например 500 или 1000.50).")
        return
    if amount_rub < 1:
        await message.answer("Сумма должна быть не менее 1 ₽.")
        return
    if amount_rub > 1_000_000:
        await message.answer("Сумма слишком большая. Введите до 1 000 000 ₽.")
        return
    await state.update_data(pay_amount_rub=amount_rub)
    await state.set_state(PayStates.choosing_method)
    await message.answer(
        f"Сумма: {amount_rub:.2f} ₽.\n\nРаздел оплаты картами пока в разработке. Доступна оплата Stars.\nВыберите способ:",
        reply_markup=get_pay_method_buttons(),
    )


@router.callback_query(F.data.startswith(CB_PAY_METHOD), PayStates.choosing_method)
async def cb_pay_method(
    callback: CallbackQuery, school_user: dict | None, state: FSMContext, bot: Bot
) -> None:
    """Выбран способ — отправляем инвойс (Stars с комиссией +35% или провайдер)."""
    await callback.answer()
    if not school_user or school_user.get("role") != ROLE_PARENT:
        await state.clear()
        return
    method = callback.data.replace(CB_PAY_METHOD, "").strip()
    if method not in ("stars", "provider"):
        return
    data = await state.get_data()
    student_id = data.get("pay_student_id")
    purpose = data.get("pay_purpose")
    amount_rub = data.get("pay_amount_rub")
    user_id = data.get("pay_user_id")
    if not all([student_id, purpose, amount_rub is not None, user_id]):
        await callback.message.answer("Сессия истекла. Начните заново: «Оплатить».")
        await state.clear()
        return
    # Оплата картой пока в разработке — не даём доступ, только сообщение
    if method == "provider":
        await callback.answer("Оплата картами пока в разработке.", show_alert=True)
        return
    from bot.school_db import student_by_id, payment_purpose_name_by_code
    student = student_by_id(student_id)
    student_name = student.get("full_name", "Ученик") if student else "Ученик"
    purpose_name = payment_purpose_name_by_code(purpose)
    payload = _make_payload(student_id, purpose, amount_rub, user_id, method)
    title = f"Оплата: {purpose_name}"
    description = f"{student_name}. Сумма: {amount_rub:.2f} ₽."

    try:
        if method == "stars":
            # Telegram Stars: сумма в звёздах; комиссия +35%
            rate = get_stars_rub_rate()
            commission = get_stars_commission_percent() / 100.0
            # Родитель платит: amount_rub в рублях → переводим в звёзды, добавляем комиссию
            stars_base = amount_rub / rate
            stars_with_commission = int(math.ceil(stars_base * (1 + commission)))
            if stars_with_commission < 1:
                stars_with_commission = 1
            description += f" С учётом комиссии {get_stars_commission_percent()}%: {stars_with_commission} ⭐."
            await bot.send_invoice(
                chat_id=callback.message.chat.id,
                title=title,
                description=description[:255],
                payload=payload,
                currency="XTR",
                prices=[LabeledPrice(label=f"{purpose_name} ({amount_rub:.0f} ₽ + комиссия)", amount=stars_with_commission)],
                provider_token="",
            )
        else:
            # Платёжная система: сумма в копейках (или центы)
            currency = get_payment_provider_currency()
            if currency == "RUB":
                amount_units = int(round(amount_rub * 100))  # копейки
            else:
                amount_units = int(round(amount_rub * 100))  # центы для USD
            if amount_units < 1:
                amount_units = 1
            await bot.send_invoice(
                chat_id=callback.message.chat.id,
                title=title,
                description=description[:255],
                payload=payload,
                currency=currency,
                prices=[LabeledPrice(label=f"{purpose_name} — {amount_rub:.2f} ₽", amount=amount_units)],
                provider_token=get_payment_provider_token(),
            )
        await state.clear()
        await callback.message.answer("Проверьте открывшийся счёт и оплатите.")
    except Exception as e:
        logger.exception("Ошибка отправки инвойса: %s", e)
        await callback.message.answer("Не удалось создать счёт. Попробуйте позже или обратитесь в школу.")
        await state.clear()


@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout: PreCheckoutQuery, bot: Bot) -> None:
    """Подтверждаем приём платежа (обязательно ответить ok)."""
    await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, school_user: dict | None) -> None:
    """После успешной оплаты создаём платёж в БД и уведомляем родителя."""
    sp = message.successful_payment
    if not sp or not sp.invoice_payload:
        return
    data = _parse_payload(sp.invoice_payload)
    if not data:
        await message.answer("Платёж принят, но не удалось записать данные. Обратитесь в школу с номером счёта.")
        return
    student_id = data["student_id"]
    purpose = data["purpose"]
    amount_rub = data["amount_rub"]
    user_id = data["user_id"]
    method = data["method"]
    payment_type = "telegram_stars" if method == "stars" else "telegram_provider"
    try:
        payment_id = payment_create(
            student_id=student_id,
            amount=amount_rub,
            purpose=purpose,
            description=f"Оплата через Telegram ({'Stars' if method == 'stars' else 'платёжная система'})",
            payment_type=payment_type,
        )
        # Подтверждаем сразу: оплата уже прошла через Telegram
        payment_confirm(payment_id, user_id, "Оплата через Telegram")
        audit_log(
            logger,
            "bot_payment_success",
            user_id=user_id,
            role=ROLE_PARENT,
            extra={"student_id": student_id, "amount": amount_rub, "purpose": purpose, "method": method},
        )
        await message.answer(
            f"✅ Оплата прошла успешно. {amount_rub:.2f} ₽ зачислены на счёт ученика. "
            "Баланс обновится после обработки."
        )
    except Exception as e:
        logger.exception("Ошибка записи платежа после оплаты: %s", e)
        await message.answer("Оплата получена, но при записи возникла ошибка. Обратитесь в школу с номером счёта.")


@router.message(F.text == BTN_ADD_PARENT)
async def btn_add_parent(message: Message, school_user: dict | None, state: FSMContext) -> None:
    """Показать список детей и кнопки «добавить маму» / «добавить папу»."""
    if not school_user or school_user.get("role") != ROLE_PARENT:
        await message.answer("Доступ только для родителей.")
        return
    await state.clear()
    students = students_by_parent_id(school_user["id"])
    if not students:
        await message.answer("У вас пока не добавлены дети.")
        return
    await message.answer(
        "Выберите ребёнка и кого добавить (маму или папу). "
        "Второй родитель должен сначала написать боту /start — тогда вы сможете ввести его Telegram ID.",
        reply_markup=get_add_parent_buttons(students),
    )


@router.callback_query(F.data.startswith(CB_ADD_PARENT))
async def cb_add_parent_start(callback: CallbackQuery, school_user: dict | None, state: FSMContext) -> None:
    """Нажатие «добавить маму»/«добавить папу» — запрашиваем Telegram ID."""
    await callback.answer()
    if not school_user or school_user.get("role") != ROLE_PARENT:
        await callback.message.answer("Доступ только для родителей.")
        return
    parts = callback.data.replace(CB_ADD_PARENT, "").split(":")
    if len(parts) != 2:
        return
    try:
        student_id = int(parts[0])
        parent_role = parts[1] if parts[1] in ("mom", "dad") else "guardian"
    except ValueError:
        return
    if not student_parent_can_manage(student_id, school_user["id"]):
        await callback.message.answer("Вы не можете добавлять родителей к этому ученику.")
        return
    await state.update_data(add_parent_student_id=student_id, add_parent_role=parent_role)
    await state.set_state(AddParentStates.waiting_telegram_id)
    role_text = "маму" if parent_role == "mom" else "папу"
    await callback.message.answer(
        f"Введите Telegram ID второго родителя ({role_text}). "
        "Он может узнать его, написав боту /start — в ответе бот покажет его ID."
    )


@router.message(AddParentStates.waiting_telegram_id, F.text)
async def add_parent_telegram_id(message: Message, school_user: dict | None, state: FSMContext) -> None:
    """Пользователь ввёл Telegram ID — привязываем второго родителя к ученику."""
    if not school_user or school_user.get("role") != ROLE_PARENT:
        await state.clear()
        return
    data = await state.get_data()
    student_id = data.get("add_parent_student_id")
    parent_role = data.get("add_parent_role", "guardian")
    await state.clear()
    if not student_id or not student_parent_can_manage(student_id, school_user["id"]):
        await message.answer("Сессия истекла. Начните заново: «Добавить второго родителя».")
        return
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("Введите число — Telegram ID (только цифры).")
        return
    telegram_id = int(text)
    if telegram_id == message.from_user.id:
        await message.answer("Это ваш собственный ID. Введите Telegram ID второго родителя.")
        return
    other = user_by_telegram_id(telegram_id)
    if not other:
        await message.answer(
            "Пользователь с таким Telegram ID не найден в системе. "
            "Пусть второй родитель сначала напишет боту /start — тогда его добавят в базу и покажут ID."
        )
        return
    if student_parents_add(student_id, other["id"], parent_role):
        audit_log(
            logger,
            "bot_add_second_parent",
            user_id=school_user["id"],
            role=ROLE_PARENT,
            extra={"student_id": student_id, "added_user_id": other["id"], "parent_role": parent_role},
        )
        role_text = "маму" if parent_role == "mom" else "папу"
        await message.answer(f"Готово. Второй родитель ({other.get('full_name', '')}) добавлен как {role_text}.")
    else:
        await message.answer("Этот пользователь уже привязан к этому ребёнку.")


@router.message(F.text == BTN_EVENTS)
async def btn_events(message: Message) -> None:
    """Список мероприятий школы (доступно всем)."""
    events = events_list()
    if not events:
        await message.answer("Пока нет запланированных мероприятий.")
        return
    parts = ["📅 Мероприятия школы:\n"]
    for e in events[:20]:
        date = e.get("event_date") or "—"
        amount = e.get("amount_required")
        amount_str = f", взнос: {amount:.2f} ₽" if amount else ""
        parts.append(f"• {e['name']} — {date}{amount_str}")
    await message.answer("\n".join(parts))
