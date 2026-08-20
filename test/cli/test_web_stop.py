"""``web stop`` must kill the whole uvicorn --reload process group (L9).

The services are launched with ``start_new_session=True``, so signalling only
the recorded parent pid used to leave the reloader's worker alive holding the
port. ``_stop`` now signals the process group and escalates to SIGKILL.
"""

from __future__ import annotations

import subprocess
import sys
import time

from quantforge.cli.commands.web_cmd import _is_running, _stop

# The "reloader" parent spawns a "worker" grandchild in the same new session,
# exactly like uvicorn --reload does; both must die when _stop signals the
# process group.
_PARENT_TEMPLATE = (
    "import subprocess, sys, time\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "open({child_pid_file!r}, 'w').write(str(p.pid))\n"
    "time.sleep(120)\n"
)


def test_stop_kills_whole_process_group(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    parent = subprocess.Popen(
        [sys.executable, "-c", _PARENT_TEMPLATE.format(child_pid_file=str(child_pid_file))],
        start_new_session=True,
    )
    try:
        for _ in range(50):  # wait for the grandchild to spawn + write its pid
            if child_pid_file.exists():
                break
            time.sleep(0.05)
        child_pid = int(child_pid_file.read_text().strip())

        pid_file = tmp_path / "backend.pid"
        pid_file.write_text(str(parent.pid))

        assert _stop(pid_file) is True
        assert not pid_file.exists()

        parent.wait(timeout=10)  # reap the group leader to clear its zombie
        assert not _is_running(parent.pid)
        # The worker is reparented to init after its parent dies; give the
        # reaper a moment before asserting it is gone too.
        for _ in range(50):
            if not _is_running(child_pid):
                break
            time.sleep(0.05)
        assert not _is_running(child_pid)
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)


def test_stop_returns_false_and_cleans_stale_pid_file(tmp_path):
    pid_file = tmp_path / "backend.pid"
    pid_file.write_text("999999999")  # almost certainly not alive
    assert _stop(pid_file) is False
    assert not pid_file.exists()
