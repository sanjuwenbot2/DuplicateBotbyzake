from datetime import datetime
from html import escape
from uuid import uuid4
from pytz import timezone
from pyrogram.enums import ButtonStyle
from .. import LOGGER
from ..core.config_manager import Config
from ..core.tg_client import TgClient
from ..helper.ext_utils.db_handler import database
from ..helper.DeDup.clients import BotPool
from ..helper.DeDup.scanner import ScanManager
from ..helper.DeDup.status import update_status
from ..helper.ext_utils.status_utils import format_dedup_time
from ..helper.telegram_helper.button_build import ButtonMaker
from ..helper.telegram_helper.message_utils import delete_message, edit_message, send_message

_scan_manager = None
_SCAN_PROMPTS = {}
_SELECTIONS = {}
_DEVELOPER_URL = "https://t.me/TheZake"


def _parse_channel_ids(raw):
    values = str(raw or "").split()
    if not values:
        raise ValueError("Usage: /scan <chat_id> [chat_id ...]")
    result = []
    for value in values:
        try:
            channel_id = int(value)
        except ValueError:
            raise ValueError(f"Invalid numeric chat ID: {value}") from None
        if channel_id >= 0:
            raise ValueError(f"Chat ID must be a negative group/channel ID: {value}")
        if channel_id not in result:
            result.append(channel_id)
    return result


def _manager():
    global _scan_manager
    if _scan_manager is None or not _scan_manager.running:
        _scan_manager = ScanManager(
            BotPool(list(TgClient.helper_bots.values())),
            database,
            BotPool([TgClient.user]) if TgClient.user else None,
        )
    return _scan_manager


def _prerequisite(scan=False):
    if not Config.DATABASE_URL or database.db is None:
        return "DeDup needs DATABASE_URL and an active MongoDB connection."
    if scan and not TgClient.helper_bots:
        return "No HELPER_TOKENS client is running; configure and restart the bot."
    return None


def _user_id(user):
    return int(user.id) if user else int(Config.OWNER_ID)


def _user_name(user):
    if not user:
        return "Unknown"
    return user.username or " ".join(x for x in [user.first_name, user.last_name] if x) or str(user.id)


def _developer_close_buttons(user_id):
    buttons = ButtonMaker()
    buttons.url_button("Developer", _DEVELOPER_URL)
    buttons.data_button("Close", f"ddup {user_id} close", style=ButtonStyle.DANGER)
    return buttons.build_menu(2)


async def _chat_name(channel_id):
    clients = list(TgClient.helper_bots.values()) or ([TgClient.bot] if TgClient.bot else [])
    for client in clients:
        try:
            chat = await client.get_chat(channel_id)
            return chat.title or chat.first_name or str(channel_id)
        except Exception:
            continue
    return str(channel_id)


def _new_token():
    return uuid4().hex[:10]


async def _begin_scan(message, channel_ids, resume=False, user=None):
    manager = _manager()
    try:
        await database.dedup_ensure_indexes()
        names = {channel_id: await _chat_name(channel_id) for channel_id in channel_ids}
        started = await manager.start(
            message,
            channel_ids,
            chat_name=names,
            by=_user_name(user or message.from_user),
            owner_id=_user_id(user or message.from_user),
            resume=resume,
        )
    except Exception as error:
        LOGGER.exception("Unable to start DeDup scan")
        await send_message(message, f"Could not start scan: {escape(str(error))}")
        return
    if not started:
        await send_message(message, "Could not start the scan. Another scan may already be running.")


async def dedup_scan(_, message):
    if error := _prerequisite(scan=True):
        await send_message(message, error)
        return
    try:
        parts = message.text.split(maxsplit=1)
        channel_ids = _parse_channel_ids(parts[1] if len(parts) > 1 else "")
    except ValueError as error:
        await send_message(message, str(error))
        return
    if _scan_manager is not None and _scan_manager.running:
        await send_message(message, "A DeDup scan is already running. Try the queued chat IDs in one /scan command.")
        return
    owner_id = _user_id(message.from_user)
    try:
        await database.dedup_ensure_indexes()
        saved_progress = None
        for channel_id in channel_ids:
            saved_progress = await database.dedup_get_progress(channel_id, owner_id)
            if saved_progress:
                break
    except Exception as error:
        await send_message(message, f"Could not access DeDup storage: {escape(str(error))}")
        return
    if saved_progress:
        token = _new_token()
        resume_channels = saved_progress.get("channels") or channel_ids
        _SCAN_PROMPTS[token] = {
            "channel_ids": channel_ids,
            "resume_channels": [int(value) for value in resume_channels],
            "owner_id": owner_id,
        }
        buttons = ButtonMaker()
        buttons.data_button("Resume", f"ddup:resume:{token}", style=ButtonStyle.SUCCESS)
        buttons.data_button("Restart", f"ddup:restart:{token}", style=ButtonStyle.DANGER)
        await send_message(
            message,
            f"A saved scan exists for <code>{saved_progress.get('channel_id', channel_ids[0])}</code>. "
            "Resume it or restart the chat queue?",
            buttons.build_menu(2),
        )
        return
    await _begin_scan(message, channel_ids)


async def dedup_scan_callback(_, query):
    if not query.message:
        await query.answer("This scan prompt is unavailable.", show_alert=True)
        return
    _, action, token = query.data.split(":", 2)
    session = _SCAN_PROMPTS.get(token)
    if not session or int(query.from_user.id) != session["owner_id"]:
        await query.answer("This scan prompt has expired or belongs to another user.", show_alert=True)
        return
    if action not in {"resume", "restart"}:
        await query.answer("Unknown scan action.", show_alert=True)
        return
    if _scan_manager is not None and _scan_manager.running:
        await query.answer("A DeDup scan is already running.", show_alert=True)
        return
    _SCAN_PROMPTS.pop(token, None)
    await query.answer(f"{'Resuming' if action == 'resume' else 'Restarting'} scan.")
    channels = session["resume_channels"] if action == "resume" else session["channel_ids"]
    command_message = query.message.reply_to_message or query.message
    if command_message.id != query.message.id:
        await delete_message(query.message)
    await _begin_scan(
        command_message,
        channels,
        resume=action == "resume",
        user=query.from_user,
    )


async def dedup_status_callback(_, query):
    if query.data == "ddup:cancel":
        if _scan_manager is None or not _scan_manager.running:
            await query.answer("No scan is currently running.")
            return
        _scan_manager.request_stop()
        await query.answer("Cancelling after the current batch.")
        return
    if query.data == "ddup:refresh":
        if _scan_manager is None or not _scan_manager.running:
            await query.answer("No scan is currently running.")
            return
        await update_status(_scan_manager.state, _scan_manager.pool, force=True)
        await query.answer("Status refreshed.")


async def dedup_cancel(_, message):
    if _scan_manager is None or not _scan_manager.running:
        await send_message(message, "No DeDup scan is currently running.")
        return
    _scan_manager.request_stop()
    await send_message(message, "Cancelling the scan after the current message batch.")


def _local_time(value=None):
    value = value or datetime.utcnow()
    if value.tzinfo is None:
        value = timezone("UTC").localize(value)
    return value.astimezone(timezone("Asia/Kolkata"))


def _report_scan_metrics(scan, channel_id):
    return (scan.get("channel_metrics") or {}).get(str(channel_id), {})


def _format_delete_report(scan, channel_id, deleted, failed, elapsed, actor):
    metrics = _report_scan_metrics(scan, channel_id)
    now = _local_time()
    title = escape(str(scan.get("chat_name", channel_id)))
    added = _local_time(scan.get("added_at"))
    return "\n".join([
        f"╭━━━「 ✓ {title} DELETION REPORT 」━━━╮",
        "┃",
        "┃  ✓ DELETION COMPLETED",
        "┃",
        "┣━━━━━━━━━━━━━━━━━━━━━━",
        f"┃  ▣ Messages Scanned › \"{metrics.get('messages_scanned', scan.get('messages_scanned', 0)):,}\"",
        f"┃  ⌫ Duplicates Found › \"{scan.get('duplicates_found', 0):,}\"",
        f"┃  ✓ Messages Deleted › \"{deleted:,}\"",
        f"┃  ⚠ Failed           › \"{failed:,}\"",
        "┣━━━━━━━━━━━━━━━━━━━━━━",
        f"┃  ◷ Elapsed    › \"{format_dedup_time(elapsed)}\"",
        f"┃  ▷ Added On   › \"{added.strftime('%d %b %Y')}\"",
        f"┃  ♙ By         › \"{escape(_user_name(actor))}\"",
        f"┃  ◴ At         › \"{now.strftime('%I:%M:%S %p')} (IST)\"",
        "╰━━━━━━━━━━━━━━━━━━━━━━╯",
        "✺ Bot by ▸ @LeechBots",
    ])


async def _delete_one(channel_id, scan, actor, response_chat_id):
    owner_id = scan.get("owner_id")
    started = datetime.now(timezone("UTC"))
    records = await database.dedup_get_duplicates(channel_id, scan["scan_id"], owner_id)
    deleted_ids, failed = [], 0
    for record in records:
        msg_id = int(record["msg_id"])
        try:
            removed = await TgClient.user.delete_messages(channel_id, msg_id)
            if removed is None or removed > 0:
                deleted_ids.append(msg_id)
            else:
                failed += 1
        except Exception as error:
            LOGGER.warning("Could not delete duplicate message %s in %s: %s", msg_id, channel_id, error)
            failed += 1
    if deleted_ids:
        await database.dedup_delete_duplicates(channel_id, scan["scan_id"], deleted_ids, owner_id)
    scan["pending_deletions"] = max(0, int(scan.get("pending_deletions", len(records))) - len(deleted_ids))
    scan["last_delete_at"] = datetime.now(timezone("UTC"))
    scan["updated_at"] = scan["last_delete_at"]
    scan["last_delete_by"] = _user_name(actor)
    await database.dedup_save_scan(channel_id, scan, owner_id)
    elapsed = (datetime.now(timezone("UTC")) - started).total_seconds()
    return await send_message(
        response_chat_id,
        _format_delete_report(scan, channel_id, len(deleted_ids), failed, elapsed, actor),
        buttons=_developer_close_buttons(_user_id(actor)),
    )


def _selection_text(session):
    scans = session["scans"]
    selected = session["selected"]
    action_title = "DELETE MANAGER" if session["mode"] == "delete" else "CLEAR MANAGER"
    lines = [
        f"╭━━━「 ⌫ {action_title} 」━━━╮",
        "┃",
        "┃  ▣ SCANNED CHATS",
        f"┃  ├ Total Chats: \"{len(scans)}\"",
        f"┃  └ Select chats to {session['mode']}.",
        "┃",
        "┣━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for index, scan in enumerate(scans):
        channel_id = int(scan["channel_id"])
        lines.extend([
            f"┃  \"{index + 1}.\" {escape(str(scan.get('chat_name', channel_id)))}",
            f"┃  ├ ID: \"{channel_id}\"",
            f"┃  └ Duplicates: \"{scan.get('pending_deletions', scan.get('duplicates_found', 0))}\"",
        ])
        if index + 1 < len(scans):
            lines.append("┃")
    lines.extend([
        "┣━━━━━━━━━━━━━━━━━━━━━━",
        "┃",
        "┃  ✦ HOW TO SELECT",
        "┃  ▸ Tap a chat to select or unselect it",
        "┃  ▸ Select All — select every chat",
        "┃  ▸ Done — apply to selected chats",
        "┃  ▸ Cancel — close this menu",
        "╰━━━━━━━━━━━━━━━━━━━━━━╯",
    ])
    return "\n".join(lines)


def _selection_keyboard(token, session):
    buttons = ButtonMaker()
    for index, scan in enumerate(session["scans"]):
        channel_id = int(scan["channel_id"])
        selected = channel_id in session["selected"]
        mark = "☑" if selected else "☐"
        title = str(scan.get("chat_name", channel_id))[:30]
        count = scan.get("pending_deletions", scan.get("duplicates_found", 0))
        buttons.data_button(
            f"{mark} {title} ({count})",
            f"ddup:{session['mode']}:toggle:{token}:{index}",
        )
    all_selected = len(session["selected"]) == len(session["scans"])
    buttons.data_button(
        "☑ Select All" if all_selected else "☐ Select All",
        f"ddup:{session['mode']}:all:{token}",
        position="footer",
    )
    buttons.data_button("✓ Done", f"ddup:{session['mode']}:done:{token}", position="footer")
    buttons.data_button("✖ Cancel", f"ddup:{session['mode']}:cancel:{token}", position="footer")
    return buttons.build_menu(2, f_cols=2)


async def _show_selection(message, mode, owner_id):
    try:
        if mode == "clear":
            scans = await database.dedup_list_saved_data(owner_id)
        else:
            scans = await database.dedup_list_scans(owner_id)
            available = []
            for scan in scans:
                if not scan.get("scan_id"):
                    continue
                duplicates = await database.dedup_get_duplicates(
                    scan["channel_id"], scan["scan_id"], owner_id
                )
                scan["pending_deletions"] = len(duplicates)
                if duplicates:
                    available.append(scan)
            scans = available
    except Exception as error:
        await send_message(message, f"Could not read saved chat data: {escape(str(error))}")
        return
    if not scans:
        await send_message(message, "No saved chats were found for your account.")
        return
    token = _new_token()
    session = {
        "mode": mode,
        "owner_id": owner_id,
        "scans": scans,
        "selected": set(),
        "chat_id": message.chat.id,
        "command_message": message,
    }
    _SELECTIONS[token] = session
    await send_message(
        message,
        _selection_text(session),
        buttons=_selection_keyboard(token, session),
    )


async def dedup_delete(_, message):
    if error := _prerequisite():
        await send_message(message, error)
        return
    if _scan_manager is not None and _scan_manager.running:
        await send_message(message, "Wait for the active scan to finish before deleting duplicates.")
        return
    if not TgClient.user:
        await send_message(message, "USER_SESSION_STRING is required to delete duplicate messages.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) == 1 or parts[1].strip().lower() == "all":
        await _show_selection(message, "delete", _user_id(message.from_user))
        return
    try:
        channel_id = _parse_channel_ids(parts[1])[0]
        scan = await database.dedup_get_scan(channel_id, _user_id(message.from_user))
    except ValueError as error:
        await send_message(message, str(error))
        return
    except Exception as error:
        await send_message(message, f"Could not read scan data: {escape(str(error))}")
        return
    if not scan or not scan.get("scan_id"):
        await send_message(message, f"No completed scan data is saved for <code>{channel_id}</code>.")
        return
    await _delete_one(channel_id, scan, message.from_user, message)


async def dedup_clear(_, message):
    if error := _prerequisite():
        await send_message(message, error)
        return
    if _scan_manager is not None and _scan_manager.running:
        await send_message(message, "Wait for the active scan to finish before clearing scan data.")
        return
    parts = message.text.split(maxsplit=1)
    owner_id = _user_id(message.from_user)
    if len(parts) == 1:
        await _show_selection(message, "clear", owner_id)
        return
    target = parts[1].strip().lower()
    if target == "all":
        count = await database.dedup_clear_user_data(owner_id)
        await send_message(
            message.chat.id,
            f"Cleared DeDup data for <code>{count}</code> chat(s).",
            buttons=_developer_close_buttons(owner_id),
        )
        return
    try:
        channel_id = _parse_channel_ids(parts[1])[0]
    except ValueError as error:
        await send_message(message, str(error))
        return
    await database.dedup_clear_channel_data(channel_id, owner_id)
    await send_message(
        message.chat.id,
        f"Cleared saved DeDup data for <code>{channel_id}</code>.",
        buttons=_developer_close_buttons(owner_id),
    )


async def dedup_selection_callback(_, query):
    parts = query.data.split(":")
    if len(parts) < 4:
        await query.answer("This selection is invalid.", show_alert=True)
        return
    _, mode, action, token, *extra = parts
    session = _SELECTIONS.get(token)
    if not session or int(query.from_user.id) != session["owner_id"]:
        await query.answer("This selection has expired or belongs to another user.", show_alert=True)
        return
    if action == "toggle" and extra:
        index = int(extra[0])
        if index < 0 or index >= len(session["scans"]):
            await query.answer("Chat selection is invalid.", show_alert=True)
            return
        channel_id = int(session["scans"][index]["channel_id"])
        if channel_id in session["selected"]:
            session["selected"].remove(channel_id)
        else:
            session["selected"].add(channel_id)
        await edit_message(query.message, _selection_text(session), buttons=_selection_keyboard(token, session))
        await query.answer("Selection updated.")
        return
    if action == "all":
        ids = {int(row["channel_id"]) for row in session["scans"]}
        session["selected"] = set() if session["selected"] == ids else ids
        await edit_message(query.message, _selection_text(session), buttons=_selection_keyboard(token, session))
        await query.answer("Selection updated.")
        return
    if action == "cancel":
        _SELECTIONS.pop(token, None)
        await delete_message(query.message)
        await query.answer("Selection cancelled.")
        return
    if action != "done":
        await query.answer("Unknown selection action.", show_alert=True)
        return
    if not session["selected"]:
        await query.answer("Select at least one chat first.", show_alert=True)
        return
    selected = list(session["selected"])
    scans_by_id = {int(row["channel_id"]): row for row in session["scans"]}
    _SELECTIONS.pop(token, None)
    await query.answer("Processing selected chats.")
    await delete_message(query.message)
    if mode == "delete":
        if _scan_manager is not None and _scan_manager.running:
            await send_message(session["chat_id"], "Wait for the active scan to finish before deleting duplicates.")
            return
        if not TgClient.user:
            await send_message(session["chat_id"], "USER_SESSION_STRING is required for deletion.")
            return
        for channel_id in selected:
            scan = scans_by_id[channel_id]
            await _delete_one(
                channel_id,
                scan,
                query.from_user,
                session["command_message"],
            )
    else:
        if _scan_manager is not None and _scan_manager.running:
            await send_message(session["chat_id"], "Wait for the active scan to finish before clearing scan data.")
            return
        for channel_id in selected:
            await database.dedup_clear_channel_data(channel_id, session["owner_id"])
        await send_message(
            session["chat_id"],
            f"Cleared saved DeDup data for <code>{len(selected)}</code> chat(s).",
            buttons=_developer_close_buttons(session["owner_id"]),
        )


async def dedup_close_callback(_, query):
    data = query.data.split()
    user_id = query.from_user.id
    if user_id != int(data[1]):
        await query.answer("Not yours!", show_alert=True)
    elif data[2] == "close":
        await query.answer()
        await delete_message(query.message, query.message.reply_to_message)


async def dedup_list(_, message):
    if error := _prerequisite():
        await send_message(message, error)
        return
    try:
        scans = await database.dedup_list_scans(_user_id(message.from_user))
    except Exception as error:
        await send_message(message, f"Could not read scan history: {escape(str(error))}")
        return
    if not scans:
        await send_message(message, "No chats have been scanned yet.")
        return
    lines = ["<b>Saved DeDup scan history</b>"]
    for row in scans[:30]:
        updated = _local_time(row.get("updated_at") or row.get("finished_at"))
        lines.append(
            f"• <b>{escape(str(row.get('chat_name', 'Unknown chat')))}</b> — <code>{row.get('channel_id')}</code>\n"
            f"  Duplicates: <code>{row.get('duplicates_found', 0)}</code> | "
            f"Pending delete: <code>{row.get('pending_deletions', 0)}</code>\n"
            f"  Scanned: {updated.strftime('%d %b %Y %I:%M %p')} IST"
        )
    await send_message(message, "\n".join(lines)[:4000])
