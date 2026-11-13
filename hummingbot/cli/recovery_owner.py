"""Verify physical ownership on its loop while bulk recovery uses a DB worker."""
import asyncio
import concurrent.futures
import threading


class RecoveryOwner:
    def __init__(self, owner):
        self.owner_uuid, self.generation = owner.owner_uuid, owner.generation
        self._owner, self._loop = owner, asyncio.get_running_loop()
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    def check_owner(self):
        if self._cancelled.is_set():
            raise asyncio.CancelledError()
        result = concurrent.futures.Future()

        def check():
            if self._cancelled.is_set():
                result.set_exception(asyncio.CancelledError())
                return
            try:
                value = self._owner.check_owner()
            except BaseException as error:
                result.set_exception(error)
            else:
                result.set_result(value)

        self._loop.call_soon_threadsafe(check)
        value = result.result(timeout=5)
        if self._cancelled.is_set():
            raise asyncio.CancelledError()
        return value
