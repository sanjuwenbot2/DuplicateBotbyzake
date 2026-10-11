from importlib import import_module

from aiofiles import open as aiopen
from aiofiles.os import path as aiopath
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError
from pymongo.server_api import ServerApi

from ... import LOGGER, user_data
from ...core.config_manager import Config
from ...core.tg_client import TgClient, db_partition_id


def _bot_id():
    if TgClient.ID:
        return str(TgClient.ID)
    return Config.BOT_TOKEN.split(":", 1)[0]


def _part():
    if not TgClient.PARTITION:
        TgClient.PARTITION = db_partition_id(_bot_id())
    return TgClient.PARTITION


class DbManager:
    def __init__(self):
        self._return = True
        self._conn = None
        self.db = None

    async def connect(self):
        try:
            if self._conn is not None:
                await self._conn.close()
            self._conn = AsyncMongoClient(
                Config.DATABASE_URL, server_api=ServerApi("1")
            )
            self.db = self._conn.flashmirror
            self._return = False
        except PyMongoError as e:
            LOGGER.error(f"Error in DB connection: {e}")
            self.db = None
            self._return = True
            self._conn = None

    async def disconnect(self):
        self._return = True
        if self._conn is not None:
            await self._conn.close()
        self._conn = None

    async def update_deploy_config(self):
        if self._return:
            return
        settings = import_module("config")
        config_file = {
            key: value.strip() if isinstance(value, str) else value
            for key, value in vars(settings).items()
            if not key.startswith("__")
        }
        await self.db.settings.deployConfig.replace_one(
            {"_id": _part()}, config_file, upsert=True
        )

    async def update_config(self, dict_):
        if self._return:
            return
        await self.db.settings.config.update_one(
            {"_id": _part()}, {"$set": dict_}, upsert=True
        )

    async def update_private_file(self, path):
        if self._return:
            return
        db_path = path.replace(".", "__")
        if await aiopath.exists(path):
            async with aiopen(path, "rb+") as pf:
                pf_bin = await pf.read()
            await self.db.settings.files.update_one(
                {"_id": _part()}, {"$set": {db_path: pf_bin}}, upsert=True
            )
            if path == "config.py":
                await self.update_deploy_config()
        else:
            await self.db.settings.files.update_one(
                {"_id": _part()}, {"$unset": {db_path: ""}}, upsert=True
            )

    async def update_user_data(self, user_id):
        if self._return:
            return
        data = user_data.get(user_id, {})
        data = data.copy()

        await self.db.users[_part()].update_one(
            {"_id": user_id}, {"$set": data}, upsert=True
        )

    async def update_user_doc(self, user_id, key, path=""):
        if self._return:
            return
        if path:
            async with aiopen(path, "rb+") as doc:
                doc_bin = await doc.read()
            await self.db.users[_part()].update_one(
                {"_id": user_id}, {"$set": {key: doc_bin}}, upsert=True
            )
        else:
            await self.db.users[_part()].update_one(
                {"_id": user_id}, {"$unset": {key: ""}}, upsert=True
            )

    async def get_pm_uids(self):
        if self._return:
            return
        return [doc["_id"] async for doc in self.db.pm_users[_part()].find({})]

    async def set_pm_users(self, user_id):
        if self._return:
            return
        if not bool(await self.db.pm_users[_part()].find_one({"_id": user_id})):
            await self.db.pm_users[_part()].insert_one({"_id": user_id})
            LOGGER.info(f"New PM User Added : {user_id}")

    async def rm_pm_user(self, user_id):
        if self._return:
            return
        await self.db.pm_users[_part()].delete_one({"_id": user_id})

    async def trunc_table(self, name):
        if self._return:
            return
        await self.db[name][_part()].drop()

    def _dedup_collections(self):
        if self.db is None or self._return:
            raise RuntimeError("Database is not connected")
        return (
            self.db[Config.FILES_COLLECTION],
            self.db[Config.PROGRESS_COLLECTION],
        )

    def _dedup_partition(self):
        return str(_part())

    def _dedup_file_id(self, file_id, owner_id=None):
        suffix = f"user:{int(owner_id)}:" if owner_id is not None else ""
        return f"{self._dedup_partition()}:{suffix}{file_id}"

    def dedup_file_key(self, file_id, owner_id=None):
        return self._dedup_file_id(file_id, owner_id)

    def _dedup_progress_id(self):
        return f"{self._dedup_partition()}:{Config.PROGRESS_DOC_ID}"

    def _dedup_channel_progress_id(self, channel_id, owner_id=None):
        suffix = f":user:{int(owner_id)}" if owner_id is not None else ""
        return f"{self._dedup_partition()}:channel:{int(channel_id)}{suffix}"

    def _dedup_scan_id(self, channel_id, owner_id=None):
        suffix = f":user:{int(owner_id)}" if owner_id is not None else ""
        return f"{self._dedup_partition()}:channel:{int(channel_id)}{suffix}"

    def _dedup_result_collections(self):
        if self.db is None or self._return:
            raise RuntimeError("Database is not connected")
        return self.db["dedup_results"], self.db["dedup_scans"]

    def _dedup_result_id(self, channel_id, scan_id, message_id):
        return f"{self._dedup_partition()}:{int(channel_id)}:{scan_id}:{int(message_id)}"

    async def dedup_save_duplicate(self, record):
        results, _ = self._dedup_result_collections()
        data = dict(record)
        data["_partition"] = self._dedup_partition()
        data["_id"] = self._dedup_result_id(
            data["channel_id"], data["scan_id"], data["msg_id"]
        )
        await results.replace_one({"_id": data["_id"]}, data, upsert=True)

    async def dedup_get_duplicates(self, channel_id, scan_id, owner_id=None):
        results, _ = self._dedup_result_collections()
        cursor = results.find(
            {
                "_partition": self._dedup_partition(),
                "channel_id": int(channel_id),
                "scan_id": scan_id,
            }
        )
        if owner_id is not None:
            cursor = results.find(
                {
                    "_partition": self._dedup_partition(),
                    "channel_id": int(channel_id),
                    "scan_id": scan_id,
                    "owner_id": int(owner_id),
                }
            )
        cursor = cursor.sort("msg_id", 1)
        return await cursor.to_list(length=None)

    async def dedup_delete_duplicates(self, channel_id, scan_id, message_ids=None, owner_id=None):
        results, _ = self._dedup_result_collections()
        query = {
            "_partition": self._dedup_partition(),
            "channel_id": int(channel_id),
            "scan_id": scan_id,
        }
        if message_ids is not None:
            query["msg_id"] = {"$in": [int(item) for item in message_ids]}
        if owner_id is not None:
            query["owner_id"] = int(owner_id)
        await results.delete_many(query)

    async def dedup_save_scan(self, channel_id, document, owner_id=None):
        _, scans = self._dedup_result_collections()
        data = dict(document)
        owner_id = data.get("owner_id", owner_id)
        data["_id"] = self._dedup_scan_id(channel_id, owner_id)
        data["_partition"] = self._dedup_partition()
        data["channel_id"] = int(channel_id)
        if owner_id is not None:
            data["owner_id"] = int(owner_id)
        await scans.replace_one({"_id": data["_id"]}, data, upsert=True)

    async def dedup_get_scan(self, channel_id, owner_id=None):
        _, scans = self._dedup_result_collections()
        document = await scans.find_one(
            {
                "_id": self._dedup_scan_id(channel_id, owner_id),
                "_partition": self._dedup_partition(),
            }
        )
        return document

    async def dedup_list_scans(self, owner_id=None):
        _, scans = self._dedup_result_collections()
        query = {"_partition": self._dedup_partition()}
        if owner_id is not None:
            query["owner_id"] = int(owner_id)
        cursor = scans.find(query).sort("updated_at", -1)
        return await cursor.to_list(length=None)

    async def dedup_list_saved_data(self, owner_id):
        scans = await self.dedup_list_scans(owner_id)
        _, progress = self._dedup_collections()
        cursor = progress.find({"_partition": self._dedup_partition(), "owner_id": int(owner_id)})
        checkpoints = await cursor.to_list(length=None)
        known = {int(row["channel_id"]) for row in scans}
        for row in checkpoints:
            channel_id = row.get("channel_id")
            if channel_id is None or int(channel_id) in known:
                continue
            scans.append({
                "channel_id": int(channel_id),
                "chat_name": (row.get("chat_names") or {}).get(str(channel_id), row.get("chat_name", str(channel_id))),
                "scan_id": row.get("scan_id"),
                "duplicates_found": row.get("duplicates_found", 0),
                "pending_deletions": row.get("duplicates_found", 0),
                "updated_at": row.get("added_at"),
                "owner_id": int(owner_id),
                "status": row.get("status", "stopped"),
                "progress_only": True,
            })
            known.add(int(channel_id))
        def sort_key(row):
            value = row.get("updated_at") or row.get("finished_at")
            return value.timestamp() if hasattr(value, "timestamp") else 0
        scans.sort(key=sort_key, reverse=True)
        return scans

    async def dedup_prune_old_results(self, channel_id, keep_scan_id, owner_id=None):
        results, _ = self._dedup_result_collections()
        query = {
            "_partition": self._dedup_partition(),
            "channel_id": int(channel_id),
            "scan_id": {"$ne": keep_scan_id},
        }
        if owner_id is not None:
            query["owner_id"] = int(owner_id)
        await results.delete_many(query)

    async def dedup_ensure_indexes(self):
        files, progress = self._dedup_collections()
        await files.create_index([("_partition", 1), ("owner_id", 1), ("channel_id", 1), ("file_size", 1)])
        await files.create_index([("_partition", 1), ("owner_id", 1), ("channel_id", 1), ("file_unique_id", 1)])
        await progress.create_index([("_partition", 1), ("owner_id", 1), ("channel_id", 1)])
        results, scans = self._dedup_result_collections()
        await results.create_index([("_partition", 1), ("owner_id", 1), ("channel_id", 1), ("scan_id", 1)])
        await scans.create_index([("_partition", 1), ("owner_id", 1), ("updated_at", -1)])

    async def dedup_file_exists(self, file_id, owner_id=None):
        files, _ = self._dedup_collections()
        query = {
            "_id": self._dedup_file_id(file_id, owner_id),
            "_partition": self._dedup_partition(),
        }
        if owner_id is not None:
            query["owner_id"] = int(owner_id)
        return (
            await files.find_one(query, {"_id": 1})
            is not None
        )

    async def dedup_find_by_unique_id(self, file_unique_id, exclude_id, channel_id=None, owner_id=None):
        if not file_unique_id:
            return None
        files, _ = self._dedup_collections()
        query = {
            "_partition": self._dedup_partition(),
            "file_unique_id": file_unique_id,
            "_id": {"$ne": self._dedup_file_id(exclude_id, owner_id)},
        }
        if channel_id is not None:
            query["channel_id"] = int(channel_id)
        if owner_id is not None:
            query["owner_id"] = int(owner_id)
        return await files.find_one(query)

    async def dedup_find_by_size(self, file_size, exclude_id, channel_id=None, owner_id=None):
        files, _ = self._dedup_collections()
        query = {
            "_partition": self._dedup_partition(),
            "file_size": file_size,
            "_id": {"$ne": self._dedup_file_id(exclude_id, owner_id)},
        }
        if channel_id is not None:
            query["channel_id"] = int(channel_id)
        if owner_id is not None:
            query["owner_id"] = int(owner_id)
        cursor = files.find(query).sort("created_at", 1)
        return await cursor.to_list(length=None)

    async def dedup_insert_file(self, record):
        import pymongo

        files, _ = self._dedup_collections()
        document = dict(record)
        document["_partition"] = self._dedup_partition()
        try:
            await files.insert_one(document)
            return True
        except pymongo.errors.DuplicateKeyError:
            return False

    async def dedup_set_md5(self, file_id, md5):
        files, _ = self._dedup_collections()
        await files.update_one(
            {"_id": file_id, "_partition": self._dedup_partition()},
            {"$set": {"md5": md5}},
        )

    async def dedup_delete_file(self, file_id):
        files, _ = self._dedup_collections()
        await files.delete_one({"_id": file_id, "_partition": self._dedup_partition()})

    async def dedup_delete_all_files(self, channel_id=None, owner_id=None):
        files, _ = self._dedup_collections()
        query = {"_partition": self._dedup_partition()}
        if channel_id is not None:
            query["channel_id"] = int(channel_id)
        if owner_id is not None:
            query["owner_id"] = int(owner_id)
        await files.delete_many(query)

    async def dedup_get_progress(self, channel_id=None, owner_id=None):
        _, progress = self._dedup_collections()
        progress_id = (
            self._dedup_channel_progress_id(channel_id, owner_id)
            if channel_id is not None
            else self._dedup_progress_id()
        )
        document = await progress.find_one(
            {
                "_id": progress_id,
                "_partition": self._dedup_partition(),
            }
        )
        return document

    async def dedup_save_progress(self, document, channel_id=None, owner_id=None):
        _, progress = self._dedup_collections()
        data = dict(document)
        data["_id"] = (
            self._dedup_channel_progress_id(channel_id, owner_id)
            if channel_id is not None
            else self._dedup_progress_id()
        )
        data["_partition"] = self._dedup_partition()
        await progress.replace_one({"_id": data["_id"]}, data, upsert=True)

    async def dedup_delete_progress(self, channel_id=None, owner_id=None):
        _, progress = self._dedup_collections()
        await progress.delete_one(
            {
                "_id": (
                    self._dedup_channel_progress_id(channel_id, owner_id)
                    if channel_id is not None
                    else self._dedup_progress_id()
                ),
                "_partition": self._dedup_partition(),
            }
        )

    async def dedup_clear_channel_data(self, channel_id, owner_id):
        results, scans = self._dedup_result_collections()
        scan = await self.dedup_get_scan(channel_id, owner_id)
        progress = await self.dedup_get_progress(channel_id, owner_id)
        scan_id = (scan or {}).get("scan_id") or (progress or {}).get("scan_id")
        query = {
            "_partition": self._dedup_partition(),
            "channel_id": int(channel_id),
        }
        if owner_id is not None:
            query["owner_id"] = int(owner_id)
        elif scan_id:
            query["scan_id"] = scan_id
        if owner_id is not None or scan_id:
            await results.delete_many(query)
        if scan:
            await scans.delete_one({"_id": scan["_id"], "_partition": self._dedup_partition()})
        await self.dedup_delete_progress(channel_id, owner_id)
        files, _ = self._dedup_collections()
        query = {"_partition": self._dedup_partition(), "channel_id": int(channel_id)}
        if owner_id is not None:
            query["owner_id"] = int(owner_id)
        await files.delete_many(query)

    async def dedup_clear_user_data(self, owner_id):
        scans = await self.dedup_list_scans(owner_id)
        _, progress = self._dedup_collections()
        cursors = progress.find({"_partition": self._dedup_partition(), "owner_id": int(owner_id)})
        progress_docs = await cursors.to_list(length=None)
        channel_ids = {int(row["channel_id"]) for row in scans}
        channel_ids.update(int(row["channel_id"]) for row in progress_docs if row.get("channel_id") is not None)
        for channel_id in channel_ids:
            await self.dedup_clear_channel_data(channel_id, owner_id)
        return len(channel_ids)


database = DbManager()
