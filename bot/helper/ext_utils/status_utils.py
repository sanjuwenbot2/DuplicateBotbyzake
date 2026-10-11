

from html import escape
from re import findall
from datetime import datetime, timezone as dt_timezone
from pytz import timezone


SIZE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def get_readable_file_size(size_in_bytes):
    if not size_in_bytes:
        return "0B"

    index = 0
    while size_in_bytes >= 1024 and index < len(SIZE_UNITS) - 1:
        size_in_bytes /= 1024
        index += 1

    return f"{size_in_bytes:.2f}{SIZE_UNITS[index]}"


def get_readable_time(seconds: int):
    periods = [("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]
    result = ""
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            result += f"{int(period_value)}{period_name}"
    return result


def get_raw_time(time_str: str) -> int:
    time_units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    return sum(
        int(value) * time_units[unit]
        for value, unit in findall(r"(\d+)([dhms])", time_str)
    )


def time_to_seconds(time_duration):
    try:
        parts = time_duration.split(":")
        if len(parts) == 3:
            hours, minutes, seconds = map(float, parts)
        elif len(parts) == 2:
            hours = 0
            minutes, seconds = map(float, parts)
        elif len(parts) == 1:
            hours = 0
            minutes = 0
            seconds = float(parts[0])
        else:
            return 0
        return hours * 3600 + minutes * 60 + seconds
    except (AttributeError, TypeError, ValueError):
        return 0


def get_progress_bar_string(pct):
    pct = float(str(pct).strip("%"))
    p = min(max(pct, 0), 100)
    full = int(p // 8)
    return f"[{('⬤' * full) + ('□' * (12 - full))}]"


def format_dedup_size(size):
    if size is None:
        return "0 B"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024.0:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} PiB"


def format_dedup_time(seconds):
    if seconds is None:
        return "n/a"
    seconds = int(seconds)
    if seconds < 0:
        return "n/a"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def format_dedup_percent(done, total):
    if not total:
        return "0.0%"
    return f"{done * 100.0 / total:.1f}%"


def _dedup_clock(seconds):
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def _dedup_local(value):
    value = value or datetime.now(dt_timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_timezone.utc)
    return value.astimezone(timezone("Asia/Kolkata"))


def build_dedup_status(pool, state):
    active = state.channel_index if state.status == "running" else -1
    queued = max(0, len(state.channels) - active - 1) if active >= 0 else 0
    lines = [
        "╭━━━「 ⌕ @TheZake SCANNER 」━━━╮",
        "┃",
        f'┃  ◉ Active Scans  »  "{1 if active >= 0 else 0}"',
        f'┃  ◷ Queued Scans  »  "{queued}"',
        f'┃  ▣ Total Chats   »  "{len(state.channels)}"',
        "┃",
        "╰━━━━━━━━━━━━━━━━━━━━━━╯",
    ]
    for index, channel_id in enumerate(state.channels):
        metrics = state.metrics(channel_id)
        if index < state.channel_index:
            status = "FAILED" if channel_id in state.skipped_channels else "COMPLETED"
        elif index == state.channel_index and state.status == "running":
            status = "RUNNING"
        elif state.status == "stopped" and index == state.channel_index:
            status = "CANCELLED"
        elif state.status.startswith("error") and index == state.channel_index:
            status = "ERROR"
        else:
            status = "WAITING"
        marker = chr(0x2460 + index) if index < 20 else str(index + 1)
        lines.extend([
            "",
            f"┏━━「 {marker} CHAT #{index + 1} 」━━┓",
            f"┃  ◷ Status     : {status}",
            f"┃  ▸ Chat ID    : <code>{channel_id}</code>",
            f"┃  ▸ Chat Name  : {escape(str(state.chat_title(channel_id)))}",
        ])
        if status == "WAITING":
            lines.extend([
                "┃  ━━━━━━━━━━━━━━━━━━",
                f"┃  ▫ Queue Pos. : #{index + 1}",
                "┃  ◷ Waiting for scanner...",
            ])
        else:
            elapsed = state.channel_elapsed(channel_id)
            speed = metrics.get("messages_scanned", 0) / elapsed if elapsed > 0 else 0
            lines.extend([
                "┃  ━━━━━━━━━━━━━━━━━━",
                f"┃  ▫ Messages   : {metrics.get('messages_scanned', 0):,}",
                f"┃  ▫ Media      : {metrics.get('files_seen', 0):,}",
                f"┃  ▫ Duplicates : {metrics.get('duplicates_found', 0):,}",
                f"┃  ↯ Speed      : {speed:.1f} msg/s",
                f"┃  ◷ Elapsed    : {_dedup_clock(elapsed)}",
            ])
        lines.append("┗━━━━━━━━━━━━━━━━━━━┛")
    lines.append("\n✺ Bot by ▸ @LeechBots")
    return "\n".join(lines)


def build_dedup_summary(state, channel_id):
    metrics = state.metrics(channel_id)
    done = _dedup_local(state.finished_at)
    added = _dedup_local(state.added_at)
    status = "SCAN COMPLETED" if channel_id not in state.skipped_channels else "SCAN FAILED"
    title = escape(str(state.chat_title(channel_id)))
    return "\n".join([
        f"╭━━━「 ✓ {title} SCAN REPORT 」━━━╮",
        "┃",
        f"┃  ✓ {status}",
        "┃",
        "┣━━━━━━━━━━━━━━━━━━━━━━",
        f'┃  ▣ Messages Scanned › "{metrics.get("messages_scanned", 0):,}"',
        f'┃  ▣ Media Scanned    › "{metrics.get("files_seen", 0):,}"',
        f'┃  ⌫ Duplicates Found › "{metrics.get("duplicates_found", 0):,}"',
        "┣━━━━━━━━━━━━━━━━━━━━━━",
        f'┃  ◷ Elapsed    › "{_dedup_clock(metrics.get("elapsed_seconds", 0))}"',
        f'┃  ▷ Added On   › "{added.strftime("%d %b %Y")}"',
        f'┃  ♙ By         › "{escape(str(state.by))}"',
        f'┃  ◴ At         › "{done.strftime("%I:%M:%S %p")} (IST)"',
        "╰━━━━━━━━━━━━━━━━━━━━━━╯",
        "✺ Bot by ▸ @LeechBots",
    ])
