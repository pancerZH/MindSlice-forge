"""Run one non-interactive agent session.

A harness is a command line. The prompt is written to a file and the command only carries a
one-line message pointing at it, so prompt length and quoting never matter and any agent with
file tools can be used.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from . import ForgeError

MESSAGE = "Read the file {prompt_file} and carry out the task it describes, completely and exactly."

# Placeholders: {message} (the line above), {prompt_file}, {workdir}.
PRESETS = {
    "agy": ["agy", "--print={message}", "--dangerously-skip-permissions", "--disable-slash-commands",
            "--output-format", "stream-json"],
    "claude": ["claude", "-p", "{message}", "--permission-mode", "bypassPermissions",
               "--output-format", "stream-json", "--verbose"],
}

BACKOFF = (30, 120, 300)  # seconds to wait before each retry of a failed session


def template(preset: str, custom: str | None, model: str | None, dirs: list[Path]) -> list[str]:
    """The command template: a custom one as given, or a preset with the model and readable directories."""
    if custom:
        return shlex.split(custom)
    cmd = list(PRESETS[preset])
    if model:
        cmd += ["--model", model]
    for d in dirs:
        cmd += ["--add-dir", str(d)]
    return cmd


def command(template: list[str], prompt_file: Path, workdir: Path) -> list[str]:
    values = {"{message}": MESSAGE.format(prompt_file=prompt_file), "{prompt_file}": str(prompt_file),
              "{workdir}": str(workdir)}
    cmd = []
    for part in template:
        for placeholder, value in values.items():
            part = part.replace(placeholder, value)
        cmd.append(part)
    return cmd


def run(cmd: list[str], cwd: Path, log: Path, timeout: int) -> bool:
    """Run the session, appending its output to ``log``. True when it exited with status 0 in time."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as fh:
        fh.write(f"$ {shlex.join(cmd)}\n".encode())
        fh.flush()
        try:
            done = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            fh.write(f"\n[forge] timed out after {timeout}s\n".encode())
            return False
        except FileNotFoundError:
            raise ForgeError(f"harness command not found: {cmd[0]}") from None
        fh.write(f"\n[forge] exit status {done.returncode}\n".encode())
        return done.returncode == 0
