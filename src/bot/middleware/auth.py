"""
Middleware авторизации: подставляет текущего пользователя из БД по telegram_id.

Как работает: при каждом сообщении от пользователя мы смотрим его telegram_id,
ищем запись в таблице users. Если нашли — кладём её в data["school_user"],
чтобы в хендлерах можно было проверить роль (родитель, бухгалтер, директор).
"""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.school_db import user_by_telegram_id


class SchoolAuthMiddleware(BaseMiddleware):
    """Добавляет в data ключ school_user: dict | None — данные пользователя из БД или None."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # У сообщений и callback есть from_user
        user = getattr(event, "from_user", None)
        if user:
            data["school_user"] = user_by_telegram_id(user.id)
        else:
            data["school_user"] = None
        return await handler(event, data)
