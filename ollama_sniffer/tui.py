from __future__ import annotations

from pathlib import Path

from aiohttp import web
from rich.syntax import Syntax
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, DataTable, Footer, Header, Input, Static
from textual.widget import Widget

from .export import export_archive
from .model import CaptureStore, Transaction
from .present import DetailSection, detail_sections, transaction_row
from .proxy import create_app
from .reader import ReaderContent
from .value_save import SaveValueScreen


class CapturesChanged(Message):
    pass


class SaveArchiveScreen(ModalScreen[Path | None]):
    """Keep chosen captures available while their save is completed or canceled."""

    CSS = """
    SaveArchiveScreen {
        align: center middle;
    }

    #archive-dialog {
        width: 76;
        max-width: 95%;
        height: auto;
        max-height: 95%;
        border: tall $accent;
        background: $panel;
        padding: 0 2;
    }

    #archive-members {
        height: auto;
        max-height: 3;
        margin: 0 0 1 0;
    }

    #archive-dialog .field-label {
        height: 1;
    }

    #archive-error {
        display: none;
        height: auto;
        color: $error;
    }

    #archive-actions {
        height: 3;
        align-horizontal: right;
    }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, captures: list[Transaction], directory: Path, *, marked: bool) -> None:
        super().__init__()
        self._captures = tuple(captures)
        self.directory = directory
        self.marked = marked

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="archive-dialog"):
            count = len(self._captures)
            selection = f"{count} marked capture{'s' if count != 1 else ''}" if self.marked else "selected capture"
            yield Static(f"Save {selection}", id="archive-selection", markup=False)
            with VerticalScroll(id="archive-members"):
                yield Static(
                    "\n".join(f"{capture.id}  {capture.method} {capture.path_qs}" for capture in self._captures),
                    id="archive-members-text", markup=False,
                )
            yield Static("Label", classes="field-label")
            yield Input(
                placeholder="e.g. qwen in codex",
                id="archive-label",
            )
            yield Static("Directory", classes="field-label")
            yield Input(value=str(self.directory), id="archive-directory")
            yield Static("", id="archive-error", markup=False)
            with Horizontal(id="archive-actions"):
                yield Button("Cancel", id="archive-cancel")
                yield Button("Save", id="archive-save", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#archive-label", Input).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#archive-save")
    def save_archive(self, event: Input.Submitted | Button.Pressed) -> None:
        event.stop()
        label = self.query_one("#archive-label", Input).value.strip()
        if not label:
            self._show_error("Enter a label before saving.")
            self.query_one("#archive-label", Input).focus()
            return
        directory = self.query_one("#archive-directory", Input).value.strip()
        if not directory:
            self._show_error("Choose a directory before saving.")
            self.query_one("#archive-directory", Input).focus()
            return
        if any(capture.completed_at is None for capture in self._captures):
            self._show_error("Wait for all chosen captures to complete, then save again.")
            return
        try:
            output_path = export_archive(label, list(self._captures), Path(directory).expanduser().resolve())
        except (OSError, ValueError, RuntimeError) as exc:
            self._show_error(str(exc))
            return
        self.dismiss(output_path)

    def _show_error(self, message: str) -> None:
        error = self.query_one("#archive-error", Static)
        error.update(message)
        error.display = True
        error.scroll_visible(animate=False)

    @on(Button.Pressed, "#archive-cancel")
    def cancel_save(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_unmount(self) -> None:
        self._captures = ()


class ConfirmQuitScreen(ModalScreen[bool]):
    CSS = """
    ConfirmQuitScreen {
        align: center middle;
    }

    #quit-dialog {
        width: 64;
        max-width: 95%;
        height: auto;
        border: tall $accent;
        background: $panel;
        padding: 1 2;
    }

    #quit-actions {
        height: 3;
        margin-top: 1;
        align-horizontal: right;
    }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, *, proxy_running: bool) -> None:
        super().__init__()
        self.proxy_running = proxy_running

    def compose(self) -> ComposeResult:
        with Vertical(id="quit-dialog"):
            yield Static("Quit Ollama Sniffer?")
            message = "Any captures you haven't saved will be lost."
            if self.proxy_running:
                message += "\nQuitting also stops the proxy."
            yield Static(message, id="quit-warning")
            with Horizontal(id="quit-actions"):
                yield Button("Cancel", id="quit-cancel")
                yield Button("Quit", id="quit-confirm", variant="error")

    def on_mount(self) -> None:
        self.query_one("#quit-cancel", Button).focus()

    @on(Button.Pressed, "#quit-cancel")
    def cancel_quit(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

    @on(Button.Pressed, "#quit-confirm")
    def confirm_quit(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


def _section_widget_id(section_id: str) -> str:
    return f"section-{section_id}"


def _section_id_from_widget(widget: Collapsible) -> str | None:
    if widget.id is None or not widget.id.startswith("section-"):
        return None
    return widget.id.removeprefix("section-")


def _section_content(section: DetailSection) -> Syntax | Text:
    if section.json_content:
        return Syntax(
            section.content,
            "json",
            word_wrap=True,
            background_color="default",
        )
    return Text(section.content)


class DetailRow(Button):
    """One selectable outline entry, with optional captured reader content."""

    BINDINGS = [("enter", "press", "Read")]

    def __init__(self, section: DetailSection, *, classes: str = "", readable: bool = True) -> None:
        self.section = section
        self.readable = readable
        super().__init__(
            Text(section.title),
            id=_section_widget_id(section.id),
            classes=f"detail-row {classes}",
        )

    def render(self) -> Text:
        label, separator, preview = self.section.title.partition(" — ")
        width = max(1, self.content_size.width - 2 * self.styles.line_pad)
        size = Text(self.section.size_label or "", style="dim")
        show_size = bool(size) and width >= size.cell_len + 24
        body_width = width - size.cell_len - 2 if show_size else width
        label_width = max(12, (body_width - 5) // 2)
        text = Text("› " if self.readable else "  ", no_wrap=True, overflow="ellipsis")
        name = Text(label)
        name.truncate(label_width if separator else max(0, body_width - 2), overflow="ellipsis")
        text.append_text(name)
        if separator:
            text.append(" — ", style="dim")
            excerpt = Text(preview, style="dim")
            excerpt.truncate(max(0, body_width - text.cell_len), overflow="ellipsis")
            text.append_text(excerpt)
        if show_size:
            text.append(" " * (width - text.cell_len - size.cell_len))
            text.append_text(size)
        return text

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        return self.readable if action == "press" else True

    def press(self) -> DetailRow:
        if self.readable:
            super().press()
        return self


class CaptureTable(DataTable):
    BINDINGS = [("enter", "select_cursor", "Open")]


class ReaderBody(VerticalScroll):
    BINDINGS = [
        ("s", "app.save_value", "Save value"),
        ("space", "page_down", "Page down"),
        Binding("shift+space", "page_up", "Page up", show=False),
    ]


class SnifferApp(App[None]):
    TITLE = "Ollama Sniffer"
    COLUMNS = (
        ("number", "#"),
        ("status", "State"),
        ("method", "Method"),
        ("path", "Path"),
        ("model", "Model"),
        ("prompt", "Prompt"),
        ("reply", "Reply / call"),
        ("tools", "Tools"),
    )
    CSS = """
    Screen {
        layout: vertical;
    }

    #main {
        height: 1fr;
    }

    #transactions {
        width: 100%;
        height: 1fr;
    }

    #detail-pane {
        display: none;
        width: 100%;
        height: 1fr;
    }

    #capture-empty {
        height: auto;
        padding: 1 2;
        color: $text-muted;
    }

    #capture-filter {
        display: none;
        height: auto;
        padding: 0 1;
        color: $warning;
    }

    #retention-status {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }

    #retention-status.at-capacity {
        color: $warning;
    }

    #details, #reader {
        width: 100%;
        height: 1fr;
        padding: 0 1;
    }

    #reader {
        display: none;
    }

    #reader-toolbar {
        height: 3;
    }

    #reader-heading {
        width: 1fr;
        height: 3;
    }

    #reader-back {
        min-width: 9;
        width: 9;
    }

    #reader-save {
        width: 14;
        min-width: 14;
    }

    #reader-body {
        height: 1fr;
        border: round $accent;
    }

    #empty-details {
        padding: 1 2;
        color: $text-muted;
    }

    Collapsible {
        margin: 0 0 1 0;
    }

    .section-body {
        padding: 0 1 1 2;
    }

    .capture-context {
        margin-bottom: 1;
        text-style: bold;
    }

    .capture-error {
        color: $error;
        margin-bottom: 1;
    }

    .capture-warnings {
        color: $warning;
        margin-bottom: 1;
    }

    .outline-group {
        margin-top: 1;
        text-style: bold underline;
        color: $accent;
    }

    .outline-subheading {
        padding-left: 2;
        text-style: bold;
    }

    .detail-row {
        width: 1fr;
        height: 1;
        min-height: 1;
        margin: 0;
        padding: 0 0 0 2;
        line-pad: 1;
        border: none;
        background: transparent;
        content-align: left top;
        text-align: left;
        text-style: none;
        color: $foreground;
    }

    .detail-row:focus, .detail-row:hover {
        background: $boost;
    }

    .detail-row:focus {
        text-style: $block-cursor-text-style;
        background: $block-cursor-background;
        color: $block-cursor-foreground;
    }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh_captures", "Refresh"),
        ("space", "toggle_selected_capture_mark", "Mark"),
        ("s", "save_captures", "Save"),
        ("h", "hide_selected_path", "Hide path"),
        Binding("H", "show_all_paths", "Show paths", key_display="H"),
        ("escape", "back", "Back"),
        Binding("backspace", "back", "Back", show=False),
        ("v", "toggle_original", "JSON / original"),
    ]

    def __init__(
        self,
        *,
        listen: tuple[str, int],
        upstream: str,
        capture_store: CaptureStore | None = None,
        start_proxy: bool = True,
        export_dir: Path | None = None,
        clamp_developer_role: bool = False,
    ) -> None:
        super().__init__()
        self.listen = listen
        self.upstream = upstream
        self.capture_store = capture_store or CaptureStore()
        self.start_proxy = start_proxy
        self.export_dir = export_dir
        self.clamp_developer_role = clamp_developer_role
        self.bound_port: int | None = None
        self._runner: web.AppRunner | None = None
        self._refresh_pending = False
        self._changed_capture_ids: set[str] = set()
        self._selected_id: str | None = None
        self._row_order: list[str] = []
        self._capture_numbers: dict[str, int] = {}
        self._section_states: dict[tuple[str, str], bool] = {}
        self._rendered_detail_id: str | None = None
        self._marked_capture_ids: set[str] = set()
        self._hidden_paths: set[str] = set()
        self._base_sub_title = ""
        self._view = "captures"
        self._reader_origin: tuple[str, str, float] | None = None
        self._reader_details_dirty = False
        self._reader_section: DetailSection | None = None
        self._reader_caption = ""
        self._reader_original = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="retention-status", markup=False)
        yield Static(
            "Waiting for traffic. Point the harness at the listen address.",
            id="capture-empty",
        )
        yield Static("", id="capture-filter", markup=False)
        with Horizontal(id="main"):
            yield CaptureTable(id="transactions", cursor_type="row", zebra_stripes=True)
            with Vertical(id="detail-pane"):
                with VerticalScroll(id="details"):
                    yield Static(
                        "Waiting for traffic. Point the harness at the listen address.",
                        id="empty-details",
                    )
                with Vertical(id="reader"):
                    with Horizontal(id="reader-toolbar"):
                        yield Static(id="reader-heading", markup=False)
                        back = Button("← Back", id="reader-back")
                        back.can_focus = False
                        yield back
                        save = Button("Save value", id="reader-save")
                        save.can_focus = False
                        yield save
                    with ReaderBody(id="reader-body"):
                        yield ReaderContent(id="reader-content", classes="section-body")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#transactions", DataTable)
        for key, label in self.COLUMNS:
            table.add_column(label, key=key)
        self.capture_store.set_on_change(self._capture_changed)
        await self._refresh_captures(force_details=True)

        host, port = self.listen
        if self.start_proxy:
            proxy_app = create_app(
                self.upstream,
                capture_store=self.capture_store,
                clamp_developer_role=self.clamp_developer_role,
            )
            self._runner = web.AppRunner(proxy_app, shutdown_timeout=1.0)
            await self._runner.setup()
            site = web.TCPSite(self._runner, host, port)
            await site.start()
            assert site._server is not None
            self.bound_port = site._server.sockets[0].getsockname()[1]
            self._base_sub_title = f"{host}:{self.bound_port} -> {self.upstream}"
            if self.clamp_developer_role:
                self._base_sub_title += " | developer → system enabled"
        else:
            capture_count = len(self.capture_store.snapshot())
            noun = "capture" if capture_count == 1 else "captures"
            self._base_sub_title = f"Offline import — {capture_count} {noun}"
        self._update_sub_title()
        table.focus()

    async def stop_proxy(self) -> None:
        self.capture_store.set_on_change(None)
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def on_unmount(self) -> None:
        await self.stop_proxy()

    def _capture_changed(self, transaction: Transaction) -> None:
        self._changed_capture_ids.add(transaction.id)
        self.post_message(CapturesChanged())

    @on(CapturesChanged)
    def queue_capture_refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.call_later(self._refresh_captures)

    async def action_refresh_captures(self) -> None:
        await self._refresh_captures(force_details=True)

    def _update_sub_title(self) -> None:
        parts = [self._base_sub_title]
        if self._view == "outline" and self._selected_id is not None:
            parts.append(f"Capture {self._selected_id[:8]}")
        count = len(self._marked_capture_ids)
        if count:
            noun = "capture" if count == 1 else "captures"
            parts.append(f"Marked: {count} {noun}")
        self.sub_title = " | ".join(parts)

    def _show_view(self, view: str) -> None:
        self._view = view
        table = self.query_one("#transactions", DataTable)
        table.display = view == "captures"
        table.disabled = view == "reader"
        self.query_one("#detail-pane").display = view != "captures"
        self.query_one("#details").display = view == "outline"
        self.query_one("#reader").display = view == "reader"
        self.query_one("#capture-empty").display = view == "captures" and not self._row_order
        self.query_one("#capture-filter").display = view == "captures" and bool(self._hidden_paths)
        self._update_sub_title()
        self.refresh_bindings()

    def action_open_capture(self) -> None:
        if self._view != "captures" or self._selected_id is None:
            return
        self._show_view("outline")
        details = self.query_one("#details", VerticalScroll)
        target = self._first_detail_stop(details)
        target.focus(scroll_visible=False)
        self.call_after_refresh(self._keep_detail_focus_visible, details, target)

    @on(DataTable.RowSelected, "#transactions")
    def open_selected_capture(self) -> None:
        self.action_open_capture()

    async def action_back(self) -> None:
        if self._view == "reader":
            await self.action_return_to_outline()
        elif self._view == "outline":
            self._show_view("captures")
            self.query_one("#transactions", DataTable).focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if isinstance(self.screen, ModalScreen) and action in {
            "back", "refresh_captures", "toggle_original",
            "toggle_selected_capture_mark", "save_captures",
            "hide_selected_path", "show_all_paths",
            "save_value",
        }:
            return False
        if action == "back":
            return self._view != "captures"
        if action == "open_capture":
            return self._view == "captures" and self._selected_id is not None
        if action == "toggle_original":
            return self._reader_section is not None and self._reader_section.original_content is not None
        if action == "save_value":
            return self._view == "reader" and self._reader_section is not None
        if action == "toggle_selected_capture_mark":
            return self._view == "captures"
        if action == "hide_selected_path":
            return self._view == "captures" and self._selected_id is not None
        if action == "show_all_paths":
            return self._view == "captures" and bool(self._hidden_paths)
        if self._reader_origin is not None and action in {
            "toggle_selected_capture_mark",
            "save_captures",
        }:
            return False
        return True

    @on(Button.Pressed)
    async def read_or_return(self, event: Button.Pressed) -> None:
        if isinstance(event.button, DetailRow):
            self._open_reader(event.button)
        elif event.button.id == "reader-back":
            await self.action_back()
        elif event.button.id == "reader-save":
            self.action_save_value()

    def _open_reader(self, row: DetailRow) -> None:
        if self._reader_origin is not None or self._selected_id is None:
            return
        details = self.query_one("#details", VerticalScroll)
        transaction = next(
            (
                capture
                for capture in self.capture_store.snapshot()
                if capture.id == self._selected_id
            ),
            None,
        )
        if transaction is None:
            return
        assert row.id is not None
        self._reader_origin = (transaction.id, row.id, details.scroll_y)
        self._reader_details_dirty = False
        self._reader_section = row.section
        self._reader_original = False
        title = row.section.title.split(" — ", 1)[0].splitlines()[0]
        self._reader_caption = (
            f"{transaction.method} {transaction.path_qs} · {transaction.id[:8]}\n{title}"
        )
        self._update_reader_content()
        self._show_view("reader")
        body = self.query_one("#reader-body", VerticalScroll)
        body.scroll_home(animate=False)
        body.focus()
        self.refresh_bindings()

    def _update_reader_content(self) -> None:
        section = self._reader_section
        assert section is not None
        if self._reader_original:
            kind = "Original text"
            content = Text(section.original_content or "")
        else:
            kind = (
                "Formatted JSON" if section.original_content is not None
                else "Exact JSON" if section.json_content else "Complete text"
            )
            content = _section_content(section)
        hint = "Space / PgDn scroll · Backspace / Esc Back"
        if section.original_content is not None:
            hint += " · v JSON/original"
        self.query_one("#reader-heading", Static).update(
            f"{self._reader_caption} · {kind}\n{hint}"
        )
        self.query_one("#reader-content", Static).update(content)

    def action_toggle_original(self) -> None:
        if self._reader_section is None or self._reader_section.original_content is None:
            return
        self._reader_original = not self._reader_original
        self._update_reader_content()
        self.query_one("#reader-body", VerticalScroll).scroll_home(animate=False)

    async def action_return_to_outline(self) -> None:
        if self._reader_origin is None:
            return
        capture_id, row_id, scroll_y = self._reader_origin
        self._reader_origin = None
        self._reader_section = None
        self._reader_caption = ""
        self._reader_original = False
        self._show_view("outline")
        self.query_one("#reader-content", Static).update(Text(""))
        self.query_one("#reader-heading", Static).update("")
        details = self.query_one("#details", VerticalScroll)
        table = self.query_one("#transactions", DataTable)
        if self._reader_details_dirty:
            transaction = next(
                (
                    capture
                    for capture in self.capture_store.snapshot()
                    if capture.id == self._selected_id
                ),
                None,
            )
            await self._render_details(transaction)
        self._reader_details_dirty = False

        def restore_origin() -> None:
            if self._view != "outline":
                return
            if self._selected_id != capture_id:
                # Retention policy is unchanged; a reader remains an exact snapshot.
                self._show_view("captures")
                table.focus()
                self.notify("The capture you read is no longer retained.", title="Back to captures")
            else:
                details.scroll_to(y=scroll_y, animate=False, immediate=True)
                matches = list(details.query(f"#{row_id}"))
                target = matches[0] if matches else self._first_detail_stop(details)
                target.focus(scroll_visible=False)
                # Reflow can put the origin outside the restored numeric offset.
                self.call_after_refresh(self._keep_detail_focus_visible, details, target)
            self.refresh_bindings()

        self.call_after_refresh(restore_origin)

    def action_toggle_selected_capture_mark(self) -> None:
        if self._selected_id is None:
            self.notify("Select a capture before marking it.", title="Nothing to mark")
            return
        transaction = next(
            (
                capture
                for capture in self.capture_store.snapshot()
                if capture.id == self._selected_id
            ),
            None,
        )
        if transaction is None:
            self.notify("The selected capture is no longer available.", title="Mark failed")
            return
        if transaction.id in self._marked_capture_ids:
            self._marked_capture_ids.remove(transaction.id)
        else:
            self._marked_capture_ids.add(transaction.id)
        table = self.query_one("#transactions", DataTable)
        table.update_cell(
            transaction.id,
            "status",
            self._table_cells(transaction)[1],
            update_width=True,
        )
        self._update_sub_title()

    def action_save_captures(self) -> None:
        chosen_ids = self._marked_capture_ids or {self._selected_id}
        captures = [
            capture
            for capture in self.capture_store.snapshot()
            if capture.id in chosen_ids
        ]
        if not captures:
            self.notify("Select or mark captures before saving.", title="Nothing to save")
            return
        self.push_screen(
            SaveArchiveScreen(captures, self.export_dir or Path.cwd(), marked=bool(self._marked_capture_ids)),
            self._archive_saved,
        )

    def _archive_saved(self, output_path: Path | None) -> None:
        if output_path is None:
            return
        self.export_dir = output_path.parent
        self.notify(f"Wrote {output_path}", title="Archive saved")

    def action_save_value(self) -> None:
        section = self._reader_section
        if section is None or self._reader_origin is None:
            return
        value = section.original_content if self._reader_original else section.content
        assert value is not None
        capture_id = self._reader_origin[0]
        destination = (self.export_dir or Path.cwd()) / f"{capture_id[:8]}-{section.id}.txt"
        kind = "Original text" if self._reader_original else "Displayed JSON" if section.json_content else "Displayed text"
        self.push_screen(SaveValueScreen(f"{self._reader_caption}\n{kind}", value, destination), self._value_saved)

    def _value_saved(self, output_path: Path | None) -> None:
        if output_path is not None:
            self.export_dir = output_path.parent
            self.notify(f"Wrote {output_path}", title="Value saved")

    def action_quit(self) -> None:
        if not isinstance(self.screen, ConfirmQuitScreen):
            self.push_screen(ConfirmQuitScreen(proxy_running=self._runner is not None), self._quit_confirmed)

    def _quit_confirmed(self, confirmed: bool) -> None:
        if confirmed:
            self.exit()

    async def action_hide_selected_path(self) -> None:
        capture = next((item for item in self.capture_store.snapshot() if item.id == self._selected_id), None)
        if capture is not None:
            self._hidden_paths.add(capture.path_qs.partition("?")[0])
            await self._refresh_captures()

    async def action_show_all_paths(self) -> None:
        self._hidden_paths.clear()
        await self._refresh_captures()

    async def _refresh_captures(self, *, force_details: bool = False) -> None:
        self._refresh_pending = False
        changed_capture_ids = self._changed_capture_ids
        self._changed_capture_ids = set()
        retained = list(reversed(self.capture_store.snapshot()))
        # Assign before filtering; evictions advance the oldest session number.
        self._capture_numbers = {
            capture.id: number
            for number, capture in enumerate(
                reversed(retained), start=self.capture_store.evicted_count + 1
            )
        }
        status = self.query_one("#retention-status", Static)
        capacity = self.capture_store.max_captures
        status_text = (
            f"Captures {len(retained)}/{capacity} · Evicted {self.capture_store.evicted_count} · "
            "When full: oldest removed (marked/live too)"
        )
        if str(status.content) != status_text:
            status.update(status_text)
        at_capacity = len(retained) == capacity
        if status.has_class("at-capacity") != at_capacity:
            status.set_class(at_capacity, "at-capacity")
        retained_ids = {capture.id for capture in retained}
        captures = [capture for capture in retained if capture.path_qs.partition("?")[0] not in self._hidden_paths]
        capture_by_id = {capture.id: capture for capture in captures}
        capture_ids = [capture.id for capture in captures]
        self._marked_capture_ids.intersection_update(retained_ids)
        self._update_sub_title()
        self._section_states = {
            key: value
            for key, value in self._section_states.items()
            if key[0] in retained_ids
        }

        if self._selected_id not in capture_by_id:
            if self._view == "outline":
                self._show_view("captures")
                self.query_one("#transactions", DataTable).focus()
                self.notify("The capture is no longer retained.", title="Back to captures")
            self._selected_id = captures[0].id if captures else None

        table = self.query_one("#transactions", DataTable)
        if capture_ids != self._row_order:
            table.clear()
            for capture in captures:
                table.add_row(*self._table_cells(capture), key=capture.id)
            self._row_order = capture_ids
        else:
            for capture in captures:
                if capture.id not in changed_capture_ids:
                    continue
                cells = self._table_cells(capture)
                for (column_key, _label), value in zip(
                    self.COLUMNS, cells, strict=True
                ):
                    table.update_cell(
                        capture.id,
                        column_key,
                        value,
                        update_width=True,
                    )

        empty = self.query_one("#capture-empty", Static)
        empty_message = ("All retained captures are hidden. Press H to show all paths." if retained and not captures
                         else "Waiting for traffic. Point the harness at the listen address.")
        if str(empty.content) != empty_message:
            empty.update(empty_message)
        empty.display = self._view == "captures" and not captures
        hidden = retained_ids - set(capture_ids)
        banner = self.query_one("#capture-filter", Static)
        if self._hidden_paths:
            banner_message = (
                f"Hidden: {len(hidden)} captures on {len(self._hidden_paths)} "
                f"{'path' if len(self._hidden_paths) == 1 else 'paths'} "
                f"({len(hidden & self._marked_capture_ids)} marked). H shows all.\n"
                "Hidden captures still use the capture limit."
            )
            if str(banner.content) != banner_message:
                banner.update(banner_message)
        banner.display = self._view == "captures" and bool(self._hidden_paths)

        if self._selected_id is not None:
            selected_index = next(
                index
                for index, capture in enumerate(captures)
                if capture.id == self._selected_id
            )
            if table.cursor_row != selected_index:
                table.move_cursor(row=selected_index)
            if (
                force_details
                or self._rendered_detail_id != self._selected_id
                or self._selected_id in changed_capture_ids
            ):
                if self._reader_origin is not None:
                    self._reader_details_dirty = True
                else:
                    await self._render_details(capture_by_id[self._selected_id])
        else:
            if force_details or self._rendered_detail_id is not None:
                if self._reader_origin is not None:
                    self._reader_details_dirty = True
                else:
                    await self._render_details(None)

    def _table_cells(self, transaction: Transaction) -> tuple[str, ...]:
        cells = list(transaction_row(transaction).cells())
        if transaction.id in self._marked_capture_ids:
            cells[0] = f"✓ {cells[0]}"
        return (str(self._capture_numbers[transaction.id]), *cells)

    @on(DataTable.RowHighlighted, "#transactions")
    async def select_transaction(self, event: DataTable.RowHighlighted) -> None:
        # Empty-table navigation can highlight the absence of a row.
        if self._view != "captures" or event.row_key is None:
            return
        selected_id = str(event.row_key.value)
        if selected_id not in self._row_order:
            return
        if selected_id == self._selected_id:
            return
        self._selected_id = selected_id
        transaction = next(
            (
                capture
                for capture in self.capture_store.snapshot()
                if capture.id == self._selected_id
            ),
            None,
        )
        await self._render_details(transaction)

    @on(Collapsible.Toggled)
    def remember_section_state(self, event: Collapsible.Toggled) -> None:
        if self._selected_id is None or event.collapsible.id is None:
            return
        section_id = _section_id_from_widget(event.collapsible)
        if section_id is None:
            return
        self._section_states[(self._selected_id, section_id)] = (
            event.collapsible.collapsed
        )

    @on(events.Key)
    def move_detail_selection(self, event: events.Key) -> None:
        """Choose outline values; the primary reader owns its own scrolling."""

        if self._view == "reader":
            return
        details = self.query_one("#details", VerticalScroll)
        table = self.query_one("#transactions", DataTable)
        if self._view == "captures":
            return
        headers = list(details.query("DetailRow, CollapsibleTitle"))
        if not headers:
            return
        outline_header = self._first_detail_stop(details)
        if self.focused is details and event.key in {"up", "down"}:
            target = headers[-1] if event.key == "up" else outline_header
            self._focus_detail_header(details, target)
            event.prevent_default()
            event.stop()
            return
        if self.focused not in headers:
            return
        if event.key == "left":
            self._show_view("captures")
            table.focus()
            event.prevent_default()
            event.stop()
            return
        if event.key in {"pageup", "pagedown"} and isinstance(self.focused, DetailRow):
            index = headers.index(self.focused)
            direction = -1 if event.key == "pageup" else 1
            candidates = headers[:index] if direction < 0 else headers[index + 1:]
            if candidates:
                target_y = self.focused.region.y + direction * details.scrollable_content_region.height
                target = min(candidates, key=lambda row: abs(row.region.y - target_y))
                # Keep the row's screen position until the scroll range clamps.
                details.scroll_relative(
                    y=target.region.y - self.focused.region.y,
                    animate=False,
                    immediate=True,
                )
                target.focus(scroll_visible=False)
                self.call_after_refresh(self._keep_detail_focus_visible, details, target)
            event.prevent_default()
            event.stop()
            return
        if event.key not in {"up", "down"}:
            return
        index = headers.index(self.focused)
        target_index = index - 1 if event.key == "up" else index + 1
        if not 0 <= target_index < len(headers):
            return
        target = headers[target_index]
        self._focus_detail_header(details, target)
        event.prevent_default()
        event.stop()

    @staticmethod
    def _first_detail_stop(details: VerticalScroll) -> Widget:
        summary_rows = list(details.query("DetailRow.summary-item"))
        stops = list(details.query("DetailRow, CollapsibleTitle"))
        return summary_rows[0] if summary_rows else stops[0] if stops else details

    def _keep_detail_focus_visible(self, details: VerticalScroll, header: Widget) -> None:
        if self.focused is header and self._view == "outline":
            self._focus_detail_header(details, header)

    @staticmethod
    def _focus_detail_header(details: VerticalScroll, header: Widget) -> None:
        """Move focus without Textual's eager recentering of a visible row."""

        header.focus(scroll_visible=False)
        viewport = details.scrollable_content_region
        region = header.region
        if region.y < viewport.y:
            details.scroll_relative(
                y=region.y - viewport.y,
                animate=False,
                immediate=True,
            )
        elif region.bottom > viewport.bottom:
            details.scroll_relative(
                y=region.bottom - viewport.bottom,
                animate=False,
                immediate=True,
            )

    def _summary_widgets(
        self,
        section: DetailSection,
    ) -> list[Static | DetailRow]:
        """Render outline rows as direct children of the main detail scroller."""

        items: list[Static | DetailRow] = []
        for item in section.summary_items:
            if item.heading:
                items.append(Static(Text(item.content), classes="outline-subheading"))
                continue
            assert item.id is not None
            readable = item.detail_content is not None
            items.append(
                DetailRow(
                    DetailSection(
                        id=item.id,
                        title=item.content,
                        content=item.detail_content or "",
                        collapsed=True,
                        json_content=item.json_content,
                        original_content=item.original_content,
                        size_label=item.size_label,
                    ),
                    classes="summary-item" if readable else "summary-item outline-empty",
                    readable=readable,
                )
            )
        return items

    def _section_body(self, section: DetailSection) -> Static:
        return Static(_section_content(section), classes="section-body")

    async def _render_details(self, transaction: Transaction | None) -> None:
        details = self.query_one("#details", VerticalScroll)
        if transaction is None:
            self._rendered_detail_id = None
            await details.remove_children()
            await details.mount(
                Static(
                    "Waiting for traffic. Point the harness at the listen address.",
                    id="empty-details",
                )
            )
            return

        sections = detail_sections(transaction)
        if self._rendered_detail_id == transaction.id and self._update_details(
            details,
            transaction,
            sections,
        ):
            return

        focused = self.focused
        focused_id = None
        if focused is not None and details in focused.ancestors:
            if isinstance(focused, DetailRow):
                focused_id = focused.id
            elif isinstance(focused.parent, Collapsible):
                focused_id = focused.parent.id
                if transaction.completed_at is not None:
                    destination = {
                        "section-overview": "section-message-1",
                        "section-messages": "section-message-1",
                        "section-tools": "section-tool-1",
                        "section-response": "section-response-full",
                        "section-thinking": "section-thinking-full",
                        "section-tool-calls": "section-tool-call-1",
                    }
                    focused_id = destination.get(focused_id, focused_id)
        await details.remove_children()
        widgets: list[Widget] = []
        live_group = None
        for section in sections:
            if section.summary_items:
                widgets.extend(
                    [
                        Static(
                            Text(section.title),
                            classes="outline-group",
                            id=_section_widget_id(section.id),
                        ),
                        *self._summary_widgets(section),
                    ]
                )
                continue
            if transaction.completed_at is not None:
                classes = "capture-context" if section.id == "context" else section.id
                widgets.append(Static(
                    Text(section.content), classes=classes, id=_section_widget_id(section.id),
                ))
                continue
            if section.id in {"messages", "tools"}:
                group = "Input"
            elif section.id in {"response", "thinking", "tool-calls"}:
                group = "Output"
            elif section.id in {"diagnostics", "raw-events", "raw-request", "forwarded-request", "raw-response"}:
                group = "Evidence"
            else:
                group = None
            if group is not None and group != live_group:
                widgets.append(Static(group, classes="outline-group", id=f"live-{group.lower()}"))
                live_group = group
            widgets.append(
                Collapsible(
                    self._section_body(section),
                    title=section.title,
                    collapsed=self._section_states.get(
                        (transaction.id, section.id),
                        section.collapsed,
                    ),
                    id=_section_widget_id(section.id),
                )
            )
        await details.mount(*widgets)
        self._rendered_detail_id = transaction.id

        def restore_updated_row() -> None:
            if (
                focused_id is None or self._view != "outline"
                or self._rendered_detail_id != transaction.id or self.focused is not details
            ):
                return
            matches = list(details.query(f"#{focused_id}"))
            target = matches[0] if matches else self._first_detail_stop(details)
            if isinstance(target, Collapsible):
                target = target.query_one("CollapsibleTitle")
            self._focus_detail_header(details, target)

        if focused_id is not None:
            self.call_after_refresh(restore_updated_row)

    def _update_details(
        self,
        details: VerticalScroll,
        transaction: Transaction,
        sections: list[DetailSection],
    ) -> bool:
        if any(section.summary_items for section in sections):
            return False
        existing = [
            child for child in details.children if isinstance(child, Collapsible)
        ]
        expected_ids = [_section_widget_id(section.id) for section in sections]
        if [widget.id for widget in existing] != expected_ids:
            return False

        for widget, section in zip(existing, sections, strict=True):
            widget.title = section.title
            state_key = (transaction.id, section.id)
            widget.collapsed = self._section_states.get(state_key, section.collapsed)
            widget.query_one(".section-body", Static).update(_section_content(section))
        return True
