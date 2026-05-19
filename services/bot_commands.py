"""Меню слэш-команд в Telegram (кнопка ⌘ у поля ввода)."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand

logger = logging.getLogger(__name__)

DEFAULT_BOT_COMMANDS: tuple[BotCommand, ...] = (
    BotCommand(command="start", description="Запустить бота"),
    BotCommand(command="send", description="Запустить рассылку"),
    BotCommand(command="stop", description="Остановить рассылку"),
    BotCommand(command="stat", description="Статус рассылки"),
)


async def register_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(list(DEFAULT_BOT_COMMANDS))
    logger.info(
        "Меню команд Telegram: %s",
        ", ".join(f"/{c.command}" for c in DEFAULT_BOT_COMMANDS),
    )
