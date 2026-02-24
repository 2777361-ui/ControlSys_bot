"""
Обработка очереди рассылок: веб создаёт задачи, бот отправляет в каналы (ТГ/и т.д.) и конкретным пользователям.
В сообщение добавляется подпись отправителя. Фиксируется отправка каждому получателю и в каждый канал.
"""
import asyncio
import logging

from aiogram import Bot

from bot.school_db import (
    broadcast_mark_channel_failed,
    broadcast_mark_channel_sent,
    broadcast_mark_recipient_failed,
    broadcast_mark_recipient_sent,
    broadcast_mark_sending,
    broadcast_mark_task_finished,
    broadcast_channel_sends_pending,
    broadcast_recipients_pending,
    broadcast_pending_task,
    user_by_id,
)

logger = logging.getLogger(__name__)


def _message_with_sender(text: str, created_by: int) -> str:
    """Добавить подпись отправителя к тексту рассылки."""
    user = user_by_id(created_by)
    name = (user.get("full_name") or "Школа").strip()
    if not name:
        name = "Школа"
    return f"{text.strip()}\n\n— {name}"


async def process_broadcast_queue(bot: Bot) -> None:
    """
    Взять одну задачу рассылки: отправить в каналы (общий чат ТГ и т.д.), затем каждому пользователю.
    Сообщение содержит подпись отправителя. Отправка фиксируется по каждому получателю и каналу.
    """
    task = broadcast_pending_task()
    if not task:
        return
    broadcast_id = task["id"]
    raw_text = task["message_text"] or ""
    if not raw_text.strip():
        broadcast_mark_task_finished(broadcast_id)
        return

    text = _message_with_sender(raw_text, task["created_by"])
    broadcast_mark_sending(broadcast_id)
    logger.info(
        "AUDIT | broadcast_start | broadcast_id=%s | created_by=%s | channels_and_recipients",
        broadcast_id,
        task["created_by"],
    )

    # Сначала — отправка в каналы (общий чат ТГ, позже WhatsApp/МАХ)
    channel_sends = broadcast_channel_sends_pending(broadcast_id)
    for cs in channel_sends:
        if cs.get("channel_type") == "telegram":
            try:
                chat_id = cs["channel_identifier"].strip()
                if chat_id.lstrip("-").isdigit():
                    chat_id = int(chat_id)
                await bot.send_message(chat_id=chat_id, text=text)
                broadcast_mark_channel_sent(cs["id"])
                logger.info(
                    "AUDIT | broadcast_channel_sent | broadcast_id=%s | channel_id=%s | channel_name=%s",
                    broadcast_id, cs["id"], cs.get("channel_name"),
                )
            except Exception as e:
                logger.warning(
                    "AUDIT | broadcast_channel_failed | broadcast_id=%s | channel_id=%s | channel_name=%s | error=%s",
                    broadcast_id, cs["id"], cs.get("channel_name"), e,
                )
                broadcast_mark_channel_failed(cs["id"], str(e))
        else:
            broadcast_mark_channel_failed(cs["id"], "Отправка в этот канал пока не настроена")
            logger.info(
                "AUDIT | broadcast_channel_skipped | broadcast_id=%s | channel_type=%s",
                broadcast_id, cs.get("channel_type"),
            )
        await asyncio.sleep(0.05)

    # Затем — конкретным пользователям (фиксация по каждому)
    recipients = broadcast_recipients_pending(broadcast_id)
    for rec in recipients:
        try:
            await bot.send_message(chat_id=rec["telegram_id"], text=text)
            broadcast_mark_recipient_sent(rec["id"])
            logger.info(
                "AUDIT | broadcast_recipient_sent | broadcast_id=%s | recipient_id=%s | user_id=%s",
                broadcast_id, rec["id"], rec.get("user_id"),
            )
        except Exception as e:
            logger.warning(
                "AUDIT | broadcast_recipient_failed | broadcast_id=%s | recipient_id=%s | user_id=%s | error=%s",
                broadcast_id, rec["id"], rec.get("user_id"), e,
            )
            broadcast_mark_recipient_failed(rec["id"], str(e))
        await asyncio.sleep(0.05)

    broadcast_mark_task_finished(broadcast_id)
    logger.info("Рассылка %s завершена", broadcast_id)
