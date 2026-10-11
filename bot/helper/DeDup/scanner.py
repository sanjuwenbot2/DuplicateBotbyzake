
import asyncio
from html import escape
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4
from pyrogram.errors import FloodWait
from ... import LOGGER
from ...core.config_manager import Config
from . import hasher
from .status import finish_status, send_status, update_status

_UNKNOWN = object()


def _file_record_name(media, media_type):
    name = getattr(media, "file_name", None)
    if name:
        return name
    if media_type == "video":
        return f"video_{getattr(media, 'file_unique_id', None) or 'unknown'}.mp4"
    return None


class ScanState:
    def __init__(self, channel_id, chat_name="Unknown chat", by="Unknown", owner_id=None):
        self.channels = (
            [int(value) for value in channel_id]
            if isinstance(channel_id, (list, tuple))
            else [int(channel_id)]
        )
        self.channel_index = 0
        if isinstance(chat_name, dict):
            self.chat_names = {str(key): value for key, value in chat_name.items()}
            self.chat_name = self.chat_names.get(str(self.channels[0]), str(self.channels[0]))
        else:
            self.chat_name = chat_name
            self.chat_names = {str(self.channels[0]): chat_name}
        self.owner_id = int(owner_id) if owner_id is not None else None
        self.channel_metrics = {
            str(value): {
                "messages_scanned": 0,
                "files_seen": 0,
                "duplicates_found": 0,
                "ids_processed": 0,
                "hashes_computed": 0,
                "elapsed_seconds": 0.0,
            }
            for value in self.channels
        }
        self.last_ids = {}
        self.cursors = {}
        self.files_seen = 0
        self.ids_processed = 0
        self.messages_scanned = 0
        self.duplicates_found = 0
        self.hashes_computed = 0
        self.skipped_channels = []
        self.status = "starting"
        self.progress_chat_id = None
        self.progress_msg_id = None
        self.command_message = None
        self.elapsed_base = 0.0
        self.run_started = None
        self.channel_started_at = None
        self.stop_requested = False
        self.scan_id = uuid4().hex
        self.by = by
        self.added_at = datetime.now(timezone.utc)
        self.finished_at = None

    @property
    def channel_id(self):
        return self.channels[min(self.channel_index, len(self.channels) - 1)]

    def chat_title(self, channel_id):
        return self.chat_names.get(str(channel_id), str(channel_id))

    def metrics(self, channel_id):
        return self.channel_metrics.setdefault(str(channel_id), {
            "messages_scanned": 0,
            "files_seen": 0,
            "duplicates_found": 0,
            "ids_processed": 0,
            "hashes_computed": 0,
            "elapsed_seconds": 0.0,
        })

    def channel_elapsed(self, channel_id):
        elapsed = float(self.metrics(channel_id).get("elapsed_seconds", 0.0))
        if int(channel_id) == self.channel_id and self.channel_started_at is not None:
            elapsed += monotonic() - self.channel_started_at
        return elapsed

    def elapsed_seconds(self):
        elapsed = self.elapsed_base or 0.0
        if self.run_started is not None:
            elapsed += monotonic() - self.run_started
        return elapsed

    def to_document(self):
        channel_metrics = {key: dict(value) for key, value in self.channel_metrics.items()}
        if self.channel_started_at is not None:
            current = channel_metrics.setdefault(str(self.channel_id), {})
            current["elapsed_seconds"] = (
                float(current.get("elapsed_seconds", 0.0))
                + monotonic()
                - self.channel_started_at
            )
        return {
            "channel_id": self.channel_id,
            "channels": self.channels,
            "channel_index": self.channel_index,
            "chat_names": self.chat_names,
            "channel_metrics": channel_metrics,
            "owner_id": self.owner_id,
            "last_ids": {str(k): v for k, v in self.last_ids.items()},
            "cursors": {str(k): v for k, v in self.cursors.items()},
            "files_seen": self.files_seen,
            "ids_processed": self.ids_processed,
            "messages_scanned": self.messages_scanned,
            "duplicates_found": self.duplicates_found,
            "hashes_computed": self.hashes_computed,
            "skipped_channels": self.skipped_channels,
            "status": self.status,
            "progress_chat_id": self.progress_chat_id,
            "progress_msg_id": self.progress_msg_id,
            "elapsed_base": self.elapsed_seconds(),
            "scan_id": self.scan_id,
            "chat_name": self.chat_name,
            "by": self.by,
            "added_at": self.added_at,
            "finished_at": self.finished_at,
        }

    def load_document(self, document):
        self.channels = [int(x) for x in document.get("channels") or self.channels]
        self.channel_index = int(document.get("channel_index", 0))
        self.chat_names = {str(k): v for k, v in (document.get("chat_names") or self.chat_names).items()}
        self.channel_metrics = document.get("channel_metrics") or self.channel_metrics
        self.owner_id = document.get("owner_id", self.owner_id)
        self.last_ids = {int(k): v for k, v in (document.get("last_ids") or {}).items()}
        self.cursors = {int(k): v for k, v in (document.get("cursors") or {}).items()}
        self.files_seen = int(document.get("files_seen", 0))
        self.ids_processed = int(document.get("ids_processed", 0))
        self.messages_scanned = int(document.get("messages_scanned", self.ids_processed))
        self.duplicates_found = int(document.get("duplicates_found", 0))
        self.hashes_computed = int(document.get("hashes_computed", 0))
        self.skipped_channels = [int(x) for x in document.get("skipped_channels") or []]
        self.status = document.get("status", "stopped")
        self.progress_chat_id = document.get("progress_chat_id")
        self.progress_msg_id = document.get("progress_msg_id")
        self.elapsed_base = float(document.get("elapsed_base", 0.0))
        self.scan_id = document.get("scan_id") or uuid4().hex
        self.chat_name = document.get("chat_name", self.chat_name)
        self.by = document.get("by", "Unknown")
        self.added_at = document.get("added_at") or datetime.now(timezone.utc)
        self.finished_at = document.get("finished_at")


class ScanManager:

    def __init__(self, pool, db, delete_pool=None, channels=None):
        self.pool = pool
        self.db = db
        self.delete_pool = delete_pool or pool
        self.state = None
        self.task = None
        self._size_locks = {}
        self._locks_guard = asyncio.Lock()
        self._excluded = {}
        self._last_checkpoint = 0.0
        self._stall = 0

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def request_stop(self):
        if self.state is not None and self.running:
            self.state.stop_requested = True

    async def start(
        self,
        message,
        channel_id,
        chat_name="Unknown chat",
        by="Unknown",
        resume=False,
        owner_id=None,
        show_status=True,
    ):
        if self.running:
            return False
        channel_ids = (
            [int(value) for value in channel_id]
            if isinstance(channel_id, (list, tuple))
            else [int(channel_id)]
        )
        if resume:
            document = await self.db.dedup_get_progress(channel_ids[0], owner_id)
            if document is None:
                return False
            state = ScanState(channel_ids, chat_name, by, owner_id)
            state.load_document(document)
            if chat_name:
                if isinstance(chat_name, dict):
                    state.chat_names.update({str(key): value for key, value in chat_name.items()})
                if not isinstance(chat_name, dict):
                    state.chat_names[str(state.channels[0])] = chat_name
            state.chat_name = state.chat_title(state.channels[0])
            state.by = by or state.by
        else:
            state = ScanState(channel_ids, chat_name, by, owner_id)
            for channel in state.channels:
                await self.db.dedup_delete_all_files(channel, owner_id)
        self.state = state
        state.command_message = message
        self._excluded = {}
        self._size_locks = {}
        self._stall = 0
        self._last_checkpoint = monotonic()
        state.stop_requested = False
        state.status = "running"
        state.run_started = monotonic()
        self.show_status = show_status
        self.report_chat_id = message.chat.id if hasattr(message, "chat") else int(message)
        if show_status:
            await send_status(message, state, self.pool)
        await self._checkpoint(force=True)
        self.task = asyncio.create_task(self._run())
        return True

    async def _run(self):
        state = self.state
        updater = asyncio.create_task(self._progress_loop())
        try:
            for index in range(state.channel_index, len(state.channels)):
                state.channel_index = index
                state.status = "running"
                state.channel_started_at = monotonic()
                if self.show_status:
                    await update_status(state, self.pool, force=True)
                await self._scan_channel(state.channels[index])
                metric = state.metrics(state.channels[index])
                metric["elapsed_seconds"] = (
                    float(metric.get("elapsed_seconds", 0.0))
                    + monotonic()
                    - state.channel_started_at
                )
                state.channel_started_at = None
                if state.stop_requested:
                    state.status = "stopped"
                    await self._checkpoint(force=True)
                    if self.show_status:
                        await update_status(state, self.pool, force=True, finished=True)
                    return
            state.status = "completed"
            state.finished_at = datetime.now(timezone.utc)
            reports = []
            from ..ext_utils.status_utils import build_dedup_summary
            for channel_id in state.channels:
                if channel_id in state.skipped_channels:
                    reports.append("\n".join([
                        f"╭━━━「 ⚠ {escape(str(state.chat_title(channel_id)))} SCAN REPORT 」━━━╮",
                        "┃",
                        "┃  ⚠ SCAN FAILED — no helper bot could access this chat.",
                        f"┃  ▸ Chat ID › <code>{channel_id}</code>",
                        "╰━━━━━━━━━━━━━━━━━━━━━━╯",
                    ]))
                    continue
                duplicates = await self.db.dedup_get_duplicates(channel_id, state.scan_id, state.owner_id)
                state.metrics(channel_id)["duplicates_found"] = len(duplicates)
                summary = state.to_document()
                summary.update({
                    "scan_id": state.scan_id,
                    "chat_name": state.chat_title(channel_id),
                    "duplicates_found": len(duplicates),
                    "pending_deletions": len(duplicates),
                    "updated_at": state.finished_at,
                    "status": "completed",
                    "owner_id": state.owner_id,
                    "channel_metrics": {str(channel_id): state.metrics(channel_id)},
                })
                await self.db.dedup_save_scan(channel_id, summary, state.owner_id)
                await self.db.dedup_prune_old_results(channel_id, state.scan_id, state.owner_id)
                await self.db.dedup_delete_all_files(channel_id, state.owner_id)
                await self.db.dedup_delete_progress(channel_id, state.owner_id)
                reports.append(build_dedup_summary(state, channel_id))
            if self.show_status:
                await finish_status(state, self.pool, "\n\n".join(reports))
        except asyncio.CancelledError:
            state.status = "stopped"
            if state.channel_started_at is not None:
                metric = state.metrics(state.channel_id)
                metric["elapsed_seconds"] = (
                    float(metric.get("elapsed_seconds", 0.0))
                    + monotonic()
                    - state.channel_started_at
                )
                state.channel_started_at = None
            await self._checkpoint(force=True)
            if self.show_status:
                await update_status(state, self.pool, force=True, finished=True)
            raise
        except Exception as error:
            LOGGER.exception("DeDup scan failed")
            state.status = f"error: {error}"
            if state.channel_started_at is not None:
                metric = state.metrics(state.channel_id)
                metric["elapsed_seconds"] = (
                    float(metric.get("elapsed_seconds", 0.0))
                    + monotonic()
                    - state.channel_started_at
                )
                state.channel_started_at = None
            try:
                await self._checkpoint(force=True)
            except Exception:
                LOGGER.exception("Could not save DeDup error checkpoint")
            try:
                await update_status(state, self.pool, force=True, finished=True)
            except Exception:
                LOGGER.exception("Could not edit DeDup error status")
        finally:
            updater.cancel()
            try:
                await updater
            except asyncio.CancelledError:
                pass
            state.elapsed_base = state.elapsed_seconds()
            state.run_started = None

    async def _progress_loop(self):
        while True:
            await asyncio.sleep(max(1.0, float(Config.PROGRESS_UPDATE_INTERVAL)))
            try:
                if self.show_status:
                    await update_status(self.state, self.pool)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("DeDup status update failed")

    async def _scan_channel(self, channel_id):
        state = self.state
        if channel_id not in state.last_ids:
            last_id = await self._detect_last_id(channel_id)
            if last_id is None:
                state.skipped_channels.append(channel_id)
                return
            state.last_ids[channel_id] = last_id
            if channel_id in state.skipped_channels:
                state.skipped_channels.remove(channel_id)
        last_id = state.last_ids[channel_id]
        cursor = state.cursors.get(channel_id, 1)
        while cursor <= last_id and not state.stop_requested:
            state.cursors[channel_id] = cursor
            await self._checkpoint(force=False)
            available = len(self.pool.available())
            if available == 0:
                await asyncio.sleep(min(self.pool._next_wakeup(), 5.0))
                continue
            chunks = self._chunks(cursor, last_id, available)
            results = await asyncio.gather(
                *(self._process_range(channel_id, start, end) for start, end in chunks)
            )
            advance = 0
            for (start, end), ok in zip(chunks, results):
                if start != cursor + advance or not ok:
                    break
                advance = end - cursor + 1
            if advance:
                cursor += advance
                state.ids_processed += advance
                state.metrics(channel_id)["ids_processed"] += advance
                self._stall = 0
            else:
                self._stall += 1
                if self._stall >= Config.MAX_RETRIES:
                    LOGGER.warning("Skipping repeatedly failing message range at %d in %s", cursor, channel_id)
                    cursor += min(Config.BATCH_SIZE, last_id - cursor + 1)
                    self._stall = 0
            state.cursors[channel_id] = cursor
        await self._checkpoint(force=True)

    @staticmethod
    def _chunks(cursor, last_id, count):
        chunks, start = [], cursor
        for _ in range(count):
            if start > last_id:
                break
            end = min(last_id, start + Config.BATCH_SIZE - 1)
            chunks.append((start, end))
            start = end + 1
        return chunks

    async def _process_range(self, channel_id, start, end):
        ids = list(range(start, end + 1))
        exclude = set(self._excluded.get(channel_id, ()))
        async def factory(client):
            return await client.get_messages(channel_id, ids, replies=0)
        try:
            messages = await self.pool.call(factory, timeout=Config.REQUEST_TIMEOUT, exclude=exclude)
        except Exception as error:
            LOGGER.warning("Range %d-%d in %s failed: %s", start, end, channel_id, error)
            return False
        if messages is None:
            return True
        if not isinstance(messages, (list, tuple)):
            messages = [messages]
        for message in messages:
            if message is not None and not getattr(message, "empty", False):
                self.state.messages_scanned += 1
                self.state.metrics(channel_id)["messages_scanned"] += 1
            await self._handle_message(channel_id, message)
        return True

    async def _handle_message(self, channel_id, message):
        if message is None or getattr(message, "empty", False):
            return
        media = getattr(message, "document", None) or getattr(message, "video", None)
        if media is None:
            return
        size = getattr(media, "file_size", None)
        if not size:
            return
        media_type = "document" if getattr(message, "document", None) is not None else "video"
        await self._dedup(channel_id, message.id, media, media_type, size, message)

    async def _size_lock(self, file_size):
        async with self._locks_guard:
            return self._size_locks.setdefault(file_size, asyncio.Lock())

    def _record(self, channel_id, message_id, media, media_type, file_size, md5):
        return {
            "_id": self.db.dedup_file_key(f"{channel_id}#{message_id}", self.state.owner_id),
            "channel_id": int(channel_id),
            "owner_id": self.state.owner_id,
            "msg_id": int(message_id),
            "file_name": _file_record_name(media, media_type),
            "file_size": file_size,
            "file_unique_id": getattr(media, "file_unique_id", None),
            "mime_type": getattr(media, "mime_type", None),
            "media_type": media_type,
            "md5": md5,
            "created_at": datetime.now(timezone.utc),
        }

    async def _stage_duplicate(self, channel_id, message_id, media, media_type, file_size, matched_with):
        await self.db.dedup_save_duplicate({
            "channel_id": int(channel_id),
            "scan_id": self.state.scan_id,
            "owner_id": self.state.owner_id,
            "msg_id": int(message_id),
            "file_name": _file_record_name(media, media_type),
            "file_size": file_size,
            "matched_with": int(matched_with),
            "found_at": datetime.now(timezone.utc),
        })
        self.state.duplicates_found += 1
        self.state.metrics(channel_id)["duplicates_found"] += 1

    async def _stage_record_duplicate(self, record, matched_with):
        await self.db.dedup_save_duplicate({
            "channel_id": int(record["channel_id"]),
            "scan_id": self.state.scan_id,
            "owner_id": self.state.owner_id,
            "msg_id": int(record["msg_id"]),
            "file_name": record.get("file_name"),
            "file_size": record["file_size"],
            "matched_with": int(matched_with),
            "found_at": datetime.now(timezone.utc),
        })
        self.state.duplicates_found += 1
        self.state.metrics(record["channel_id"])["duplicates_found"] += 1

    async def _dedup(self, channel_id, message_id, media, media_type, file_size, message):
        file_id = f"{channel_id}#{message_id}"
        unique_id = getattr(media, "file_unique_id", None)
        async with await self._size_lock(file_size):
            if await self.db.dedup_file_exists(file_id, self.state.owner_id):
                return
            self.state.files_seen += 1
            self.state.metrics(channel_id)["files_seen"] += 1
            other = await self.db.dedup_find_by_unique_id(unique_id, file_id, channel_id, self.state.owner_id)
            if other is not None:
                if other["msg_id"] < message_id:
                    await self._stage_duplicate(channel_id, message_id, media, media_type, file_size, other["msg_id"])
                else:
                    await self._stage_record_duplicate(other, message_id)
                    await self.db.dedup_delete_file(other["_id"])
                    await self.db.dedup_insert_file(
                        self._record(
                            channel_id, message_id, media, media_type, file_size, other.get("md5")
                        )
                    )
                return
            same_size = await self.db.dedup_find_by_size(file_size, file_id, channel_id, self.state.owner_id)
            if not same_size:
                await self.db.dedup_insert_file(
                    self._record(channel_id, message_id, media, media_type, file_size, None)
                )
                return
            digest = await hasher.compute_md5(self.pool, message, file_size, channel_id, message_id)
            self.state.hashes_computed += 1
            self.state.metrics(channel_id)["hashes_computed"] += 1
            for record in same_size:
                record_digest = record.get("md5")
                if record_digest is None:
                    record_digest = await self._lazy_hash(record)
                    if record_digest is None:
                        await self.db.dedup_delete_file(record["_id"])
                        continue
                    if record_digest is _UNKNOWN:
                        continue
                if record_digest != digest:
                    continue
                if record["msg_id"] < message_id:
                    await self._stage_duplicate(channel_id, message_id, media, media_type, file_size, record["msg_id"])
                    return
                await self._stage_record_duplicate(record, message_id)
                await self.db.dedup_delete_file(record["_id"])
            await self.db.dedup_insert_file(self._record(channel_id, message_id, media, media_type, file_size, digest))

    async def _lazy_hash(self, record):
        async def factory(client):
            return await client.get_messages(record["channel_id"], record["msg_id"], replies=0)
        try:
            message = await self.pool.call(factory, timeout=Config.REQUEST_TIMEOUT)
        except Exception as error:
            LOGGER.warning(
                "Could not fetch existing message %s in %s: %s",
                record.get("msg_id"),
                record.get("channel_id"),
                error,
            )
            return _UNKNOWN
        if message is None or getattr(message, "empty", False):
            return None
        media = getattr(message, "document", None) or getattr(message, "video", None)
        if media is None or getattr(media, "file_size", None) != record["file_size"]:
            return None
        digest = await hasher.compute_md5(
            self.pool,
            message,
            record["file_size"],
            record["channel_id"],
            record["msg_id"],
        )
        self.state.hashes_computed += 1
        self.state.metrics(record["channel_id"])["hashes_computed"] += 1
        await self.db.dedup_set_md5(record["_id"], digest)
        return digest

    async def _detect_last_id(self, channel_id):
        results = await asyncio.gather(
            *(self._sentinel(channel_id, client) for client in self.pool.clients),
            return_exceptions=True,
        )
        last_id = None
        for client, result in zip(self.pool.clients, results):
            if isinstance(result, BaseException) or result is None:
                self._excluded.setdefault(channel_id, set()).add(client)
                if isinstance(result, BaseException):
                    LOGGER.warning("Helper %s cannot access chat %s: %s", client.name, channel_id, result)
                continue
            last_id = result if last_id is None else max(last_id, result)
        if last_id is None:
            LOGGER.warning("No helper could resolve chat %s", channel_id)
        return last_id

    async def _sentinel(self, channel_id, client):
        try:
            sent = await client.send_message(channel_id, Config.SENTINEL_TEXT)
            await client.delete_messages(channel_id, sent.id)
            return sent.id
        except FloodWait as error:
            self.pool.cool(client, getattr(error, "value", 0))
            raise

    async def _checkpoint(self, force):
        now = monotonic()
        if not force and now - self._last_checkpoint < Config.CHECKPOINT_INTERVAL:
            return
        self._last_checkpoint = now
        document = self.state.to_document()
        for channel_id in self.state.channels:
            channel_document = dict(document)
            channel_document["channel_id"] = channel_id
            await self.db.dedup_save_progress(channel_document, channel_id, self.state.owner_id)
