"""Run one non-interactive agent session.

A harness is a command line, given by the caller: the forge knows no agent product, only what
it needs from a session (``CONTRACT``). The prompt is written to a file and the command only
carries a one-line message pointing at it, so prompt length and quoting never matter and any
agent with file tools can be used.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from . import ForgeError

MESSAGE = "Read the file {prompt_file} and carry out the task it describes, completely and exactly."

# Shown by ``mindslice-forge --help``: an agent that composes the command reads it there.
CONTRACT = """\
the harness command:
  --harness-cmd is the command line that runs one agent session of the harness you choose, with
  its model and options. The forge runs it once per step, from the forge directory, with no
  terminal attached, after filling in these placeholders (do not quote them):
    {message}      one line telling the agent to read the prompt file and do what it says
    {prompt_file}  the path of the prompt file
    {workdir}      the forge directory
  The command must:
    - contain {message} or {prompt_file}, so that the agent receives its task;
    - name files and directories by absolute paths, as it does not run from your directory;
    - end when the task is done, with exit status 0 on success;
    - never wait for an answer: no questions, and no permission prompts for reading and writing
      files or running shell commands;
    - let the agent write in the workspace and the forge directory and read the sources and
      every --dict (--dry-run lists these directories);
    - give the agent's shell a PATH with the `mindslice` command.
  Before the first step with a new command, one short session checks all of this.
"""

BACKOFF = (30, 120, 300)  # seconds to wait before each retry of a failed session


def template(command_line: str) -> list[str]:
    """The ``--harness-cmd`` command line as a list of arguments, placeholders still in place."""
    try:
        cmd = shlex.split(command_line)
    except ValueError as error:
        raise ForgeError(f"--harness-cmd cannot be read as a command line: {error}") from None
    if not any("{message}" in part or "{prompt_file}" in part for part in cmd[1:]):
        raise ForgeError("--harness-cmd must be a command with {message} or {prompt_file} among its arguments; "
                         "without one the agent never receives its task. See `mindslice-forge --help`.")
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
