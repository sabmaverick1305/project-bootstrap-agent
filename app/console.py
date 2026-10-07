"""User interaction. The workflow talks to a Console, so tests can script the answers."""

from typing import Protocol


class Console(Protocol):
    def show(self, text: str) -> None: ...

    def choose(self, prompt: str, options: list[str]) -> str:
        """Return one of `options`. The last option must be the safe exit (cancel/reject)."""
        ...

    def ask(self, prompt: str) -> str:
        """Return free text, or "" if the user gave none."""
        ...


class TerminalConsole:
    def show(self, text: str) -> None:
        print(text, flush=True)

    def choose(self, prompt: str, options: list[str]) -> str:
        labels = "/".join(options)
        while True:
            try:
                answer = input(f"\n{prompt} [{labels}]: ").strip().lower()
            except EOFError:
                print()
                return options[-1]  # no input available: take the safe exit
            matches = [option for option in options if answer and option.startswith(answer)]
            if len(matches) == 1:
                return matches[0]
            print(f"Please answer one of: {labels}")

    def ask(self, prompt: str) -> str:
        try:
            return input(f"\n{prompt} ").strip()
        except EOFError:
            print()
            return ""
