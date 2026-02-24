"""
Веб-интерфейс для школы: вход по email, управление платежами и учениками.

Запуск: из корня проекта выполнить
  uvicorn web.main:app --reload --host 0.0.0.0 --port 8000

Доступ: директор и бухгалтер входят по email/пароль. Родитель может войти,
чтобы привязать Telegram (или посмотреть данные).
"""
import logging
import os
import sys
import time
from pathlib import Path

# Чтобы импортировать bot.school_db
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi import FastAPI, File, Form, Query, Request, Response, Depends, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bot import school_db
from bot.utils.logging import audit_log, log_exception
from web.auth_utils import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)
from web.email_utils import get_smtp_config, send_password_reset_email

app = FastAPI(title="Школьная система")
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Логгер для веб-аудита и отладки (кто что нажимает, где ломается)
_web_logger = logging.getLogger("web.audit")

# Инициализация БД при старте; проверка WEB_SECRET_KEY в проде
_DEFAULT_SECRET = "school-bot-secret-change-in-production"


@app.on_event("startup")
def startup():
    school_db.init_db()
    secret = os.getenv("WEB_SECRET_KEY")
    if not secret or secret.strip() == "" or secret == _DEFAULT_SECRET:
        logging.getLogger("web").critical(
            "WEB_SECRET_KEY не задан или равен дефолту. В продакшене задайте свою случайную строку (например: openssl rand -hex 32)."
        )


@app.middleware("http")
async def request_audit_middleware(request: Request, call_next):
    """Супер-логирование: каждый запрос (кто, куда, метод), ответ (статус, время) и ошибки."""
    start = time.perf_counter()
    user_id = get_current_user_id(request)
    role = None
    if user_id:
        user = school_db.user_by_id(user_id)
        role = user.get("role") if user else None
    method = request.method
    path = request.url.path
    query = str(request.query_params) if request.query_params else ""
    # Не логируем тело с паролями
    audit_log(
        _web_logger,
        "request",
        user_id=user_id,
        role=role,
        extra={"method": method, "path": path, "query": query[:200]},
        message=f"{method} {path}",
    )
    try:
        response = await call_next(request)
        status = response.status_code
        duration_ms = (time.perf_counter() - start) * 1000
        audit_log(
            _web_logger,
            "response",
            user_id=user_id,
            role=role,
            extra={"method": method, "path": path, "status": status, "duration_ms": f"{duration_ms:.1f}"},
            message=f"{method} {path} -> {status} ({duration_ms:.0f}ms)",
        )
        return response
    except Exception as e:
        duration_ms = (time.perf_counter() - start) * 1000
        log_exception(
            _web_logger,
            f"Ошибка при обработке {method} {path} за {duration_ms:.0f}ms",
            user_id=user_id,
            path=path,
            exc=e,
        )
        raise


def _cookie_secure_samesite(request: Request) -> dict:
    """Параметры cookie: secure при HTTPS, samesite=lax (для сессии при работе по HTTP и HTTPS)."""
    return {
        "samesite": "lax",
        "secure": request.url.scheme == "https",
    }


@app.middleware("http")
async def theme_cookie_middleware(request: Request, call_next):
    """Подставляем cookie темы из БД, чтобы интерфейс применял выбранную тему."""
    response = await call_next(request)
    if request.cookies.get("session") and not request.cookies.get("theme"):
        try:
            payload = decode_token(request.cookies["session"])
            if payload and "sub" in payload:
                uid = int(payload["sub"])
                user = school_db.user_by_id(uid)
                if user and user.get("theme_preference"):
                    opts = _cookie_secure_samesite(request)
                    response.set_cookie(
                        "theme",
                        user["theme_preference"],
                        max_age=31536000,
                        path="/",
                        samesite=opts["samesite"],
                        secure=opts["secure"],
                    )
        except Exception:
            pass
    return response


def get_current_user_id(request: Request) -> int | None:
    """Читает JWT из cookie и возвращает user_id или None."""
    token = request.cookies.get("session")
    if not token:
        return None
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        return None
    try:
        return int(payload["sub"])
    except ValueError:
        return None


def _template_current_user(request: Request):
    """Контекст для шаблонов: текущий пользователь и права заместителя (для навигации)."""
    uid = get_current_user_id(request)
    if not uid:
        return {"current_user": None, "deputy_allowed": None}
    user = school_db.user_by_id(uid)
    deputy_allowed = None
    if user and user.get("role") == "deputy_director":
        deputy_allowed = set(school_db.deputy_permissions_list(uid))
    return {"current_user": user, "deputy_allowed": deputy_allowed}


def _redirect_302(url: str) -> None:
    """Редирект из зависимости: raise, чтобы не подставлять ответ в параметры маршрута."""
    raise HTTPException(status_code=302, detail="Redirect", headers={"Location": url})


def require_permission(permission_key: str):
    """Доступ: администратор/директор/бухгалтер всегда; заместитель директора — только если ему выдали это право."""
    def _dep(request: Request):
        uid = get_current_user_id(request)
        if not uid:
            _redirect_302("/login")
        user = school_db.user_by_id(uid)
        if not user:
            raise HTTPException(status_code=403, detail="Пользователь не найден")
        role = user.get("role")
        if role in ("administrator", "director", "accountant"):
            return uid
        if role == "deputy_director" and school_db.deputy_has_permission(uid, permission_key):
            return uid
        raise HTTPException(status_code=403, detail="Нет доступа к этому разделу")
    return _dep


def require_director_or_deputy(permission_key: str):
    """Доступ: только директор или администратор; либо заместитель с выданным правом (например report, departments)."""
    def _dep(request: Request):
        uid = get_current_user_id(request)
        if not uid:
            _redirect_302("/login")
        user = school_db.user_by_id(uid)
        if not user:
            raise HTTPException(status_code=403, detail="Пользователь не найден")
        role = user.get("role")
        if role in ("director", "administrator"):
            return uid
        if role == "deputy_director" and school_db.deputy_has_permission(uid, permission_key):
            return uid
        raise HTTPException(status_code=403, detail="Доступ только для директора или заместителя с правом")
    return _dep


templates = Jinja2Templates(
    directory=Path(__file__).parent / "templates",
    context_processors=[_template_current_user],
)


def _class_grade_display(class_grade) -> str:
    """Для шаблонов: 0 → «Детский сад», иначе «N класс»."""
    if class_grade == 0:
        return "Детский сад"
    return f"{class_grade} класс"


templates.env.filters["class_grade_display"] = lambda g: _class_grade_display(g)


def require_staff(request: Request):
    """Администратор, директор, бухгалтер или заместитель директора (с любыми правами для проверки настройки)."""
    uid = get_current_user_id(request)
    if not uid:
        _redirect_302("/login")
    user = school_db.user_by_id(uid)
    if not user or user.get("role") not in ("administrator", "director", "accountant", "deputy_director"):
        raise HTTPException(status_code=403, detail="Доступ только для сотрудников школы")
    return uid


def require_teacher_or_canteen(request: Request):
    """Учитель или столовая — для ввода данных по питанию; администратор/директор/бухгалтер тоже."""
    uid = get_current_user_id(request)
    if not uid:
        _redirect_302("/login")
    user = school_db.user_by_id(uid)
    if not user or user.get("role") not in ("teacher", "canteen", "administrator", "director", "accountant", "deputy_director"):
        raise HTTPException(status_code=403, detail="Доступ только для учителя или столовой")
    return uid


def require_director(request: Request):
    """Директор или администратор — для отчётов, отделов и прочих прав директора."""
    uid = get_current_user_id(request)
    if not uid:
        _redirect_302("/login")
    user = school_db.user_by_id(uid)
    if not user or user.get("role") not in ("director", "administrator"):
        raise HTTPException(status_code=403, detail="Доступ только для директора или администратора")
    return uid


def require_can_broadcast(request: Request):
    """Администратор, директор, бухгалтер, учитель или заместитель директора с правом «Рассылки»."""
    uid = get_current_user_id(request)
    if not uid:
        _redirect_302("/login")
    user = school_db.user_by_id(uid)
    if not user:
        raise HTTPException(status_code=403, detail="Пользователь не найден")
    role = user.get("role")
    if role in ("administrator", "director", "accountant", "teacher"):
        return uid
    if role == "deputy_director" and school_db.deputy_has_permission(uid, "broadcast"):
        return uid
    raise HTTPException(status_code=403, detail="Рассылки доступны администратору, директору, бухгалтеру, учителю или заместителю с правом")


def require_auth(request: Request):
    """Любой авторизованный пользователь (для профиля)."""
    uid = get_current_user_id(request)
    if not uid:
        _redirect_302("/login")
    return uid


def require_parent(request: Request):
    """Только родитель (для раздела «План питания» и отчётов)."""
    uid = get_current_user_id(request)
    if not uid:
        _redirect_302("/login")
    user = school_db.user_by_id(uid)
    if not user or user.get("role") != "parent":
        _redirect_302("/dashboard")
    return uid


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Главная: редирект в дашборд (администратор/директор/бухгалтер), питание (учитель/столовая) или логин."""
    uid = get_current_user_id(request)
    if uid:
        user = school_db.user_by_id(uid)
        if user and user.get("role") in ("teacher", "canteen"):
            return RedirectResponse(url="/nutrition", status_code=302)
        if user and user.get("role") == "parent":
            return RedirectResponse(url="/parent", status_code=302)
        if user and user.get("role") in ("administrator", "director", "accountant", "deputy_director"):
            return RedirectResponse(url="/dashboard", status_code=302)
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/parent", response_class=HTMLResponse)
async def parent_dashboard(request: Request, welcome: str = Query("")):
    """Личный кабинет родителя: дети, баланс столовой, история платежей."""
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse(url="/login", status_code=302)
    user = school_db.user_by_id(uid)
    if not user or user.get("role") != "parent":
        return RedirectResponse(url="/dashboard", status_code=302)
    students = school_db.students_by_parent_id(uid)
    purpose_options = _purpose_options()
    purposes_with_prices = school_db.payment_purpose_list()
    meal_prices = school_db.meal_prices_get_all()
    total_debt = 0.0
    for s in students:
        bal = school_db.balance_total_for_student(s["id"])
        s["balance"] = bal
        if bal < 0:
            total_debt += abs(bal)
        s["payments"] = school_db.payments_by_student(s["id"])[:15]
        s["charges"] = school_db.student_charges_for_student(s["id"])
        s["nutrition_deductions"] = school_db.nutrition_deductions_for_student(s["id"])[:20]
        s["nutrition_charge_to"] = school_db.nutrition_deductions_for_student_charge_to(s["id"])[:20]
    from datetime import date as date_type
    today = date_type.today()
    show_debt_banner = today.day > 10 and total_debt > 0
    return templates.TemplateResponse(
        "parent_dashboard.html",
        {
            "request": request,
            "user": user,
            "students": students,
            "purpose_options": purpose_options,
            "purposes_with_prices": purposes_with_prices,
            "meal_prices": meal_prices,
            "show_welcome": welcome == "1",
            "show_debt_banner": show_debt_banner,
            "total_debt": round(total_debt, 2),
        },
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Страница входа по email и паролю."""
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login_submit(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
):
    """Проверка email/пароль и установка cookie с JWT (secure и samesite для сессии)."""
    email = email.strip().lower()
    user = school_db.user_by_email(email)
    if not user:
        audit_log(_web_logger, "login_fail", extra={"email": email, "reason": "user_not_found"})
        return RedirectResponse(url="/login?error=user", status_code=302)
    if not user.get("password_hash"):
        audit_log(_web_logger, "login_fail", extra={"email": email, "reason": "no_password"})
        return RedirectResponse(url="/login?error=no_password", status_code=302)
    if not verify_password(password, user["password_hash"]):
        audit_log(_web_logger, "login_fail", extra={"email": email, "reason": "wrong_password"})
        return RedirectResponse(url="/login?error=password", status_code=302)
    audit_log(
        _web_logger,
        "login_success",
        user_id=user["id"],
        role=user.get("role"),
        extra={"email": email},
    )
    token = create_access_token(user["id"], user["role"])
    if user.get("role") in ("teacher", "canteen"):
        url = "/nutrition"
    elif user.get("role") == "parent":
        url = "/parent"
    else:
        url = "/dashboard"
    response = RedirectResponse(url=url, status_code=302)
    opts = _cookie_secure_samesite(request)
    response.set_cookie(
        key="session",
        value=token,
        path="/",
        httponly=True,
        max_age=86400,
        samesite=opts["samesite"],
        secure=opts["secure"],
    )
    return response


# --- Забыл пароль: ссылка на почту для сброса ---

RESET_EXPIRES_HOURS = 24


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    """Форма: ввод email для отправки ссылки сброса пароля."""
    return templates.TemplateResponse(
        "forgot_password.html",
        {"request": request, "email": ""},
    )


@app.post("/forgot-password")
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
):
    """Создать токен сброса и отправить ссылку на почту (если SMTP настроен)."""
    if not get_smtp_config():
        return RedirectResponse(url="/forgot-password?error=no_smtp", status_code=302)
    email_clean = email.strip().lower()
    user = school_db.user_by_email(email_clean)
    # Всегда показываем «отправлено», чтобы не раскрывать наличие email в системе
    if not user:
        return RedirectResponse(url="/forgot-password?sent=1", status_code=302)
    if not user.get("email"):
        return RedirectResponse(url="/forgot-password?sent=1", status_code=302)
    token = school_db.password_reset_create(user["id"], expires_hours=RESET_EXPIRES_HOURS)
    base_url = get_smtp_config()["base_url"]
    reset_link = f"{base_url}/reset-password?token={token}"
    sent_ok = send_password_reset_email(user["email"], reset_link, expires_hours=RESET_EXPIRES_HOURS)
    if not sent_ok:
        return RedirectResponse(url="/forgot-password?error=no_smtp", status_code=302)
    audit_log(
        _web_logger,
        "forgot_password_requested",
        extra={"email": email_clean},
    )
    return RedirectResponse(url="/forgot-password?sent=1", status_code=302)


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = ""):
    """Страница задания нового пароля по токену из письма."""
    if not token:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": "", "error": "Ссылка не указана. Запросите сброс пароля заново."},
        )
    data = school_db.password_reset_get(token)
    if not data:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": "", "error": "Ссылка недействительна или истекла. Запросите сброс пароля заново."},
        )
    return templates.TemplateResponse(
        "reset_password.html",
        {"request": request, "token": token, "error": None},
    )


@app.post("/reset-password")
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password2: str = Form(""),
):
    """Проверить токен, сохранить новый пароль и перенаправить на вход."""
    token = (token or "").strip()
    if not token:
        return RedirectResponse(url="/forgot-password", status_code=302)
    data = school_db.password_reset_get(token)
    if not data:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": "", "error": "Ссылка недействительна или истекла."},
        )
    if len(password) < 4:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "error": "Пароль должен быть не менее 4 символов."},
        )
    if password != password2:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "error": "Пароли не совпадают."},
        )
    user_id = data["user_id"]
    school_db.user_update(user_id, password_hash=hash_password(password))
    school_db.password_reset_use(token)
    audit_log(
        _web_logger,
        "password_reset_done",
        extra={"user_id": user_id},
    )
    return RedirectResponse(url="/login?reset=1", status_code=302)


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, token: str = ""):
    """Страница регистрации родителя по пригласительной ссылке (?token=...)."""
    if not token:
        return templates.TemplateResponse(
            "register_invalid.html",
            {"request": request, "message": "Ссылка приглашения не указана."},
        )
    inv = school_db.invitation_get_by_token(token)
    if not inv:
        return templates.TemplateResponse(
            "register_invalid.html",
            {"request": request, "message": "Ссылка недействительна или уже использована."},
        )
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "token": token, "invitation": inv},
    )


@app.post("/register")
async def register_submit(
    request: Request,
    response: Response,
    token: str = Form(...),
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    """Регистрация родителя по приглашению: создать пользователя, привязать к ученику, сразу войти и перейти в кабинет."""
    inv = school_db.invitation_get_by_token(token)
    if not inv:
        return RedirectResponse(url="/register?error=invalid", status_code=302)
    email_clean = email.strip().lower()
    if not email_clean or len(password) < 4:
        return RedirectResponse(url=f"/register?token={token}&error=validation", status_code=302)
    if school_db.user_by_email(email_clean):
        return RedirectResponse(url=f"/register?token={token}&error=email_taken", status_code=302)
    parent_id = school_db.user_create(
        role="parent",
        full_name=full_name.strip(),
        email=email_clean,
        password_hash=hash_password(password),
    )
    school_db.student_parents_add(inv["student_id"], parent_id, inv.get("parent_role", "primary"))
    school_db.invitation_mark_used(token, parent_id)
    # Сразу авторизуем и перенаправляем в личный кабинет родителя с приветствием
    session_token = create_access_token(parent_id, "parent")
    redirect_response = RedirectResponse(url="/parent?welcome=1", status_code=302)
    opts = _cookie_secure_samesite(request)
    redirect_response.set_cookie(
        key="session",
        value=session_token,
        path="/",
        httponly=True,
        max_age=86400,
        samesite=opts["samesite"],
        secure=opts["secure"],
    )
    return redirect_response


@app.get("/logout")
async def logout(request: Request):
    """Выход — удаляем cookie сессии (path совпадает с установкой)."""
    r = RedirectResponse(url="/login", status_code=302)
    r.delete_cookie("session", path="/")
    return r


# --- Профиль пользователя (фото, имя, почта, тема, для родителей — дети) ---
UPLOAD_AVATARS = Path(__file__).parent / "static" / "uploads" / "avatars"
UPLOAD_AVATARS.mkdir(parents=True, exist_ok=True)
ALLOWED_AVATAR_EXT = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, user_id: int = Depends(require_auth)):
    """Страница профиля: аватар, имя, почта, пароль, тема; у родителей — список детей."""
    user = school_db.user_by_id(user_id)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    students = school_db.students_by_parent_id(user_id) if user.get("role") == "parent" else []
    return templates.TemplateResponse(
        "profile.html",
        {"request": request, "user": user, "students": students},
    )


@app.get("/messages", response_class=HTMLResponse)
async def messages_page(request: Request, user_id: int = Depends(require_auth)):
    """Входящие рассылки и мои обращения (обратная связь с ответами админки)."""
    user = school_db.user_by_id(user_id)
    inbox = school_db.broadcast_inbox_for_user(user_id)
    my_feedback = school_db.feedback_list_by_user(user_id)
    return templates.TemplateResponse(
        "messages.html",
        {"request": request, "inbox": inbox, "my_feedback": my_feedback, "is_parent": user and user.get("role") == "parent"},
    )


# --- Обратная связь (все пользователи могут отправить) ---

@app.get("/feedback", response_class=HTMLResponse)
async def feedback_page(request: Request, user_id: int = Depends(require_auth)):
    """Форма обратной связи: любой пользователь может написать в админку."""
    user = school_db.user_by_id(user_id)
    is_parent = user and user.get("role") == "parent"
    return templates.TemplateResponse("feedback.html", {"request": request, "is_parent": is_parent})


@app.post("/feedback")
async def feedback_submit(
    request: Request,
    message_text: str = Form(""),
    user_id: int = Depends(require_auth),
):
    """Отправить обратную связь в админку."""
    if not (message_text or "").strip():
        raise HTTPException(status_code=400, detail="Введите текст сообщения")
    school_db.feedback_create(user_id, message_text.strip())
    return RedirectResponse(url="/feedback?sent=1", status_code=302)


# --- Админка: входящая обратная связь (директор/бухгалтер) ---

@app.get("/feedback/inbox", response_class=HTMLResponse)
async def feedback_inbox_page(
    request: Request,
    status: str = None,
    user_id: int = Depends(require_permission("feedback_inbox")),
):
    """Входящая обратная связь: список, фильтр по статусу."""
    items = school_db.feedback_list(status_filter=status)
    return templates.TemplateResponse(
        "feedback_inbox.html",
        {"request": request, "items": items, "status_filter": status},
    )


@app.post("/feedback/{feedback_id}/read")
async def feedback_mark_read_post(feedback_id: int, user_id: int = Depends(require_permission("feedback_inbox"))):
    school_db.feedback_mark_read(feedback_id)
    return RedirectResponse(url="/feedback/inbox", status_code=302)


@app.post("/feedback/{feedback_id}/reply")
async def feedback_reply_post(
    request: Request,
    feedback_id: int,
    admin_reply: str = Form(""),
    user_id: int = Depends(require_permission("feedback_inbox")),
):
    if not (admin_reply or "").strip():
        raise HTTPException(status_code=400, detail="Введите ответ")
    school_db.feedback_reply(feedback_id, admin_reply.strip(), user_id)
    return RedirectResponse(url="/feedback/inbox", status_code=302)


@app.post("/feedback/{feedback_id}/delete")
async def feedback_delete_post(feedback_id: int, user_id: int = Depends(require_permission("feedback_inbox"))):
    school_db.feedback_delete(feedback_id)
    return RedirectResponse(url="/feedback/inbox", status_code=302)


@app.post("/feedback/{feedback_id}/move-to-task")
async def feedback_move_to_task_post(
    request: Request,
    feedback_id: int,
    title: str = Form(""),
    contact_info: str = Form(""),
    user_id: int = Depends(require_permission("feedback_inbox")),
):
    """Перенести обратную связь в текущие дела (создать задачу)."""
    fb = school_db.feedback_by_id(feedback_id)
    if not fb:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    title = (title or "").strip() or f"Обратная связь #{feedback_id}"
    task_id = school_db.task_create(
        title=title,
        description=fb.get("message_text", ""),
        contact_info=(contact_info or "").strip() or f"{fb.get('from_name', '')} ({fb.get('from_email', '')})",
        created_by_id=user_id,
        source_feedback_id=feedback_id,
    )
    school_db.feedback_mark_moved(feedback_id)
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=302)


# --- Текущие дела (задачи) — админка ---

@app.get("/tasks", response_class=HTMLResponse)
async def tasks_list_page(request: Request, user_id: int = Depends(require_permission("tasks"))):
    """Список текущих дел."""
    tasks = school_db.task_list()
    return templates.TemplateResponse("tasks_list.html", {"request": request, "tasks": tasks})


@app.get("/tasks/add", response_class=HTMLResponse)
async def task_add_page(request: Request, user_id: int = Depends(require_permission("tasks"))):
    """Форма создания задачи."""
    groups = school_db.task_group_list()
    users = school_db.user_list()
    return templates.TemplateResponse(
        "task_edit.html",
        {"request": request, "task": None, "groups": groups, "users": users},
    )


@app.post("/tasks/add")
async def task_add_submit(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    contact_info: str = Form(""),
    user_id: int = Depends(require_permission("tasks")),
):
    task_id = school_db.task_create(
        title=title.strip(),
        description=description.strip(),
        contact_info=contact_info.strip(),
        created_by_id=user_id,
    )
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=302)


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail_page(
    request: Request,
    task_id: int,
    user_id: int = Depends(require_permission("tasks")),
):
    """Просмотр/редактирование задачи, назначения, комментарии, контакт для связи."""
    task = school_db.task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    assignments = school_db.task_assignment_list(task_id)
    comments = school_db.task_comments_list(task_id)
    groups = school_db.task_group_list()
    users = school_db.user_list()
    return templates.TemplateResponse(
        "task_detail.html",
        {"request": request, "task": task, "assignments": assignments, "comments": comments, "groups": groups, "users": users},
    )


@app.post("/tasks/{task_id}")
async def task_update_submit(
    request: Request,
    task_id: int,
    title: str = Form(""),
    description: str = Form(""),
    contact_info: str = Form(""),
    status: str = Form(""),
    user_id: int = Depends(require_permission("tasks")),
):
    school_db.task_update(
        task_id,
        title=title.strip() or None,
        description=description.strip() if description is not None else None,
        contact_info=contact_info.strip() if contact_info is not None else None,
        status=status.strip() if status in ("open", "in_progress", "done") else None,
    )
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=302)


@app.post("/tasks/{task_id}/delete")
async def task_delete_post(task_id: int, user_id: int = Depends(require_permission("tasks"))):
    school_db.task_delete(task_id)
    return RedirectResponse(url="/tasks", status_code=302)


@app.post("/tasks/{task_id}/comment")
async def task_comment_post(
    task_id: int,
    comment_text: str = Form(""),
    user_id: int = Depends(require_permission("tasks")),
):
    """Добавить комментарий к задаче (веб)."""
    task = school_db.task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    text = (comment_text or "").strip()
    if text:
        school_db.task_comment_add(task_id, user_id, text)
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=302)


@app.post("/tasks/{task_id}/assign")
async def task_assign_post(
    request: Request,
    task_id: int,
    assign_type: str = Form(...),  # "group" or "user"
    group_id: str = Form(""),
    assign_user_id: str = Form(""),
    can_view: str = Form("on"),
    is_executor: str = Form(""),
    user_id: int = Depends(require_permission("tasks")),
):
    can_view = can_view == "on"
    is_executor = is_executor == "on"
    if assign_type == "group" and group_id and group_id.strip().isdigit():
        school_db.task_assignment_add(task_id, group_id=int(group_id.strip()), can_view=can_view, is_executor=is_executor)
    elif assign_type == "user" and assign_user_id and assign_user_id.strip().isdigit():
        school_db.task_assignment_add(task_id, user_id=int(assign_user_id.strip()), can_view=can_view, is_executor=is_executor)
    return RedirectResponse(url=f"/tasks/{task_id}", status_code=302)


@app.post("/tasks/assignments/{assignment_id}/remove")
async def task_assignment_remove_post(request: Request, assignment_id: int, user_id: int = Depends(require_permission("tasks"))):
    school_db.task_assignment_remove(assignment_id)
    # Редирект только на свой сайт (защита от open redirect)
    referer = request.headers.get("referer", "").strip()
    if referer.startswith("/") and "//" not in referer:
        return RedirectResponse(url=referer, status_code=302)
    return RedirectResponse(url="/tasks", status_code=302)


# --- Группы для задач (родители, бухгалтерия, сторонние) ---

@app.get("/tasks/groups", response_class=HTMLResponse)
async def task_groups_page(request: Request, user_id: int = Depends(require_permission("tasks"))):
    """Список групп для назначения на задачи."""
    groups = school_db.task_group_list()
    return templates.TemplateResponse("task_groups.html", {"request": request, "groups": groups})


@app.post("/tasks/groups/add")
async def task_group_add(name: str = Form(...), user_id: int = Depends(require_permission("tasks"))):
    school_db.task_group_create(name.strip())
    return RedirectResponse(url="/tasks/groups", status_code=302)


@app.post("/tasks/groups/{group_id}/delete")
async def task_group_delete_route(
    group_id: int,
    user_id: int = Depends(require_permission("tasks")),
):
    """Удалить группу (назначения на задачах с ней снимаются)."""
    group = school_db.task_group_by_id(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    school_db.task_group_delete(group_id)
    return RedirectResponse(url="/tasks/groups", status_code=302)


@app.get("/tasks/groups/{group_id}", response_class=HTMLResponse)
async def task_group_edit_page(
    request: Request,
    group_id: int,
    user_id: int = Depends(require_permission("tasks")),
):
    """Редактирование группы: участники (родители, администрация, сторонние)."""
    group = school_db.task_group_by_id(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    members = school_db.task_group_members_list(group_id)
    member_ids = [m["id"] for m in members]
    users = school_db.user_list()
    return templates.TemplateResponse(
        "task_group_edit.html",
        {"request": request, "group": group, "members": members, "users": users, "member_ids": member_ids},
    )


@app.post("/tasks/groups/{group_id}/members/add")
async def task_group_member_add(
    request: Request,
    group_id: int,
    user_id_add: int = Form(...),
    user_id: int = Depends(require_permission("tasks")),
):
    school_db.task_group_add_member(group_id, user_id_add)
    return RedirectResponse(url=f"/tasks/groups/{group_id}", status_code=302)


@app.post("/tasks/groups/{group_id}/members/remove")
async def task_group_member_remove(
    request: Request,
    group_id: int,
    user_id_remove: int = Form(...),
    user_id: int = Depends(require_permission("tasks")),
):
    school_db.task_group_remove_member(group_id, user_id_remove)
    return RedirectResponse(url=f"/tasks/groups/{group_id}", status_code=302)


# --- Мои задачи (для пользователей, назначенных на задачи) ---

@app.get("/my-tasks", response_class=HTMLResponse)
async def my_tasks_page(request: Request, user_id: int = Depends(require_auth)):
    """Задачи, которые назначены на текущего пользователя (или на группу, в которой он состоит)."""
    user = school_db.user_by_id(user_id)
    is_parent = user and user.get("role") == "parent"
    tasks = school_db.tasks_for_user(user_id)
    return templates.TemplateResponse("my_tasks.html", {"request": request, "tasks": tasks, "is_parent": is_parent})


@app.post("/profile")
async def profile_submit(
    request: Request,
    response: Response,
    full_name: str = Form(""),
    email: str = Form(""),
    new_password: str = Form(""),
    theme_preference: str = Form("system"),
    telegram_id: str = Form(""),
    whatsapp_contact: str = Form(""),
    max_contact: str = Form(""),
    avatar: UploadFile = File(None),
    user_id: int = Depends(require_auth),
):
    """Сохранить настройки профиля и/или загрузить фото. Привязки: Telegram, WhatsApp, МАХ."""
    from web.auth_utils import hash_password
    if full_name and full_name.strip():
        school_db.user_update(user_id, full_name=full_name.strip())
    email_clean = email.strip().lower() if email else ""
    if email_clean:
        existing = school_db.user_by_email(email_clean)
        if existing and existing.get("id") != user_id:
            raise HTTPException(status_code=400, detail="Такой email уже занят")
        school_db.user_update(user_id, email=email_clean)
    if new_password.strip():
        if len(new_password) < 4:
            raise HTTPException(status_code=400, detail="Пароль не короче 4 символов")
        school_db.user_update(user_id, password_hash=hash_password(new_password))
    main_form_sent = bool(full_name.strip() or email.strip() or new_password.strip())
    if main_form_sent and theme_preference in ("light", "dark", "system"):
        school_db.user_update(user_id, theme_preference=theme_preference)
    # Привязки к мессенджерам: Telegram ID, WhatsApp, МАХ
    tid = (telegram_id or "").strip()
    update_kw: dict = {
        "whatsapp_contact": (whatsapp_contact or "").strip(),
        "max_contact": (max_contact or "").strip(),
    }
    if tid == "":
        update_kw["clear_telegram"] = True
    elif tid.isdigit():
        update_kw["telegram_id"] = int(tid)
    school_db.user_update(user_id, **update_kw)
    # Загрузка аватара
    if avatar and avatar.filename:
        ext = Path(avatar.filename).suffix.lower()
        if ext not in ALLOWED_AVATAR_EXT:
            raise HTTPException(status_code=400, detail="Допустимы только изображения: jpg, png, gif, webp")
        content = await avatar.read()
        if len(content) > 5 * 1024 * 1024:  # 5 МБ
            raise HTTPException(status_code=400, detail="Файл не больше 5 МБ")
        filename = f"{user_id}{ext}"
        path = UPLOAD_AVATARS / filename
        path.write_bytes(content)
        rel_path = f"uploads/avatars/{filename}"
        school_db.user_update(user_id, avatar_path=rel_path)
    r = RedirectResponse(url="/profile", status_code=302)
    if main_form_sent and theme_preference in ("light", "dark", "system"):
        opts = _cookie_secure_samesite(request)
        r.set_cookie(
            "theme",
            theme_preference,
            max_age=31536000,
            path="/",
            samesite=opts["samesite"],
            secure=opts["secure"],
        )
    return r


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user_id: int = Depends(require_permission("dashboard"))):
    """Дашборд: неподтверждённые платежи и сообщения родителей «Я совершил платёж»."""
    pending = school_db.payments_pending_all()
    parent_reports = school_db.parent_report_payment_list_pending()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "pending_payments": pending, "parent_reports": parent_reports, "purpose_options": _purpose_options()},
    )


@app.post("/payment/{payment_id}/confirm")
async def payment_confirm(
    request: Request,
    payment_id: int,
    comment: str = Form(""),
    user_id: int = Depends(require_permission("payments")),
):
    """Подтвердить платёж (только бухгалтер/директор)."""
    ok = school_db.payment_confirm(payment_id, user_id, comment or None)
    if not ok:
        audit_log(_web_logger, "payment_confirm_fail", user_id=user_id, extra={"payment_id": payment_id})
        raise HTTPException(status_code=404, detail="Платёж не найден или уже обработан")
    user = school_db.user_by_id(user_id)
    audit_log(
        _web_logger,
        "payment_confirm",
        user_id=user_id,
        role=user.get("role") if user else None,
        extra={"payment_id": payment_id},
    )
    return RedirectResponse(url="/dashboard", status_code=302)


@app.post("/parent-reports/{report_id}/entered")
async def parent_report_entered(
    request: Request,
    report_id: int,
    user_id: int = Depends(require_permission("payments")),
):
    """Бухгалтер/директор проверил: платёж внесён в систему."""
    ok = school_db.parent_report_mark_entered(report_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Сообщение не найдено или уже обработано")
    return RedirectResponse(url="/dashboard", status_code=302)


@app.post("/parent-reports/{report_id}/dismiss")
async def parent_report_dismiss(
    request: Request,
    report_id: int,
    user_id: int = Depends(require_permission("payments")),
):
    """Бухгалтер/директор проверил: платёж не найден / отклонить."""
    ok = school_db.parent_report_mark_dismissed(report_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Сообщение не найдено или уже обработано")
    return RedirectResponse(url="/dashboard", status_code=302)


@app.post("/payment/{payment_id}/reject")
async def payment_reject(
    request: Request,
    payment_id: int,
    comment: str = Form(""),
    user_id: int = Depends(require_permission("payments")),
):
    """Отклонить платёж."""
    ok = school_db.payment_reject(payment_id, user_id, comment or None)
    if not ok:
        raise HTTPException(status_code=404, detail="Платёж не найден или уже обработан")
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/payments", response_class=HTMLResponse)
async def payments_list(request: Request, user_id: int = Depends(require_permission("payments"))):
    """Список всех платежей (история)."""
    # Собираем все платежи по ученикам
    students = school_db.students_all()
    all_payments = []
    for s in students:
        for p in school_db.payments_by_student(s["id"]):
            p["student_name"] = s["full_name"]
            p["class_grade"] = s["class_grade"]
            p["class_letter"] = s.get("class_letter", "")
            all_payments.append(p)
    all_payments.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return templates.TemplateResponse(
        "payments.html",
        {"request": request, "payments": all_payments[:200], "purpose_options": _purpose_options()},
    )


@app.get("/students", response_class=HTMLResponse)
async def students_list(
    request: Request,
    archive: str = Query(""),
    user_id: int = Depends(require_permission("students")),
):
    """Список учеников (активные или архив). По умолчанию только активные."""
    show_archived = archive == "1"
    students = school_db.students_all(include_archived=show_archived)
    for s in students:
        s["balance"] = school_db.balance_total_for_student(s["id"])
    current = school_db.user_by_id(user_id)
    can_charge = current and current.get("role") in ("administrator", "director", "accountant") or (current.get("role") == "deputy_director" and school_db.deputy_has_permission(user_id, "payments"))
    return templates.TemplateResponse(
        "students.html",
        {"request": request, "students": students, "can_charge": can_charge, "show_archived": show_archived},
    )


@app.get("/students/invite", response_class=HTMLResponse)
async def student_invite_page(
    request: Request,
    student_id: int | None = Query(None),
    user_id: int = Depends(require_permission("students")),
):
    """Создать пригласительную ссылку для родителя ученика. Показать ссылку для копирования."""
    if not student_id:
        return RedirectResponse(url="/students", status_code=302)
    student = school_db.student_by_id(student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Ученик не найден")
    token = school_db.invitation_create(student_id, expires_days=30)
    # Ссылка всегда ведёт на веб (не в Telegram): в проде задайте BASE_URL в env
    base_url = (os.getenv("BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        base_url = str(request.base_url).rstrip("/")
    invite_url = f"{base_url}/register?token={token}"
    return templates.TemplateResponse(
        "student_invite.html",
        {"request": request, "student": student, "invite_url": invite_url},
    )


@app.get("/students/add", response_class=HTMLResponse)
async def student_add_page(request: Request, user_id: int = Depends(require_permission("students"))):
    """Форма добавления ученика."""
    parents = school_db.user_list(role="parent")
    return templates.TemplateResponse(
        "student_add.html",
        {"request": request, "parents": parents},
    )


@app.post("/students/add")
async def student_add_submit(
    request: Request,
    full_name: str = Form(...),
    class_grade: int = Form(...),
    parent_id: str = Form(""),
    user_id: int = Depends(require_permission("students")),
):
    """Добавить ученика в базу. Родитель необязателен — можно пригласить позже по ссылке."""
    if not 0 <= class_grade <= 11:
        raise HTTPException(status_code=400, detail="Класс: выберите Детский сад или 1–11")
    pid = int(parent_id) if parent_id and parent_id.strip().isdigit() else None
    school_db.student_create(full_name.strip(), class_grade, pid, "")
    user = school_db.user_by_id(user_id)
    audit_log(
        _web_logger,
        "student_add",
        user_id=user_id,
        role=user.get("role") if user else None,
        extra={"full_name": full_name.strip(), "class_grade": class_grade, "parent_id": pid},
    )
    return RedirectResponse(url="/students", status_code=302)


@app.post("/students/{student_id}/archive")
async def student_archive(
    student_id: int,
    user_id: int = Depends(require_permission("students")),
):
    """Перевести ученика в архив: не участвует в списках, расчётах и напоминаниях."""
    student = school_db.student_by_id(student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Ученик не найден")
    school_db.student_set_archived(student_id, True)
    return RedirectResponse(url="/students", status_code=302)


@app.post("/students/{student_id}/restore")
async def student_restore(
    student_id: int,
    user_id: int = Depends(require_permission("students")),
):
    """Восстановить ученика из архива."""
    student = school_db.student_by_id(student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Ученик не найден")
    school_db.student_set_archived(student_id, False)
    return RedirectResponse(url="/students", status_code=302)


@app.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, user_id: int = Depends(require_permission("users"))):
    """Список пользователей (родители, сотрудники)."""
    users = school_db.user_list()
    return templates.TemplateResponse(
        "users.html",
        {"request": request, "users": users},
    )


@app.get("/users/{uid}/edit", response_class=HTMLResponse)
async def user_edit_page(request: Request, uid: int, user_id: int = Depends(require_permission("users"))):
    """Редактирование пользователя (привязка Telegram ID, для заместителя — права разделов)."""
    user = school_db.user_by_id(uid)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    current = school_db.user_by_id(user_id)
    deputy_permissions = []
    can_edit_deputy_permissions = False
    if user.get("role") == "deputy_director" and current and current.get("role") in ("director", "administrator"):
        can_edit_deputy_permissions = True
        deputy_permissions = school_db.deputy_permissions_list(uid)
    return templates.TemplateResponse(
        "user_edit.html",
        {
            "request": request,
            "user": user,
            "deputy_permissions": deputy_permissions,
            "deputy_permission_keys": school_db.DEPUTY_PERMISSION_KEYS,
            "can_edit_deputy_permissions": can_edit_deputy_permissions,
        },
    )


@app.post("/users/{uid}/edit")
async def user_edit_submit(
    request: Request,
    uid: int,
    full_name: str = Form(""),
    email: str = Form(""),
    new_password: str = Form(""),
    telegram_id: str = Form(""),
    user_id: int = Depends(require_permission("users")),
):
    """Сохранить данные пользователя: ФИО, email, пароль, Telegram ID; для заместителя — права разделов."""
    from web.auth_utils import hash_password
    u = school_db.user_by_id(uid)
    if not u:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    current = school_db.user_by_id(user_id)
    if u.get("role") == "deputy_director" and current and current.get("role") in ("director", "administrator"):
        form = await request.form()
        keys = form.getlist("deputy_permission")
        school_db.deputy_permissions_set(uid, keys)
    if full_name and full_name.strip():
        school_db.user_update(uid, full_name=full_name.strip())
    email_clean = email.strip().lower() if email else ""
    if email_clean:
        existing = school_db.user_by_email(email_clean)
        if existing and existing.get("id") != uid:
            raise HTTPException(status_code=400, detail="Такой email уже занят другим пользователем")
        school_db.user_update(uid, email=email_clean)
    if new_password.strip():
        school_db.user_update(uid, password_hash=hash_password(new_password))
    if telegram_id.strip():
        try:
            tid = int(telegram_id.strip())
            school_db.user_link_telegram(uid, tid)
        except ValueError:
            raise HTTPException(status_code=400, detail="Telegram ID должен быть числом")
    return RedirectResponse(url="/users", status_code=302)


@app.post("/users/{uid}/delete")
async def user_delete_route(
    uid: int,
    user_id: int = Depends(require_permission("users")),
):
    """Деактивировать пользователя (он исчезнет из списков и не сможет войти)."""
    ok, msg = school_db.user_delete(uid)
    if not ok:
        raise HTTPException(status_code=400, detail=msg or "Не удалось удалить пользователя")
    return RedirectResponse(url="/users", status_code=302)


@app.get("/users/add", response_class=HTMLResponse)
async def user_add_page(request: Request, user_id: int = Depends(require_permission("users"))):
    """Форма добавления пользователя."""
    return templates.TemplateResponse("user_add.html", {"request": request})


@app.post("/users/add")
async def user_add_submit(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(""),
    role: str = Form(...),
    password: str = Form(""),
    user_id: int = Depends(require_permission("users")),
):
    """Добавить пользователя. Роль «Администратор» может назначить только текущий администратор."""
    email = email.strip().lower() if email else None
    if role not in ("administrator", "director", "deputy_director", "accountant", "parent", "teacher", "canteen"):
        raise HTTPException(status_code=400, detail="Недопустимая роль")
    if role == "administrator":
        user = school_db.user_by_id(user_id)
        if not user or user.get("role") != "administrator":
            raise HTTPException(status_code=403, detail="Создавать администратора может только администратор")
    password_hash = hash_password(password) if password else None
    if role in ("administrator", "director", "deputy_director", "accountant", "teacher", "canteen") and not password:
        raise HTTPException(status_code=400, detail="Для входа на сайт нужен пароль")
    school_db.user_create(
        role=role,
        full_name=full_name,
        email=email,
        password_hash=password_hash,
    )
    return RedirectResponse(url="/users", status_code=302)


@app.get("/events", response_class=HTMLResponse)
async def events_list_page(request: Request, user_id: int = Depends(require_permission("events"))):
    """Список мероприятий."""
    events = school_db.events_list()
    return templates.TemplateResponse("events.html", {"request": request, "events": events})


@app.get("/events/add", response_class=HTMLResponse)
async def event_add_page(request: Request, user_id: int = Depends(require_permission("events"))):
    return templates.TemplateResponse("event_add.html", {"request": request})


@app.post("/events/add")
async def event_add_submit(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    event_date: str = Form(""),
    amount_required: str = Form(""),
    user_id: int = Depends(require_permission("events")),
):
    amount = float(amount_required) if amount_required.strip() else None
    school_db.event_create(name, description or None, event_date or None, amount)
    return RedirectResponse(url="/events", status_code=302)


def _purpose_options():
    """Словарь код назначения -> название для подстановки в шаблонах."""
    return {p["code"]: p["name"] for p in school_db.payment_purpose_list()}


@app.get("/payment-purposes", response_class=HTMLResponse)
async def payment_purposes_page(request: Request, user_id: int = Depends(require_permission("payment_purposes"))):
    """Справочник назначений платежей — только для бухгалтера/директора."""
    purposes = school_db.payment_purpose_list()
    return templates.TemplateResponse(
        "payment_purposes.html",
        {"request": request, "purposes": purposes},
    )


@app.post("/payment-purposes/add")
async def payment_purpose_add(
    name: str = Form(...),
    user_id: int = Depends(require_permission("payment_purposes")),
):
    """Добавить вариант назначения. Код генерируется из названия (латиница), название показывается в форме и отчётах."""
    # Код из названия: транслит-подобный slug (только буквы, цифры, подчёркивание)
    code = name.strip().lower().replace(" ", "_")
    code = "".join(c for c in code if c.isalnum() or c == "_") or "other"
    if not name.strip():
        raise HTTPException(status_code=400, detail="Укажите название")
    try:
        school_db.payment_purpose_create(code, name.strip(), sort_order=999)
    except Exception as e:
        if "UNIQUE" in str(e) or "unique" in str(e).lower():
            raise HTTPException(status_code=400, detail="Назначение с таким кодом уже есть")
        raise
    return RedirectResponse(url="/payment-purposes", status_code=302)


@app.get("/payment-purposes/{purpose_id}/edit", response_class=HTMLResponse)
async def payment_purpose_edit_page(
    purpose_id: int,
    request: Request,
    user_id: int = Depends(require_permission("payment_purposes")),
):
    """Страница редактирования назначения."""
    purpose = school_db.payment_purpose_by_id(purpose_id)
    if not purpose:
        raise HTTPException(status_code=404, detail="Назначение не найдено")
    return templates.TemplateResponse(
        "payment_purpose_edit.html",
        {"request": request, "purpose": purpose},
    )


@app.post("/payment-purposes/{purpose_id}/edit")
async def payment_purpose_edit(
    purpose_id: int,
    code: str = Form(...),
    name: str = Form(...),
    price: str = Form("0"),
    charge_frequency: str = Form("manual"),
    user_id: int = Depends(require_permission("payment_purposes")),
):
    """Изменить назначение (в т.ч. цену и тип начисления)."""
    if not name.strip():
        raise HTTPException(status_code=400, detail="Укажите название")
    code = code.strip().lower().replace(" ", "_")
    code = "".join(c for c in code if c.isalnum() or c == "_") or "other"
    try:
        price_f = float(price.replace(",", ".")) if price else 0
    except ValueError:
        price_f = 0
    if charge_frequency not in ("monthly", "daily", "manual"):
        charge_frequency = "manual"
    if not school_db.payment_purpose_update(purpose_id, code, name.strip(), price=price_f, charge_frequency=charge_frequency):
        raise HTTPException(status_code=404, detail="Назначение не найдено")
    return RedirectResponse(url="/payment-purposes", status_code=302)


@app.post("/payment-purposes/{purpose_id}/delete")
async def payment_purpose_delete(
    purpose_id: int,
    user_id: int = Depends(require_permission("payment_purposes")),
):
    """Удалить назначение (если нет платежей с этим назначением)."""
    ok, msg = school_db.payment_purpose_delete(purpose_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return RedirectResponse(url="/payment-purposes", status_code=302)


@app.get("/payments/add", response_class=HTMLResponse)
async def payment_add_page(request: Request, user_id: int = Depends(require_permission("payments"))):
    """Форма внесения платёжных данных по ученику (бухгалтер). student_id в query — предвыбор из сообщения родителя."""
    students = school_db.students_all()
    purposes = school_db.payment_purpose_list()
    selected_id = request.query_params.get("student_id")
    try:
        selected_student_id = int(selected_id) if selected_id else None
    except ValueError:
        selected_student_id = None
    return templates.TemplateResponse(
        "payment_add.html",
        {"request": request, "students": students, "purposes": purposes, "selected_student_id": selected_student_id},
    )


@app.post("/payments/add")
async def payment_add_submit(
    request: Request,
    student_id: int = Form(...),
    amount: float = Form(...),
    purpose: str = Form(...),
    description: str = Form(""),
    payment_type: str = Form("cash"),
    bank_commission: str = Form("0"),
    amount_received: str = Form(""),
    user_id: int = Depends(require_permission("payments")),
):
    """Внести платёж (статус — ожидает подтверждения). Учитываются тип оплаты и комиссия для бухгалтерии."""
    valid_codes = school_db.payment_purpose_codes()
    if purpose not in valid_codes:
        raise HTTPException(status_code=400, detail="Недопустимое назначение. Выберите из списка в форме или добавьте в разделе «Назначения платежей».")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше 0")
    pt = "cashless" if payment_type == "cashless" else "cash"
    try:
        comm = float(bank_commission or "0")
    except ValueError:
        comm = 0
    if comm < 0:
        comm = 0
    recv = None
    if amount_received and amount_received.strip():
        try:
            recv = float(amount_received.strip())
        except ValueError:
            pass
    school_db.payment_create(
        student_id, amount, purpose,
        description=description or None,
        payment_type=pt,
        bank_commission=comm,
        amount_received=recv,
    )
    user = school_db.user_by_id(user_id)
    audit_log(
        _web_logger,
        "payment_add",
        user_id=user_id,
        role=user.get("role") if user else None,
        extra={"student_id": student_id, "amount": amount, "purpose": purpose, "payment_type": pt},
    )
    return RedirectResponse(url="/dashboard", status_code=302)


# --- Бухгалтерия (директор, бухгалтер): фильтры, сальдо, списания, баланс, прогноз ---

@app.get("/accounting", response_class=HTMLResponse)
async def accounting_page(
    request: Request,
    date_from: str = None,
    date_to: str = None,
    payment_type: str = None,
    amount_min: str = None,
    amount_max: str = None,
    user_id: int = Depends(require_permission("accounting")),
):
    """Бухгалтерия: входящее сальдо, фильтры (дата, тип оплаты, сумма), движения, баланс на сегодня, прогноз. Учебный год: сентябрь – июль."""
    start, end = school_db.school_year_period()
    period_key = school_db.school_year_key()
    opening = school_db.accounting_get_opening_balance(period_key)
    balance_today = school_db.accounting_balance_today()
    forecast = school_db.accounting_forecast_end_of_month()
    df = date_from or start
    dt = date_to or end
    am_min = float(amount_min) if amount_min and amount_min.strip() else None
    try:
        am_max = float(amount_max) if amount_max and amount_max.strip() else None
    except ValueError:
        am_max = None
    incomes = school_db.accounting_incomes_list(
        date_from=df, date_to=dt,
        payment_type_filter=payment_type or None,
        amount_min=am_min,
        amount_max=am_max,
    )
    incomes_extra = school_db.accounting_income_extra_list(date_from=df, date_to=dt)
    expenses = school_db.accounting_expenses_list(date_from=df, date_to=dt)
    # Объединяем движения и сортируем по дате (новые сверху)
    movements = []
    for p in incomes:
        movements.append({
            "date": (p.get("movement_date") or p.get("created_at") or "")[:10],
            "type": "income",
            "amount": p.get("received") or p.get("amount", 0),
            "payment_type": p.get("payment_type"),
            "description": p.get("student_name") or "",
            "purpose": p.get("purpose"),
            "bank_commission": p.get("bank_commission"),
            "is_extra": False,
        })
    for ex in incomes_extra:
        movements.append({
            "date": (ex.get("income_date") or "")[:10],
            "type": "income",
            "amount": ex.get("amount", 0),
            "payment_type": None,
            "description": ex.get("comment") or "Доп. средства в кассу",
            "purpose": None,
            "bank_commission": None,
            "is_extra": True,
        })
    for e in expenses:
        movements.append({
            "date": (e.get("expense_date") or "")[:10],
            "type": "expense",
            "amount": e.get("amount", 0),
            "description": e.get("reason") or "—",
        })
    movements.sort(key=lambda x: x["date"] or "", reverse=True)
    return templates.TemplateResponse(
        "accounting.html",
        {
            "request": request,
            "school_year_start": start,
            "school_year_end": end,
            "period_key": period_key,
            "opening_balance": opening,
            "balance_today": balance_today,
            "forecast_end_of_month": forecast,
            "movements": movements,
            "purpose_options": _purpose_options(),
            "filters": {
                "date_from": df,
                "date_to": dt,
                "payment_type": payment_type or "",
                "amount_min": amount_min or "",
                "amount_max": amount_max or "",
            },
        },
    )


@app.post("/accounting/opening-balance")
async def accounting_opening_balance_submit(
    request: Request,
    period_key: str = Form(...),
    balance: str = Form("0"),
    user_id: int = Depends(require_permission("accounting")),
):
    """Сохранить входящее сальдо на начало учебного года."""
    try:
        val = float(balance.replace(",", "."))
    except ValueError:
        val = 0
    school_db.accounting_set_opening_balance(period_key.strip(), val)
    return RedirectResponse(url="/accounting", status_code=302)


@app.post("/accounting/expense")
async def accounting_expense_submit(
    request: Request,
    amount: str = Form(...),
    reason: str = Form(...),
    expense_date: str = Form(...),
    user_id: int = Depends(require_permission("accounting")),
):
    """Внести списание (расход)."""
    try:
        am = float(amount.replace(",", "."))
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректная сумма")
    if am <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше 0")
    school_db.accounting_expense_create(am, reason.strip() or "—", expense_date, user_id)
    return RedirectResponse(url="/accounting", status_code=302)


@app.post("/accounting/income-extra")
async def accounting_income_extra_submit(
    request: Request,
    amount: str = Form(...),
    comment: str = Form(""),
    income_date: str = Form(...),
    user_id: int = Depends(require_permission("accounting")),
):
    """Внести дополнительные средства в кассу (с комментарием)."""
    try:
        am = float(amount.replace(",", ".").strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректная сумма")
    if am <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше 0")
    if not income_date.strip():
        raise HTTPException(status_code=400, detail="Укажите дату")
    school_db.accounting_income_extra_create(am, comment.strip() or "Доп. средства в кассу", income_date, user_id)
    return RedirectResponse(url="/accounting", status_code=302)


# --- Отделы (только директор) ---

@app.get("/departments", response_class=HTMLResponse)
async def departments_list(request: Request, user_id: int = Depends(require_director_or_deputy("departments"))):
    """Список отделов — только директор."""
    departments = school_db.department_list()
    return templates.TemplateResponse(
        "departments.html",
        {"request": request, "departments": departments},
    )


@app.post("/departments/add")
async def department_add(
    request: Request,
    name: str = Form(...),
    user_id: int = Depends(require_director_or_deputy("departments")),
):
    """Создать отдел."""
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название отдела не задано")
    school_db.department_create(name)
    return RedirectResponse(url="/departments", status_code=302)


@app.get("/departments/{dep_id}/edit", response_class=HTMLResponse)
async def department_edit_page(
    request: Request,
    dep_id: int,
    user_id: int = Depends(require_director_or_deputy("departments")),
):
    """Редактирование отдела: название и участники."""
    dep = school_db.department_by_id(dep_id)
    if not dep:
        raise HTTPException(status_code=404, detail="Отдел не найден")
    members = school_db.department_members(dep_id)
    member_ids = [m["id"] for m in members]
    all_users = school_db.user_list(role=None)
    return templates.TemplateResponse(
        "department_edit.html",
        {"request": request, "department": dep, "members": members, "all_users": all_users, "member_ids": member_ids},
    )


@app.post("/departments/{dep_id}/edit")
async def department_edit_submit(
    request: Request,
    dep_id: int,
    name: str = Form(""),
    user_id: int = Depends(require_director_or_deputy("departments")),
):
    """Переименовать отдел."""
    if not school_db.department_by_id(dep_id):
        raise HTTPException(status_code=404, detail="Отдел не найден")
    if name.strip():
        school_db.department_update(dep_id, name.strip())
    return RedirectResponse(url=f"/departments/{dep_id}/edit", status_code=302)


@app.post("/departments/{dep_id}/members/add")
async def department_member_add(
    request: Request,
    dep_id: int,
    user_id_add: int = Form(...),
    user_id: int = Depends(require_director_or_deputy("departments")),
):
    """Добавить пользователя в отдел."""
    if not school_db.department_by_id(dep_id):
        raise HTTPException(status_code=404, detail="Отдел не найден")
    school_db.department_add_member(dep_id, user_id_add)
    return RedirectResponse(url=f"/departments/{dep_id}/edit", status_code=302)


@app.post("/departments/{dep_id}/members/remove")
async def department_member_remove(
    request: Request,
    dep_id: int,
    user_id_remove: int = Form(...),
    user_id: int = Depends(require_director_or_deputy("departments")),
):
    """Убрать пользователя из отдела."""
    school_db.department_remove_member(dep_id, user_id_remove)
    return RedirectResponse(url=f"/departments/{dep_id}/edit", status_code=302)


@app.post("/departments/{dep_id}/delete")
async def department_delete(
    request: Request,
    dep_id: int,
    user_id: int = Depends(require_director_or_deputy("departments")),
):
    """Удалить отдел."""
    if not school_db.department_by_id(dep_id):
        raise HTTPException(status_code=404, detail="Отдел не найден")
    school_db.department_delete(dep_id)
    return RedirectResponse(url="/departments", status_code=302)


# --- Классные руководители: привязка учителя к классу (директор/бухгалтер) ---

@app.get("/teacher-classes", response_class=HTMLResponse)
async def teacher_classes_page(request: Request, user_id: int = Depends(require_permission("teacher_classes"))):
    """Назначение классных руководителей: по каждому классу выбирается учитель."""
    assignments = school_db.teacher_class_list_all()
    teachers = school_db.user_list(role="teacher")
    return templates.TemplateResponse(
        "teacher_classes.html",
        {"request": request, "assignments": assignments, "teachers": teachers},
    )


@app.post("/teacher-classes")
async def teacher_classes_submit(
    request: Request,
    user_id: int = Depends(require_permission("payments")),
):
    """Сохранить привязки класс — учитель. Пустое значение снимает привязку."""
    form = await request.form()
    for cg in range(0, 12):
        key = f"teacher_{cg}"
        val = form.get(key, "").strip()
        if val and val.isdigit():
            school_db.teacher_class_set(cg, int(val))
        else:
            school_db.teacher_class_remove(cg)
    return RedirectResponse(url="/teacher-classes?ok=1", status_code=302)


# --- Мой класс и замещение (учитель) ---

@app.get("/my-class", response_class=HTMLResponse)
async def my_class_page(request: Request, user_id: int = Depends(require_teacher_or_canteen)):
    """Страница «Мой класс»: только для учителя; свой класс и назначение замещения."""
    user = school_db.user_by_id(user_id)
    if not user or user.get("role") != "teacher":
        return RedirectResponse(url="/nutrition", status_code=302)
    data = school_db.students_for_teacher(user_id)
    substitute_info = None
    students_with_balance = []
    if data["my_class_grade"] is not None:
        substitute_info = school_db.teacher_substitute_get(
            data["my_class_grade"],
            user_id,
        )
        students_with_balance = school_db.students_with_balance_by_class(data["my_class_grade"])
    for group in data.get("substitute_classes") or []:
        cg = group.get("class_grade")
        if cg is not None:
            group["students_with_balance"] = school_db.students_with_balance_by_class(cg)
        else:
            group["students_with_balance"] = []
    other_teachers = [u for u in school_db.user_list(role="teacher") if u["id"] != user_id]
    purposes_for_charge = [p for p in school_db.payment_purpose_list() if p.get("charge_frequency") == "manual"]
    return templates.TemplateResponse(
        "my_class.html",
        {
            "request": request,
            "user": user,
            "data": data,
            "substitute_info": substitute_info,
            "other_teachers": other_teachers,
            "students_with_balance": students_with_balance,
            "purposes_for_charge": purposes_for_charge,
        },
    )


@app.post("/my-class/substitute")
async def my_class_substitute_submit(
    request: Request,
    substitute_teacher_id: int = Form(...),
    valid_until: str = Form(""),
    user_id: int = Depends(require_teacher_or_canteen),
):
    """Назначить замещающего учителя на свой класс."""
    user = school_db.user_by_id(user_id)
    if not user or user.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="Доступ только для учителя")
    my_grade = school_db.teacher_class_get_by_teacher(user_id)
    if my_grade is None:
        raise HTTPException(status_code=400, detail="Сначала вас должны назначить классным руководителем")
    valid = valid_until.strip() or None
    school_db.teacher_substitute_set(my_grade, user_id, substitute_teacher_id, valid)
    return RedirectResponse(url="/my-class?substitute=1", status_code=302)


@app.post("/my-class/substitute/remove")
async def my_class_substitute_remove(
    request: Request,
    user_id: int = Depends(require_teacher_or_canteen),
):
    """Снять замещение по своему классу."""
    user = school_db.user_by_id(user_id)
    if not user or user.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="Доступ только для учителя")
    my_grade = school_db.teacher_class_get_by_teacher(user_id)
    if my_grade is not None:
        school_db.teacher_substitute_remove(my_grade, user_id)
    return RedirectResponse(url="/my-class?removed=1", status_code=302)


def _can_add_student_charge(user_id: int, student_id: int) -> bool:
    """Может ли пользователь вносить списание для ученика: бухгалтер/директор/админ — любой; учитель — только свой класс (или по замещению)."""
    user = school_db.user_by_id(user_id)
    if not user:
        return False
    role = user.get("role")
    if role in ("administrator", "director", "accountant"):
        return True
    if role == "deputy_director":
        return school_db.deputy_has_permission(user_id, "payments")
    if role == "teacher":
        return school_db.teacher_can_charge_student(user_id, student_id)
    return False


@app.get("/student-charge/add", response_class=HTMLResponse)
async def student_charge_add_page(
    request: Request,
    student_id: int = Query(..., description="ID ученика"),
    user_id: int = Depends(require_teacher_or_canteen),
):
    """Форма внесения списания с баланса ученика (продленка, расходники и т.д.). Учитель — только для своего класса."""
    student = school_db.student_by_id(student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Ученик не найден")
    if not _can_add_student_charge(user_id, student_id):
        raise HTTPException(status_code=403, detail="Нет прав вносить списание для этого ученика")
    purposes = [p for p in school_db.payment_purpose_list() if p.get("charge_frequency") == "manual"]
    from datetime import date
    today = date.today().isoformat()
    return templates.TemplateResponse(
        "student_charge_add.html",
        {"request": request, "student": student, "purposes": purposes, "today": today},
    )


@app.post("/student-charge/add")
async def student_charge_add_submit(
    request: Request,
    student_id: int = Form(...),
    purpose_code: str = Form(...),
    amount: str = Form(...),
    charge_date: str = Form(...),
    description: str = Form(""),
    user_id: int = Depends(require_teacher_or_canteen),
):
    """Внести списание с баланса ученика. Учитель — только для своего класса."""
    student = school_db.student_by_id(student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Ученик не найден")
    if not _can_add_student_charge(user_id, student_id):
        raise HTTPException(status_code=403, detail="Нет прав вносить списание для этого ученика")
    try:
        amount_f = float(amount.replace(",", ".").strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="Укажите корректную сумму")
    if amount_f <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше 0")
    if not charge_date.strip():
        raise HTTPException(status_code=400, detail="Укажите дату списания")
    school_db.student_charge_create(
        student_id=student_id,
        purpose_code=purpose_code.strip(),
        amount=amount_f,
        charge_date=charge_date.strip(),
        created_by=user_id,
        description=description.strip() or None,
    )
    user = school_db.user_by_id(user_id)
    if user and user.get("role") == "teacher":
        return RedirectResponse(url="/my-class?charged=1", status_code=302)
    return RedirectResponse(url="/students?charged=1", status_code=302)


# --- Рассылки (директор, бухгалтер, учитель) ---

@app.get("/broadcast/channels", response_class=HTMLResponse)
async def broadcast_channels_page(request: Request, user_id: int = Depends(require_permission("broadcast"))):
    """Подключённые каналы для рассылки (общий чат ТГ, WhatsApp, МАХ)."""
    channels = school_db.broadcast_channel_list()
    return templates.TemplateResponse(
        "broadcast_channels.html",
        {"request": request, "channels": channels},
    )


@app.post("/broadcast/channels/add")
async def broadcast_channel_add(
    name: str = Form(...),
    channel_type: str = Form("telegram"),
    channel_identifier: str = Form(...),
    user_id: int = Depends(require_permission("broadcast")),
):
    """Добавить канал. Для Telegram укажите chat_id группы/канала (число или -100...)."""
    if channel_type not in ("telegram", "whatsapp", "max"):
        channel_type = "telegram"
    school_db.broadcast_channel_create(name.strip(), channel_type, channel_identifier.strip())
    return RedirectResponse(url="/broadcast/channels", status_code=302)


@app.post("/broadcast/channels/{channel_id}/delete")
async def broadcast_channel_delete(channel_id: int, user_id: int = Depends(require_permission("broadcast"))):
    school_db.broadcast_channel_delete(channel_id)
    return RedirectResponse(url="/broadcast/channels", status_code=302)


@app.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(
    request: Request,
    user_id: int = Depends(require_can_broadcast),
):
    """Рассылка: общий чат школы / класс / родители класса / учителя / отделы / ученики (учитель)."""
    user = school_db.user_by_id(user_id)
    role = user.get("role") if user else None
    teachers = school_db.user_list(role="teacher") if role in ("administrator", "director", "accountant", "teacher") else []
    departments = school_db.department_list() if role in ("administrator", "director", "accountant") else []
    students = []
    teacher_students_data = None
    if role == "teacher":
        teacher_students_data = school_db.students_for_teacher(user_id)
        # Плоский список для обратной совместимости: свой класс + классы по замещению
        students = list(teacher_students_data["my_class_students"])
        for g in teacher_students_data["substitute_classes"]:
            students.extend(g["students"])
        if not students:
            students = school_db.students_all()
    channels = school_db.broadcast_channel_list() if role in ("administrator", "director", "accountant") else []
    parents_by_class = {}
    if role in ("administrator", "director", "accountant", "teacher"):
        for g in range(0, 12):  # 0 = Детский сад, 1–11 класс
            parents_by_class[g] = school_db.parents_of_class_list(g)
    # Для учителя: список администрации (директор + бухгалтер) для рассылки
    administration = []
    if role == "teacher":
        administration = school_db.user_list(role="administrator") + school_db.user_list(role="director") + school_db.user_list(role="accountant")
    history = school_db.broadcast_list(limit=20)
    return templates.TemplateResponse(
        "broadcast.html",
        {
            "request": request,
            "role": role,
            "teachers": teachers,
            "departments": departments,
            "students": students,
            "channels": channels,
            "parents_by_class": parents_by_class,
            "administration": administration,
            "history": history,
            "teacher_students_data": teacher_students_data,
        },
    )


@app.post("/broadcast")
async def broadcast_submit(
    request: Request,
    message_text: str = Form(...),
    user_id: int = Depends(require_can_broadcast),
):
    """Создать рассылку. Сообщение уйдёт с подписью отправителя; отправка фиксируется по каждому получателю и каналу."""
    form = await request.form()
    recipient_type = (form.get("recipient_type") or "").strip()
    message_text = (message_text or "").strip()
    if not message_text:
        raise HTTPException(status_code=400, detail="Введите текст сообщения")

    user = school_db.user_by_id(user_id)
    role = user.get("role") if user else None
    recipient_ids: list[int] = []
    channel_ids: list[int] = []

    if role in ("administrator", "director", "accountant"):
        if recipient_type == "general_chat":
            for cid in form.getlist("channel_ids"):
                if isinstance(cid, str) and cid.strip().isdigit():
                    channel_ids.append(int(cid.strip()))
            if not channel_ids:
                raise HTTPException(status_code=400, detail="Выберите хотя бы один канал (общий чат)")
        elif recipient_type == "class":
            cg = form.get("class_grade")
            if cg and str(cg).strip().isdigit():
                recipient_ids = school_db.parent_user_ids_for_class(int(cg))
            if not recipient_ids:
                raise HTTPException(status_code=400, detail="В выбранном классе нет родителей с Telegram")
        elif recipient_type == "parents_of_class":
            cg = form.get("class_grade")
            for pid in form.getlist("parent_ids"):
                if isinstance(pid, str) and pid.strip().isdigit():
                    recipient_ids.append(int(pid.strip()))
            if not recipient_ids:
                raise HTTPException(status_code=400, detail="Выберите хотя бы одного родителя")
        else:
            for tid in form.getlist("teacher_ids"):
                if isinstance(tid, str) and tid.strip().isdigit():
                    recipient_ids.append(int(tid.strip()))
            for did in form.getlist("department_ids"):
                if isinstance(did, str) and did.strip().isdigit():
                    recipient_ids.extend(school_db.department_member_ids(int(did.strip())))
            recipient_ids = list(dict.fromkeys(recipient_ids))
            if not recipient_ids:
                raise HTTPException(status_code=400, detail="Выберите получателей (учителей и/или отделы)")
    elif role == "teacher":
        recipient_type_teacher = (form.get("recipient_type_teacher") or "students").strip()
        if recipient_type_teacher == "students":
            sids = [int(x.strip()) for x in form.getlist("student_ids") if isinstance(x, str) and x.strip().isdigit()]
            if sids:
                recipient_ids = list(dict.fromkeys(school_db.parent_user_ids_for_students(sids)))
            if not recipient_ids:
                raise HTTPException(status_code=400, detail="Выберите хотя бы одного ученика (сообщение получат родители)")
        elif recipient_type_teacher == "class":
            cg = form.get("class_grade_teacher")
            if cg is not None and str(cg).strip().isdigit():
                recipient_ids = school_db.parent_user_ids_for_class(int(cg))
            if not recipient_ids:
                raise HTTPException(status_code=400, detail="В выбранном классе нет родителей с Telegram")
        elif recipient_type_teacher == "teachers":
            for uid in form.getlist("teacher_ids_teacher"):
                if isinstance(uid, str) and uid.strip().isdigit():
                    recipient_ids.append(int(uid.strip()))
            recipient_ids = list(dict.fromkeys(recipient_ids))
            if not recipient_ids:
                raise HTTPException(status_code=400, detail="Выберите хотя бы одного учителя")
        elif recipient_type_teacher == "administration":
            for uid in form.getlist("admin_ids"):
                if isinstance(uid, str) and uid.strip().isdigit():
                    recipient_ids.append(int(uid.strip()))
            recipient_ids = list(dict.fromkeys(recipient_ids))
            if not recipient_ids:
                raise HTTPException(status_code=400, detail="Выберите хотя бы одного получателя (директор/бухгалтер)")
        else:
            raise HTTPException(status_code=400, detail="Выберите тип получателей")

    if not channel_ids and not recipient_ids:
        raise HTTPException(status_code=400, detail="Выберите получателей или канал для рассылки")

    # Запланированная отправка: дата + время (локальное время пользователя → сохраняем как есть)
    scheduled_date = (form.get("scheduled_date") or "").strip()
    scheduled_time = (form.get("scheduled_time") or "09:00").strip()
    scheduled_at: str | None = None
    if scheduled_date:
        if not scheduled_time:
            scheduled_time = "09:00"
        scheduled_at = f"{scheduled_date} {scheduled_time}"

    bid = school_db.broadcast_create(
        user_id, message_text,
        recipient_ids if recipient_ids else None,
        scheduled_at=scheduled_at,
    )
    if channel_ids:
        school_db.broadcast_add_channel_sends(bid, channel_ids)
    audit_log(
        _web_logger,
        "broadcast_create",
        user_id=user_id,
        role=role,
        extra={
            "broadcast_id": bid,
            "recipient_type": recipient_type,
            "recipients_count": len(recipient_ids),
            "channels_count": len(channel_ids),
        },
    )
    redirect_url = "/broadcast?sent=1" + ("&scheduled=1" if scheduled_at else "")
    return RedirectResponse(url=redirect_url, status_code=302)


# --- Питание (учитель / столовая / бухгалтер) ---

@app.get("/nutrition", response_class=HTMLResponse)
async def nutrition_index(request: Request, user_id: int = Depends(require_teacher_or_canteen)):
    """Выбор даты: расклад для столовой или ввод данных на дату."""
    user = school_db.user_by_id(user_id)
    is_staff = user and user.get("role") in ("administrator", "director", "accountant")
    return templates.TemplateResponse(
        "nutrition_index.html",
        {"request": request, "is_staff": is_staff},
    )


@app.get("/nutrition/canteen", response_class=HTMLResponse)
async def nutrition_canteen_view(
    request: Request,
    date: str = None,
    user_id: int = Depends(require_teacher_or_canteen),
):
    """Расклад для столовой на дату: кто на завтраке/обеде/ужине по классам (из планов родителей)."""
    if not date:
        from datetime import date as date_type
        date = date_type.today().isoformat()
    data = school_db.canteen_view_for_date(date)
    prices = school_db.meal_prices_get_all()
    return templates.TemplateResponse(
        "nutrition_canteen.html",
        {"request": request, "data": data, "prices": prices},
    )


@app.get("/nutrition/prices", response_class=HTMLResponse)
async def nutrition_prices_page(request: Request, user_id: int = Depends(require_permission("accounting"))):
    """Цены на питание (завтрак, обед, ужин) — только бухгалтер/директор."""
    prices = school_db.meal_prices_get_all()
    return templates.TemplateResponse(
        "nutrition_prices.html",
        {"request": request, "prices": prices},
    )


@app.post("/nutrition/prices")
async def nutrition_prices_submit(
    breakfast: str = Form("0"),
    lunch: str = Form("0"),
    dinner: str = Form("0"),
    user_id: int = Depends(require_permission("accounting")),
):
    """Сохранить цены на питание."""
    for meal, val in [("breakfast", breakfast), ("lunch", lunch), ("dinner", dinner)]:
        try:
            p = float(val.replace(",", "."))
        except ValueError:
            p = 0
        if p < 0:
            p = 0
        school_db.meal_prices_set(meal, p)
    return RedirectResponse(url="/nutrition/prices?ok=1", status_code=302)


@app.get("/nutrition/deduction-add", response_class=HTMLResponse)
async def nutrition_deduction_add_page(request: Request, user_id: int = Depends(require_permission("accounting"))):
    """Ручное списание за питание (расходная часть) — бухгалтер/директор."""
    students = school_db.students_all()
    return templates.TemplateResponse(
        "nutrition_deduction_add.html",
        {"request": request, "students": students},
    )


@app.post("/nutrition/deduction-add")
async def nutrition_deduction_add_submit(
    request: Request,
    student_id: str = Form(""),
    amount: str = Form(...),
    reason: str = Form(""),
    deduction_date: str = Form(...),
    user_id: int = Depends(require_permission("accounting")),
):
    """Внести ручное списание по ученику (отобразится у родителя в отчёте)."""
    try:
        am = float(amount.replace(",", "."))
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректная сумма")
    if am <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше 0")
    sid = int(student_id) if student_id and student_id.strip().isdigit() else None
    if not sid:
        raise HTTPException(status_code=400, detail="Выберите ученика")
    school_db.nutrition_deduction_create(
        student_id=sid,
        deduction_date=deduction_date.strip(),
        amount=am,
        is_manual=True,
        reason=reason.strip() or None,
        created_by=user_id,
    )
    return RedirectResponse(url="/nutrition/deduction-add?ok=1", status_code=302)


# --- План питания и отчёт для родителя ---

@app.get("/parent/nutrition", response_class=HTMLResponse)
async def parent_nutrition_page(request: Request, user_id: int = Depends(require_parent)):
    """План питания по умолчанию: завтрак/обед/ужин для каждого ребёнка и возможность встать на питание самому."""
    user = school_db.user_by_id(user_id)
    students = school_db.students_by_parent_id(user_id)
    plans = {}
    for s in students:
        plans[s["id"]] = school_db.student_meal_plan_get(s["id"])
    parent_plan = school_db.parent_meal_plan_get(user_id)
    return templates.TemplateResponse(
        "parent_nutrition.html",
        {"request": request, "user": user, "students": students, "plans": plans, "parent_plan": parent_plan},
    )


@app.post("/parent/nutrition")
async def parent_nutrition_submit(
    request: Request,
    user_id: int = Depends(require_parent),
):
    """Сохранить планы питания (дети + сам родитель)."""
    form = await request.form()
    students = school_db.students_by_parent_id(user_id)
    for s in students:
        sid = str(s["id"])
        school_db.student_meal_plan_set(
            s["id"],
            has_breakfast=form.get(f"child_{sid}_breakfast") == "on",
            has_lunch=form.get(f"child_{sid}_lunch") == "on",
            has_dinner=form.get(f"child_{sid}_dinner") == "on",
        )
    if form.get("parent_on_nutrition") == "on":
        school_db.parent_meal_plan_set(
            user_id,
            has_breakfast=form.get("parent_breakfast") == "on",
            has_lunch=form.get("parent_lunch") == "on",
            has_dinner=form.get("parent_dinner") == "on",
        )
    else:
        school_db.parent_meal_plan_remove(user_id)
    return RedirectResponse(url="/parent/nutrition?ok=1", status_code=302)


@app.get("/parent/nutrition-report", response_class=HTMLResponse)
async def parent_nutrition_report_page(
    request: Request,
    month: str = None,
    user_id: int = Depends(require_parent),
):
    """Отчёт по питанию за месяц: списания и пополнения по каждому ребёнку (и по родителю, если на питании)."""
    from datetime import date as date_type, timedelta
    if month:
        try:
            y, m = int(month[:4]), int(month[5:7])
            date_from = f"{y}-{m:02d}-01"
            if m == 12:
                last = date_type(y + 1, 1, 1) - timedelta(days=1)
            else:
                last = date_type(y, m + 1, 1) - timedelta(days=1)
            date_to = last.strftime("%Y-%m-%d")
        except (ValueError, IndexError):
            date_from = date_to = None
            month = None
    else:
        today = date_type.today()
        date_from = today.replace(day=1).isoformat()
        date_to = today.isoformat()
        month = date_from[:7]
    user = school_db.user_by_id(user_id)
    students = school_db.students_by_parent_id(user_id)
    report = []
    for s in students:
        balance = school_db.balance_canteen_for_student(s["id"])
        deductions = school_db.nutrition_deductions_for_student(s["id"], date_from, date_to) if date_from else []
        payments_all = school_db.payments_by_student(s["id"])
        payments = [p for p in payments_all if p.get("purpose") == "food" and date_from and date_to and (date_from <= (p.get("created_at") or "")[:10] <= date_to)]
        report.append({
            "student": s,
            "balance": balance,
            "deductions": deductions,
            "payments": payments,
            "total_deductions": sum(d["amount"] for d in deductions),
            "total_payments": sum((p.get("amount_received") or p.get("amount") or 0) for p in payments if p.get("status") == "confirmed"),
        })
    parent_deductions = school_db.nutrition_deductions_for_parent(user_id, date_from, date_to) if date_from else []
    return templates.TemplateResponse(
        "parent_nutrition_report.html",
        {
            "request": request,
            "user": user,
            "month": month,
            "date_from": date_from,
            "date_to": date_to,
            "report": report,
            "parent_deductions": parent_deductions,
        },
    )


@app.get("/nutrition/enter", response_class=HTMLResponse)
async def nutrition_enter_page(
    request: Request,
    date: str = None,
    user_id: int = Depends(require_teacher_or_canteen),
):
    """Форма ввода питания на дату. Учитель видит только свой класс и классы по замещению."""
    if not date:
        return RedirectResponse(url="/nutrition", status_code=302)
    user = school_db.user_by_id(user_id)
    role = user.get("role") if user else None
    students_grouped = None
    if role == "teacher":
        data = school_db.students_for_teacher(user_id)
        students = list(data["my_class_students"])
        for g in data["substitute_classes"]:
            students.extend(g["students"])
        if students:
            students_grouped = data
        else:
            students = school_db.students_all()
    else:
        students = school_db.students_all()
    existing = {r["student_id"]: r for r in school_db.daily_nutrition_by_date(date)}
    return templates.TemplateResponse(
        "nutrition_enter.html",
        {"request": request, "date": date, "students": students, "students_grouped": students_grouped, "existing": existing},
    )


@app.post("/nutrition/enter")
async def nutrition_enter_submit(
    request: Request,
    date: str = Form(...),
    user_id: int = Depends(require_teacher_or_canteen),
):
    """Сохранить данные по питанию на дату. Учитель может сохранять только по своим классам."""
    form = await request.form()
    user = school_db.user_by_id(user_id)
    role = user.get("role") if user else None
    if role == "teacher":
        data = school_db.students_for_teacher(user_id)
        students = list(data["my_class_students"])
        for g in data["substitute_classes"]:
            students.extend(g["students"])
        if not students:
            students = school_db.students_all()
    else:
        students = school_db.students_all()
    for s in students:
        sid = str(s["id"])
        breakfast = form.get(f"breakfast_{sid}") == "on"
        lunch = form.get(f"lunch_{sid}") == "on"
        snack = form.get(f"snack_{sid}") == "on"
        comment = form.get(f"comment_{sid}", "").strip() or None
        school_db.daily_nutrition_upsert(
            student_id=s["id"],
            nutrition_date=date,
            entered_by=user_id,
            had_breakfast=breakfast,
            had_lunch=lunch,
            had_snack=snack,
            comment=comment,
        )
    return RedirectResponse(url=f"/nutrition/enter?date={date}", status_code=302)


# --- Отчёт директора (фильтры, экспорт) ---

@app.get("/report", response_class=HTMLResponse)
async def report_page(
    request: Request,
    date_from: str = None,
    date_to: str = None,
    student_id: str = None,
    class_grade: str = None,
    purpose: str = None,
    status: str = None,
    amount_min: str = None,
    amount_max: str = None,
    user_id: int = Depends(require_director_or_deputy("report")),
):
    """Отчёт по платежам с фильтрами; результаты на той же странице."""
    students = school_db.students_all()
    sid = int(student_id) if student_id and student_id.isdigit() else None
    cg = int(class_grade) if class_grade and class_grade.isdigit() else None
    am_min = float(amount_min) if amount_min else None
    am_max = float(amount_max) if amount_max else None
    payments = school_db.payments_report(
        date_from=date_from,
        date_to=date_to,
        student_id=sid,
        class_grade=cg,
        purpose=purpose or None,
        status=status or None,
        amount_min=am_min,
        amount_max=am_max,
    )
    totals = school_db.report_totals(date_from=date_from, date_to=date_to)
    purposes = school_db.payment_purpose_list()
    pending_total = sum(totals.get("pending_" + p["code"], 0) for p in purposes)
    return templates.TemplateResponse(
        "report.html",
        {
            "request": request,
            "students": students,
            "payments": payments,
            "totals": totals,
            "purpose_options": _purpose_options(),
            "purposes": purposes,
            "pending_total": pending_total,
            "filters": {
                "date_from": date_from or "",
                "date_to": date_to or "",
                "student_id": student_id or "",
                "class_grade": class_grade or "",
                "purpose": purpose or "",
                "status": status or "",
                "amount_min": amount_min or "",
                "amount_max": amount_max or "",
            },
        },
    )


@app.get("/report/export")
async def report_export_csv(
    request: Request,
    date_from: str = None,
    date_to: str = None,
    student_id: str = None,
    class_grade: str = None,
    purpose: str = None,
    status: str = None,
    amount_min: str = None,
    amount_max: str = None,
    user_id: int = Depends(require_director_or_deputy("report")),
):
    """Скачать отчёт в CSV."""
    import csv
    from io import StringIO
    from fastapi.responses import StreamingResponse
    sid = int(student_id) if student_id and student_id.isdigit() else None
    cg = int(class_grade) if class_grade and class_grade.isdigit() else None
    am_min = float(amount_min) if amount_min else None
    am_max = float(amount_max) if amount_max else None
    rows = school_db.payments_report(
        date_from=date_from, date_to=date_to, student_id=sid,
        class_grade=cg,
        purpose=purpose or None, status=status or None,
        amount_min=am_min, amount_max=am_max,
    )
    output = StringIO()
    writer = csv.writer(output)
    purpose_options = _purpose_options()
    writer.writerow(["Дата", "Ученик", "Класс", "Сумма", "Назначение", "Статус"])
    for r in rows:
        pur = r.get("purpose", "")
        writer.writerow([
            (r.get("created_at") or "")[:10],
            r.get("student_name", ""),
            r.get("class_grade", ""),
            r.get("amount"),
            purpose_options.get(pur, pur),
            r.get("status", ""),
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=report.csv"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
