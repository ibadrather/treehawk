"""PDF reporting.

The only part of treehawk that imports matplotlib, and only :mod:`report` and
the page modules do: the CLI imports them lazily so that a plain watch never
pays for it, and :mod:`series` stays importable without it - the HTML report
reads logs through it too. Structure mirrors the rest of the codebase:
reshaping the log into series (:mod:`series`) is separate from drawing it
(:mod:`pages`), and the writer (:mod:`report`) knows only that a page is
something with a title that can draw itself onto a figure.
"""
