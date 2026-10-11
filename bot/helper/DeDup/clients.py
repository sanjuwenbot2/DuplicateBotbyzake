import asyncio

from pyrogram.errors import FloodWait, MessageNotModified, RPCError

from ... import LOGGER
from ...core.config_manager import Config

_NOT_SET = object()
NOT_MODIFIED = object()


class BotPool:
    def __init__(self, clients):
        if not clients:
            raise RuntimeError("Bot pool requires at least one client")
        self.clients = clients
        self.master = self.clients[0]
        self._cooldown = {}
        self._busy = set()
        self._event = asyncio.Event()

    def _now(self):
        return asyncio.get_running_loop().time()

    def cooldown_remaining(self, client):
        until = self._cooldown.get(client)
        if until is None:
            return 0.0
        remaining = until - self._now()
        return remaining if remaining > 0 else 0.0

    def is_available(self, client):
        return client not in self._busy and self.cooldown_remaining(client) <= 0

    def available(self):
        return [client for client in self.clients if self.is_available(client)]

    def busy_count(self):
        return len(self._busy)

    def cooling_bots(self):
        result = []
        for client in self.clients:
            remaining = self.cooldown_remaining(client)
            if remaining > 0:
                result.append((client, remaining))
        return result

    def _wake(self):
        self._event.set()

    def _reset_event(self):
        if self._event.is_set():
            self._event = asyncio.Event()
        return self._event

    def _next_wakeup(self):
        remaining = [
            self.cooldown_remaining(client)
            for client in self.clients
            if client not in self._busy and self.cooldown_remaining(client) > 0
        ]
        if not remaining:
            return 1.0
        return max(0.25, min(remaining))

    async def acquire(self, exclude=None):
        exclude = exclude or ()
        while True:
            for client in self.clients:
                if client in exclude:
                    continue
                if self.is_available(client):
                    self._busy.add(client)
                    return client
            if all(client in exclude for client in self.clients):
                exclude = ()
                continue
            event = self._reset_event()
            try:
                await asyncio.wait_for(event.wait(), self._next_wakeup())
            except asyncio.TimeoutError:
                pass

    def release(self, client):
        self._busy.discard(client)
        self._wake()

    def cool(self, client, seconds):
        seconds = max(0.0, float(seconds or 0))
        self._cooldown[client] = self._now() + seconds * Config.FLOOD_WAIT_MULTIPLIER
        LOGGER.warning(
            "Bot %s on cooldown for %.1fs",
            client.name,
            seconds * Config.FLOOD_WAIT_MULTIPLIER,
        )
        self._wake()

    async def call(self, func, retries=None, timeout=None, exclude=None):
        retries = Config.MAX_RETRIES if retries is None else retries
        attempts = 0
        last_error = None
        while True:
            attempts += 1
            client = await self.acquire(exclude)
            try:
                awaitable = func(client)
                if timeout is not None:
                    return await asyncio.wait_for(awaitable, timeout)
                return await awaitable
            except FloodWait as error:
                self.cool(client, getattr(error, "value", 0))
                last_error = error
            except MessageNotModified:
                return NOT_MODIFIED
            except asyncio.TimeoutError as error:
                self._cooldown[client] = self._now() + Config.TIMEOUT_COOLDOWN
                self._wake()
                LOGGER.warning("Bot %s timed out on a call", client.name)
                last_error = error
            except RPCError as error:
                last_error = error
                LOGGER.warning("Bot %s call failed: %s", client.name, error)
                if error.__class__.__name__ == "FileReferenceExpired":
                    raise
            finally:
                self.release(client)
            if attempts >= retries:
                raise last_error

    async def start_all(self):
        await asyncio.gather(*(client.start() for client in self.clients))
        LOGGER.info("Started %d bot client(s)", len(self.clients))

    async def stop_all(self):
        await asyncio.gather(
            *(client.stop() for client in self.clients), return_exceptions=True
        )
        LOGGER.info("Stopped %d bot client(s)", len(self.clients))
