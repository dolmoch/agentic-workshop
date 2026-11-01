"""Full-value reading without rendering the entire document off screen."""

from bisect import bisect_right
from collections import OrderedDict

from rich.cells import cell_len
from rich.segment import Segment
from rich.syntax import Syntax
from rich.text import Text
from textual.geometry import Size
from textual.strip import Strip
from textual.widgets import Static


class ReaderContent(Static):
    """Measure plain lines once per width; highlight only visible source lines."""

    def __init__(self, **kwargs) -> None:
        super().__init__(markup=False, **kwargs)
        self._lines = [""]
        self._layout_width = 0
        self._row_starts = [0, 1]
        self._line_cache: OrderedDict[int, list[Text]] = OrderedDict()

    def update(self, content: Syntax | Text | str = "", *, layout: bool = True) -> None:
        value = content.code if isinstance(content, Syntax) else content.plain if isinstance(content, Text) else content
        tab_size = content.tab_size if isinstance(content, Syntax) else 8
        self._lines = value.split("\n")
        for index, line in enumerate(self._lines):
            if "\t" in line:
                expanded = Text(line)
                expanded.expand_tabs(tab_size)
                self._lines[index] = expanded.plain
        self._layout_width = 0
        self._row_starts = [0, 1]
        self._line_cache.clear()
        super().update(content, layout=layout)
        self.clear_cached_dimensions()

    def _layout(self, width: int) -> None:
        if width == self._layout_width:
            return
        self._layout_width = width
        self._line_cache.clear()
        starts = [0]
        for line in self._lines:
            count = 1 if cell_len(line) <= width else len(
                Text(line).wrap(self.app.console, width, overflow="fold")
            )
            starts.append(starts[-1] + count)
        self._row_starts = starts

    def get_content_width(self, container: Size, viewport: Size) -> int:
        return container.width

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        if width <= 0:
            return 0
        self._layout(width)
        return self._row_starts[-1]

    def render_line(self, y: int) -> Strip:
        width = self.content_size.width
        base = self.visual_style.rich_style
        if width <= 0:
            return Strip.blank(0, base)
        self._layout(width)
        if y < 0 or y >= self._row_starts[-1]:
            return Strip.blank(width, base)
        index = bisect_right(self._row_starts, y) - 1
        wrapped = self._line_cache.get(index)
        if wrapped is None:
            line = self._lines[index]
            # Lexers may append a newline; it is not part of this source line.
            text = self.content.highlight(line)[:len(line)] if isinstance(self.content, Syntax) else Text(line)
            wrapped = list(text.wrap(self.app.console, width, overflow="fold", no_wrap=False))
            self._line_cache[index] = wrapped
            if len(self._line_cache) > 128:
                self._line_cache.popitem(last=False)
        else:
            self._line_cache.move_to_end(index)
        fragment = wrapped[y - self._row_starts[index]]
        segments = Segment.apply_style(fragment.render(self.app.console, end=""), base)
        return Strip(segments).adjust_cell_length(width, base)
