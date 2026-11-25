"""Uitvoeren van systeemcommando's. Alles draait als root; er wordt geen sudo gebruikt."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandError(RuntimeError):
    pass


class CommandRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        input_text: str | None = None,
        check: bool = False,
    ) -> CommandResult:
        raise NotImplementedError


class LocalRunner(CommandRunner):
    """Voert echte commando's uit als de huidige (root-)gebruiker."""

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    def run(
        self,
        argv: Sequence[str],
        *,
        input_text: str | None = None,
        check: bool = False,
    ) -> CommandResult:
        cmd = [str(a) for a in argv]
        try:
            proc = subprocess.run(
                cmd,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            result = CommandResult(cmd, returncode=127, stderr=str(exc))
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(cmd, returncode=124, stderr=f"timeout: {exc}")
        else:
            result = CommandResult(cmd, proc.returncode, proc.stdout, proc.stderr)
        if check and not result.ok:
            raise CommandError(
                f"{' '.join(cmd)} faalde ({result.returncode}): {result.stderr.strip()}"
            )
        return result


class FakeRunner(CommandRunner):
    """Test-double: registreert aanroepen en geeft vooraf ingestelde antwoorden."""

    def __init__(self, responses: dict[str, CommandResult] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[list[str], str | None]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        input_text: str | None = None,
        check: bool = False,
    ) -> CommandResult:
        cmd = [str(a) for a in argv]
        self.calls.append((cmd, input_text))
        result = self.responses.get(" ".join(cmd))
        if result is None:
            result = self.responses.get(cmd[0])
        if result is None:
            result = CommandResult(cmd)
        if check and not result.ok:
            raise CommandError(f"{' '.join(cmd)} faalde in FakeRunner")
        return result
