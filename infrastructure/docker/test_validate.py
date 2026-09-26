"""Exercise validation with a controlling terminal and real GNU timeout, no Docker."""

import errno
import os
import pty
import select
import signal
import tempfile
import time
import unittest
from pathlib import Path

VALIDATOR = Path(__file__).with_name("validate.sh").resolve()
FAKE_DOCKER = r"""#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$CALL_LOG"
case " $* " in
    *' exec -T '*)
        # Model Compose's stdin attachment, even though the probe needs no input.
        # With terminal stdin, timeout's background process group gets SIGTTIN.
        read -r unused || true
        case " $* " in
            *" exec -T ${FAIL_SERVICE:-none} "*) exit 42 ;;
        esac
        ;;
esac
"""


class ValidationLifecycleTest(unittest.TestCase):
    def run_validation(self, fail_service="none"):
        with tempfile.TemporaryDirectory() as directory:
            docker = Path(directory) / "docker"
            docker.write_text(FAKE_DOCKER)
            docker.chmod(0o755)
            log = Path(directory) / "calls"
            pid, terminal = pty.fork()
            if pid == 0:
                os.chdir(directory)  # Validate root resolution outside the repository.
                os.environ.update(
                    PATH=directory + os.pathsep + os.environ["PATH"],
                    CALL_LOG=str(log),
                    FAIL_SERVICE=fail_service,
                )
                os.execlp("bash", "bash", str(VALIDATOR), "check", "--cleanup")
            output = bytearray()
            status = None
            deadline = time.monotonic() + 5
            try:
                while time.monotonic() < deadline:
                    if select.select([terminal], [], [], 0.05)[0]:
                        try:
                            output.extend(os.read(terminal, 65536))
                        except OSError as error:
                            if error.errno != errno.EIO:
                                raise
                    waited, status = os.waitpid(pid, os.WNOHANG)
                    if waited:
                        break
                else:
                    # Also kill timeout's separate process groups in this PTY session.
                    for entry in Path("/proc").iterdir():
                        if entry.name.isdigit():
                            try:
                                if os.getsid(int(entry.name)) == pid:
                                    os.kill(int(entry.name), signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    os.waitpid(pid, 0)
                    self.fail("Validation hung with terminal stdin: " + output.decode())
            finally:
                os.close(terminal)
            return os.waitstatus_to_exitcode(status), log.read_text(), output.decode()

    def test_probes_finish_with_terminal_stdin(self):
        code, calls, output = self.run_validation()
        self.assertEqual(code, 0, output)
        self.assertEqual(calls.count(" exec -T "), 5)
        self.assertIn("Infrastructure validation passed.", output)
        self.assertIn(" down --timeout 20", calls)
        self.assertNotIn("--volumes", calls)

    def test_probe_failures_propagate_and_cleanup_preserves_volumes(self):
        for service in ("postgres", "redis", "rabbitmq"):
            with self.subTest(service=service):
                code, calls, output = self.run_validation(service)
                self.assertEqual(code, 42, output)
                self.assertNotIn("Infrastructure validation passed.", output)
                self.assertIn(" ps --all", calls)
                self.assertIn(" down --timeout 20", calls)
                self.assertNotIn("--volumes", calls)


if __name__ == "__main__":
    unittest.main()
