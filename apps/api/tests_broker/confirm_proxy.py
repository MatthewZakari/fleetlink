"""Test-only TCP proxy that forwards AMQP publish but withholds its confirmation."""

import select
import socket
import threading


class ConfirmProxy:
    def __init__(self, host: str, port: int) -> None:
        self._target = (host, port)
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self._listener.settimeout(5)
        self.port: int = self._listener.getsockname()[1]
        self.published = threading.Event()
        self._stop = threading.Event()
        self._errors: list[str] = []
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            with (
                self._listener.accept()[0] as client,
                socket.create_connection(self._target, timeout=3) as broker,
            ):
                client.settimeout(2)
                broker.settimeout(2)
                buffer = b""
                header_seen = False
                while not self._stop.is_set():
                    ready, _, _ = select.select([client, broker], [], [], 0.05)
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        if source is client:
                            buffer += data
                            if not header_seen and len(buffer) >= 8:
                                assert buffer[:4] == b"AMQP"
                                buffer = buffer[8:]
                                header_seen = True
                            while header_seen and len(buffer) >= 7:
                                size = int.from_bytes(buffer[3:7], "big")
                                if len(buffer) < size + 8:
                                    break
                                # AMQP method frame: Basic.Publish, class 60 / method 40.
                                if buffer[0] == 1 and buffer[7:11] == b"\x00\x3c\x00\x28":
                                    self.published.set()
                                buffer = buffer[size + 8 :]
                            broker.sendall(data)
                        elif not self.published.is_set():
                            client.sendall(data)
        except (OSError, AssertionError) as error:
            if not self._stop.is_set():
                self._errors.append(type(error).__name__)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=6)
        self._listener.close()
        assert not self._thread.is_alive(), "AMQP proxy failed to stop"
        assert not self._errors, f"AMQP proxy failed: {self._errors}"
