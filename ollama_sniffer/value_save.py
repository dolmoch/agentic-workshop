from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from .export import save_value_text


class SaveValueScreen(ModalScreen[Path | None]):
    """Save an explicit snapshot of the displayed reader representation."""

    CSS = """
    SaveValueScreen {
        align: center middle;
    }

    #value-save-dialog {
        width: 76;
        max-width: 95%;
        height: auto;
        max-height: 95%;
        border: tall $accent;
        background: $panel;
        padding: 1 2;
    }

    #value-save-error {
        display: none;
        height: auto;
        color: $error;
    }

    #value-save-actions {
        height: 3;
        align-horizontal: right;
    }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, caption: str, value: str, output_path: Path) -> None:
        super().__init__()
        self.caption = caption
        self.value_text = value
        self.output_path = output_path

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="value-save-dialog"):
            yield Static("Save displayed value as UTF-8 text")
            yield Static(self.caption, markup=False)
            yield Static("File path")
            yield Input(value=str(self.output_path), id="value-save-path")
            yield Static("", id="value-save-error", markup=False)
            with Horizontal(id="value-save-actions"):
                yield Button("Cancel", id="value-save-cancel")
                yield Button("Save value", id="value-save-confirm", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#value-save-path", Input).focus()

    @on(Input.Submitted, "#value-save-path")
    @on(Button.Pressed, "#value-save-confirm")
    def save(self, event: Input.Submitted | Button.Pressed) -> None:
        event.stop()
        value = self.query_one("#value-save-path", Input).value.strip()
        try:
            if not value:
                raise ValueError("Choose a file path before saving.")
            output_path = save_value_text(self.value_text, Path(value).expanduser().resolve())
        except (OSError, ValueError, RuntimeError) as exc:
            error = self.query_one("#value-save-error", Static)
            error.update(str(exc))
            error.display = True
            error.scroll_visible(animate=False)
            return
        self.dismiss(output_path)

    @on(Button.Pressed, "#value-save-cancel")
    def cancel(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_unmount(self) -> None:
        self.value_text = ""
