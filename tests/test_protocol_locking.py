"""
Tests for serial port serialisation in ScannerProtocol.

LogPanel polls GLG from a background thread while the virtual keypad and the
upload/download dialogs still issue commands from other threads. A scanner
command is write-then-read-until-CR on a shared port, so overlapping
transactions would let one caller consume another's response. These tests pin
the locking that prevents it.
"""
import threading
import time

from app.serial.protocol import ScannerProtocol


class _FakeSerial:
    """Serial stand-in that records overlapping transactions.

    Each command writes, then reads back its own echoed response. If two
    threads ever interleave between the write and the read, ``max_concurrent``
    rises above 1 and the response a caller sees is not its own.
    """

    def __init__(self):
        self._pending = b""
        self._active = 0
        self.max_concurrent = 0
        self._guard = threading.Lock()

    def reset_input_buffer(self):
        with self._guard:
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        self._pending = b""

    def write(self, data: bytes):
        cmd = data.decode().strip()
        # Give the scheduler a chance to interleave another thread here.
        time.sleep(0.001)
        self._pending = f"{cmd},OK\r".encode()

    @property
    def in_waiting(self):
        return len(self._pending)

    def read(self, n):
        chunk, self._pending = self._pending[:n], self._pending[n:]
        if not self._pending:
            with self._guard:
                self._active -= 1
        return chunk


def test_concurrent_commands_do_not_interleave():
    fake = _FakeSerial()
    proto = ScannerProtocol(fake)
    errors: list = []
    mismatches: list = []

    def worker(name: str):
        try:
            for _ in range(20):
                # The response echoes the command, so a caller receiving
                # someone else's payload is directly detectable.
                if proto.send_command(name) != "OK":
                    mismatches.append(name)
        except Exception as exc:      # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"CMD{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors
    assert not mismatches
    assert fake.max_concurrent == 1


def test_wait_until_idle_returns_true_when_free():
    proto = ScannerProtocol(_FakeSerial())
    assert proto.wait_until_idle(timeout=1.0) is True


def test_wait_until_idle_blocks_until_transaction_completes():
    fake = _FakeSerial()
    proto = ScannerProtocol(fake)
    released = threading.Event()

    def slow_command():
        with proto._io_lock:
            time.sleep(0.3)
            released.set()

    t = threading.Thread(target=slow_command)
    t.start()
    time.sleep(0.05)                      # let the thread take the lock

    started = time.monotonic()
    assert proto.wait_until_idle(timeout=5.0) is True
    waited = time.monotonic() - started

    t.join()
    assert released.is_set()
    # It must have actually waited for the in-flight transaction, not returned
    # immediately — this is what keeps close_port() off a live handle.
    assert waited > 0.1


def test_wait_until_idle_times_out_when_port_stays_busy():
    proto = ScannerProtocol(_FakeSerial())
    holding = threading.Event()
    finish = threading.Event()

    def hog():
        with proto._io_lock:
            holding.set()
            finish.wait(timeout=5)

    t = threading.Thread(target=hog)
    t.start()
    assert holding.wait(timeout=2)

    assert proto.wait_until_idle(timeout=0.2) is False

    finish.set()
    t.join()


def test_reentrant_lock_allows_nested_commands():
    """A high-level helper may call another without deadlocking."""
    proto = ScannerProtocol(_FakeSerial())
    with proto._io_lock:
        assert proto.send_command("MDL") == "OK"
