"""Writing a :class:`~treehawk.htmlreport.models.Page` out as one HTML file.

The file stands alone: the stylesheet, the script and every chart are inside
it, so it opens offline, mails as an attachment and prints as it looks. Charts
are SVG drawn here, in Python - the script only adds the hover readout and
table sorting, and the page reads the same without it.

Every piece of text from a log passes through :func:`_text` on its way into the
markup; nothing else escapes, so nothing is escaped twice.
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable, Iterable, Sequence
from importlib import resources

from treehawk import __version__
from treehawk.htmlreport.constants import HTML
from treehawk.htmlreport.models import (
    Cell,
    Chart,
    Column,
    Fact,
    Figure,
    Line,
    Notice,
    Page,
    Process,
    Ranking,
    Section,
    Tracks,
)
from treehawk.ui.theme import DISCOVERY_ORDER

WIDTH = 1000
"""The SVG's own width; it is stretched to the column, never read in pixels."""

HEIGHT = 100

MONO = ' class="mono"'

CRISP = 'vector-effect="non-scaling-stroke"'
"""Strokes keep their width however far the SVG is stretched."""

LOGO = (
    '<svg viewBox="0 0 96 96" aria-hidden="true"><rect width="96" height="96" rx="18" fill="var(--brand)"/>'
    '<path d="M43 28 31 42M53 28l12 14" stroke="#fff" stroke-width="5"/>'
    '<path d="m67 55-4 11" stroke="#fff" stroke-width="4" stroke-dasharray="6 4"/>'
    '<circle cx="48" cy="22" r="9" fill="#fff"/><circle cx="26" cy="48" r="9" fill="#fcf3d1"/>'
    '<circle cx="70" cy="48" r="9" fill="#fcf3d1"/><circle cx="60" cy="74" r="8" fill="#c9f0e9"/>'
    '<circle cx="60" cy="74" r="15" fill="none" stroke="#c9f0e9" stroke-width="3"/></svg>'
)


def render(page: Page) -> str:
    """The whole page, as the text of an HTML file."""
    parts = [
        _masthead(page),
        _figures(page.figures),
        _notices(page.notices),
        *(_section(section) for section in page.sections),
        f"<footer>{_text(page.footer)}</footer>",
    ]
    body = "\n".join(part for part in parts if part)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="generator" content="treehawk {_text(__version__)}">\n'
        f"<title>{_text(page.title)}</title>\n"
        f"<style>\n{_asset('report.css')}</style>\n"
        f'</head>\n<body>\n<main class="sheet">\n{body}\n</main>\n'
        f"<script>\n{_asset('report.js')}</script>\n</body>\n</html>\n"
    )


def _asset(name: str) -> str:
    return resources.files("treehawk.htmlreport").joinpath("assets", name).read_text(encoding="utf-8")


def _text(value: object) -> str:
    return html.escape(str(value), quote=True)


def _pct(fraction: float) -> str:
    return f"{fraction * 100:.3f}".rstrip("0").rstrip(".") + "%"


# -- the top of the page -------------------------------------------------------


def _masthead(page: Page) -> str:
    prompt = '<span class="prompt">$</span>' if page.kind == "run" else ""
    chips = "".join(
        f'<li class="chip {notice.level}" title="{_text(notice.text)}">{_text(notice.title)}</li>'
        for notice in page.status
    )
    span = "".join(
        f"<div><dt>{_text(item.label)}</dt><dd><strong>{_text(item.value)}</strong>"
        f"<small>{_text(item.detail)}</small></dd></div>"
        for item in page.span
    )
    return (
        '<header class="masthead">'
        f'<p class="brand">{LOGO}<b>treehawk</b> {_text(page.kind)} report</p>'
        f'<h1 class="headline">{prompt}{_text(page.headline)}</h1>'
        f'<ul class="chips">{chips}</ul>'
        f'<dl class="span">{span}</dl>'
        "</header>"
    )


def _figures(figures: Sequence[Figure]) -> str:
    if not figures:
        return ""
    cells = "".join(
        f'<div class="figure"><span class="label">{_text(figure.label)}</span>'
        f"<strong>{_text(figure.value)}</strong><small>{_text(figure.detail)}</small></div>"
        for figure in figures
    )
    return f'<section class="figures" aria-label="Key figures">{cells}</section>'


def _notices(notices: Sequence[Notice]) -> str:
    if not notices:
        body = '<p class="clear">Nothing to flag.</p>'
    else:
        body = '<ul class="notices">' + "".join(_notice(notice) for notice in notices) + "</ul>"
    count = f" ({len(notices)})" if notices else ""
    return f"<section><header><h2>Warnings{count}</h2></header>{body}</section>"


def _notice(notice: Notice) -> str:
    text = f"<p>{_text(notice.text)}</p>" if notice.text else ""
    return f'<li class="notice {notice.level}"><b data-level="{notice.level}">{_text(notice.title)}</b>{text}</li>'


# -- sections ----------------------------------------------------------------


def _section(section: Section) -> str:
    intro = f'<p class="intro">{_text(section.intro)}</p>' if section.intro else ""
    parts = [f"<header><h2>{_text(section.title)}</h2>{intro}</header>"]
    if section.charts:
        parts.append('<div class="charts">' + "".join(_chart(chart) for chart in section.charts) + "</div>")
    if section.rankings:
        parts.append('<div class="rankings">' + "".join(_ranking(ranking) for ranking in section.rankings) + "</div>")
    if section.table is not None:
        parts.append(_tracks(section.table))
    if section.event_columns:
        parts.append(_table(columns=section.event_columns, rows=section.events))
    if section.facts:
        parts.append(_facts(section.facts))
    return "<section>" + "".join(parts) + "</section>"


def _facts(facts: Iterable[Fact]) -> str:
    rows = "".join(
        f"<div><dt>{_text(fact.label)}</dt><dd{MONO if fact.mono else ''}>{_text(fact.value)}</dd></div>"
        for fact in facts
    )
    return f'<dl class="facts">{rows}</dl>'


# -- charts ------------------------------------------------------------------


def _chart(chart: Chart) -> str:
    data = {
        "x": [round(x, 5) for x in chart.x],
        "t": chart.when,
        "v": [line.readouts for line in chart.lines],
    }
    legend = "".join(
        f'<span class="key" style="--c:var(--{line.color})"><i></i>{_text(line.label)} '
        f'<b data-rest="{_text(line.summary)}">{_text(line.summary)}</b></span>'
        for line in chart.lines
    )
    y_labels = "".join(
        f'<span style="bottom:{_pct(tick.position)}">{_text(tick.label)}</span>' for tick in chart.y_ticks
    )
    x_labels = "".join(
        f'<span class="{_edge(tick.position)}" style="left:{_pct(tick.position)}">{_text(tick.label)}</span>'
        for tick in chart.x_ticks
    )
    caption = f'<p class="caption">{_text(chart.caption)}</p>' if chart.caption else ""
    return (
        f'<figure class="chart" data-group="time" data-series="{_text(json.dumps(data, separators=(",", ":")))}">'
        f"<figcaption><h3>{_text(chart.title)}</h3>"
        f'<p class="legend"><span class="when"></span>{legend}</p></figcaption>'
        f'<div class="plot"><div class="yaxis" aria-hidden="true">{y_labels}</div>'
        f'<div class="area">{_svg(chart)}{_overlay_labels(chart)}<div class="cursor" hidden></div></div>'
        f'<div class="xaxis" aria-hidden="true">{x_labels}</div></div>'
        f"{caption}</figure>"
    )


def _edge(position: float) -> str:
    if position <= 0.02:
        return "start"
    if position >= 0.98:
        return "end"
    return ""


def _svg(chart: Chart) -> str:
    def y_of(value: float) -> float:
        return HEIGHT - min(max(value / chart.top, 0.0), 1.0) * HEIGHT if chart.top else HEIGHT

    shapes: list[str] = []
    shapes.extend(
        f'<line class="gridline" x1="0" x2="{WIDTH}" y1="{y_of(tick.position * chart.top):.2f}" '
        f'y2="{y_of(tick.position * chart.top):.2f}" {CRISP}/>'
        for tick in chart.y_ticks[1:]
    )
    shapes.extend(
        f'<rect class="band" x="{band.start * WIDTH:.2f}" y="0" width="{max(band.end - band.start, 0.0) * WIDTH:.2f}" '
        f'height="{HEIGHT}"/>'
        for band in chart.bands
    )
    for line in chart.lines:
        segments = _segments(x=chart.x, line=line, y_of=y_of)
        if line.fill:
            shapes.extend(
                f'<path class="fill" style="fill:var(--{line.color})" d="{path} V{HEIGHT} H{start:.2f} Z"/>'
                for path, start in segments
            )
        stroke = " ".join(path for path, _start in segments)
        if stroke:
            shapes.append(f'<path class="line" style="stroke:var(--{line.color})" d="{stroke}" {CRISP}/>')
    shapes.extend(
        f'<line class="rule" x1="0" x2="{WIDTH}" y1="{y_of(rule.value):.2f}" y2="{y_of(rule.value):.2f}" {CRISP}/>'
        for rule in chart.rules
    )
    shapes.extend(
        f'<line class="mark {mark.level}" x1="{mark.position * WIDTH:.2f}" x2="{mark.position * WIDTH:.2f}" '
        f'y1="{HEIGHT * 0.9}" y2="{HEIGHT}" {CRISP}><title>{_text(mark.label)}</title></line>'
        for mark in chart.marks
    )
    label = "; ".join(f"{line.label}: {line.summary}" for line in chart.lines)
    return (
        f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" preserveAspectRatio="none" role="img" '
        f'aria-label="{_text(chart.title)} - {_text(label)}">{"".join(shapes)}</svg>'
    )


def _segments(*, x: Sequence[float], line: Line, y_of: Callable[[float], float]) -> list[tuple[str, float]]:
    """One SVG path per unbroken run of readings, with where each starts."""
    segments: list[tuple[str, float]] = []
    current: list[str] = []
    start = 0.0
    previous_y = 0.0
    for position, value in zip(x, line.values, strict=True):
        if value is None:
            if current:
                segments.append((" ".join(current), start))
                current = []
            continue
        px, py = position * WIDTH, y_of(value)
        if not current:
            start = px
            current.append(f"M{px:.2f} {py:.2f}")
        elif line.step:
            current.append(f"H{px:.2f} V{py:.2f}")
        else:
            current.append(f"L{px:.2f} {py:.2f}")
        previous_y = py
    if current:
        if len(current) == 1:
            # A lone reading still deserves a mark: a short flat stroke.
            current.append(f"h1.5 V{previous_y:.2f}")
        segments.append((" ".join(current), start))
    return segments


def _overlay_labels(chart: Chart) -> str:
    """Words placed over the plot in HTML, so they never stretch with the SVG."""
    labels = [
        f'<span class="label rule-label" style="bottom:{_pct(min(rule.value / chart.top, 1.0) if chart.top else 0.0)}">'
        f"{_text(rule.label)}</span>"
        for rule in chart.rules
    ]
    labels.extend(
        f'<span class="label band-label" style="left:{_pct(band.start)};width:{_pct(band.end - band.start)}">'
        f"{_text(band.label)}</span>"
        for band in chart.bands
        if band.label and band.end - band.start >= HTML.band_label_share
    )
    return "".join(labels)


# -- who did it --------------------------------------------------------------


def _who(process: Process, *, width: int = 0) -> str:
    found = ""
    if process.found:
        found = f'<span class="found" style="--c:var(--{_found_color(process.found)})">{_text(process.found)}</span>'
    command = process.command if width <= 0 or len(process.command) <= width else process.command[: width - 1] + "…"
    return (
        f'<div class="who"><span class="name"><b>{_text(process.name)}</b>'
        f'<span class="pid">{process.pid}</span>{found}</span>'
        f'<code title="{_text(process.command)}">{_text(command)}</code></div>'
    )


def _found_color(found: str) -> str:
    return f"s{DISCOVERY_ORDER.index(found) + 1}" if found in DISCOVERY_ORDER else "s1"


def _ranking(ranking: Ranking) -> str:
    widest = max((entry.value for entry in ranking.entries), default=0.0)
    items = "".join(
        f'<li>{_who(entry.process, width=160)}<span class="value">{_text(entry.text)}</span>'
        f'<span class="meter"><i style="width:{_pct(entry.value / widest if widest else 0.0)};'
        f'--c:var(--{_found_color(entry.process.found)})"></i></span></li>'
        for entry in ranking.entries
    )
    body = f"<ol>{items}</ol>" if items else '<p class="intro">Nothing recorded.</p>'
    return f'<div class="ranking"><h3>{_text(ranking.title)}</h3>{body}</div>'


def _tracks(tracks: Tracks) -> str:
    start, end = tracks.axis
    head = (
        '<th aria-sort="ascending"><button type="button">Process</button></th>'
        f'<th class="life"><button type="button">Alive</button><span class="ends"><span>{_text(start)}</span>'
        f"<span>{_text(end)}</span></span></th>"
    )
    head += "".join(_header(column) for column in tracks.columns)
    rows: list[str] = []
    for track in tracks.rows:
        life = track.lifetime
        color = _found_color(track.process.found)
        cells = (
            f'<td class="proc" data-sort="{_text(track.process.name.lower())}">{_who(track.process, width=120)}</td>'
            f'<td class="life" data-sort="{life.start:.6f}"><div class="life-track" title="{_text(life.label)}">'
            f'<i style="left:{_pct(life.start)};width:{_pct(max(life.end - life.start, 0.0))};--c:var(--{color})"></i>'
            "</div></td>"
        )
        cells += "".join(_cell(cell) for cell in track.cells)
        rows.append(f"<tr>{cells}</tr>")
    note = f'<p class="cut">{_text(tracks.note)}</p>' if tracks.note else ""
    return (
        f'<div class="table-wrap"><table class="tracks" data-sortable><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>{note}"
    )


def _table(*, columns: Sequence[Column], rows: Sequence[Sequence[Cell]]) -> str:
    head = "".join(_header(column) for column in columns)
    body = "".join(
        "<tr>"
        + "".join(_cell(cell, numeric=column.numeric) for cell, column in zip(row, columns, strict=True))
        + "</tr>"
        for row in rows
    )
    return (
        f'<div class="table-wrap"><table data-sortable><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _header(column: Column) -> str:
    kind = ' class="num"' if column.numeric else ""
    hint = f' title="{_text(column.hint)}"' if column.hint else ""
    return f'<th{kind}{hint}><button type="button">{_text(column.label)}</button></th>'


def _cell(cell: Cell, *, numeric: bool = True) -> str:
    classes = " ".join(name for name in ("num" if numeric else "", str(cell.level or "")) if name)
    sort = f"{cell.sort:.6f}" if isinstance(cell.sort, float) else str(cell.sort)
    return f'<td class="{classes}" data-sort="{_text(sort)}">{_text(cell.text)}</td>'
