import asyncio
from time import monotonic
from pyrogram.enums import ButtonStyle
from ..ext_utils.status_utils import build_dedup_status
from ..telegram_helper.button_build import ButtonMaker
from ..telegram_helper.message_utils import delete_message, edit_message, send_message

STATUS_MESSAGES = {}
STATUS_LOCK = asyncio.Lock()
DEVELOPER_URL = "https://t.me/TheZake"


def status_keyboard(running=True):
    buttons = ButtonMaker()
    if running:
        buttons.data_button("Refresh", "ddup:refresh", style=ButtonStyle.PRIMARY)
        buttons.data_button("Cancel scan", "ddup:cancel", style=ButtonStyle.DANGER)
    buttons.url_button("Developer", DEVELOPER_URL)
    return buttons.build_menu(2)


async def send_status(message, state, pool):
    chat_id = message.chat.id
    state.progress_chat_id = chat_id
    async with STATUS_LOCK:
        old = STATUS_MESSAGES.pop(chat_id, None)
        if old and old.get("message"):
            await delete_message(old["message"])
        text = build_dedup_status(pool, state)
        status_message = await send_message(message, text, buttons=status_keyboard())
        if status_message is not None and hasattr(status_message, "id"):
            STATUS_MESSAGES[chat_id] = {
                "message": status_message,
                "updated_at": monotonic(),
                "text": text,
            }
            state.progress_chat_id = chat_id
            state.progress_msg_id = status_message.id
            return status_message
    return None


async def update_status(state, pool, force=False, finished=False):
    chat_id = state.progress_chat_id
    if chat_id is None:
        return
    now = monotonic()
    async with STATUS_LOCK:
        entry = STATUS_MESSAGES.get(chat_id)
        if not entry or not entry.get("message"):
            return
        if not force and now - entry["updated_at"] < 3:
            return
        text = build_dedup_status(pool, state)
        if text == entry["text"] and not finished:
            entry["updated_at"] = now
            return
        result = await edit_message(
            entry["message"],
            text,
            buttons=status_keyboard(running=not finished),
        )
        if isinstance(result, str) and result.startswith("Telegram says: [400"):
            STATUS_MESSAGES.pop(chat_id, None)
            return
        entry["text"] = text
        entry["updated_at"] = now


async def finish_status(state, pool, text):
    chat_id = state.progress_chat_id
    if chat_id is None:
        return None
    async with STATUS_LOCK:
        entry = STATUS_MESSAGES.pop(chat_id, None)
        if entry and entry.get("message"):
            await delete_message(entry["message"])
        return await send_message(
            state.command_message or chat_id,
            text,
            buttons=status_keyboard(running=False),
        )


async def forget_status(chat_id):
    async with STATUS_LOCK:
        STATUS_MESSAGES.pop(chat_id, None)
