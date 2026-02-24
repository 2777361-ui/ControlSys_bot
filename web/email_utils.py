"""
Отправка писем по SMTP (например, ссылка для сброса пароля).

Переменные окружения:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, BASE_URL (для ссылок в письме).
Если SMTP не настроен, send_password_reset_email возвращает False и письмо не отправляется.
"""
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def get_smtp_config() -> dict | None:
    """Проверить, настроена ли отправка почты. Возвращает dict с настройками или None."""
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        return None
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    from_addr = os.getenv("SMTP_FROM", user or "").strip()
    base_url = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    return {
        "host": host,
        "port": port,
        "user": user or None,
        "password": password,
        "from_addr": from_addr or user,
        "base_url": base_url,
    }


def send_email(to: str, subject: str, body_text: str, body_html: str | None = None) -> bool:
    """
    Отправить письмо. body_text — обязательный текст, body_html — опционально.
    Возвращает True при успехе, False при ошибке или если SMTP не настроен.
    """
    cfg = get_smtp_config()
    if not cfg:
        logger.warning("SMTP не настроен: SMTP_HOST не задан")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = cfg["from_addr"]
        msg["To"] = to
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        if body_html:
            msg.attach(MIMEText(body_html, "html", "utf-8"))
        with smtplib.SMTP(cfg["host"], cfg["port"]) as server:
            if cfg.get("user") and cfg.get("password"):
                server.starttls()
                server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from_addr"], [to], msg.as_string())
        logger.info("Письмо отправлено на %s, тема: %s", to, subject)
        return True
    except Exception as e:
        logger.exception("Ошибка отправки письма на %s: %s", to, e)
        return False


def send_password_reset_email(to_email: str, reset_link: str, expires_hours: int = 24) -> bool:
    """
    Отправить письмо со ссылкой для сброса пароля.
    reset_link — полный URL страницы сброса (включая token).
    """
    subject = "Восстановление пароля — Школьная система"
    body_text = (
        f"Здравствуйте.\n\n"
        f"Вы запросили сброс пароля. Перейдите по ссылке, чтобы задать новый пароль:\n\n"
        f"{reset_link}\n\n"
        f"Ссылка действительна {expires_hours} ч. Если вы не запрашивали сброс, проигнорируйте это письмо.\n\n"
        "— Школьная система"
    )
    body_html = (
        f"<p>Здравствуйте.</p>"
        f"<p>Вы запросили сброс пароля. <a href=\"{reset_link}\">Нажмите здесь, чтобы задать новый пароль</a>.</p>"
        f"<p>Ссылка действительна {expires_hours} ч. Если вы не запрашивали сброс, проигнорируйте это письмо.</p>"
        "<p>— Школьная система</p>"
    )
    return send_email(to_email, subject, body_text, body_html)
