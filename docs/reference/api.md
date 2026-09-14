# Python API

treehawk is a command-line tool first. The Python API documented here is what you
build on when you [extend it](../development/extending.md): the protocols the
layers meet at, the settings, the error hierarchy, and the functions that read a
log back.

!!! warning "Not yet stable"

    treehawk is at version 0.x. These interfaces may change between minor
    releases; pin the version you build against.

## Protocols

Every component asks for the one capability it uses. Implementations satisfy these
structurally: no inheritance is needed.

::: treehawk.core.interfaces
    options:
      show_root_heading: false
      members:
        - ProcessSource
        - GroupMetricSource
        - ProcessLauncher
        - HostInfoSource
        - ProcessMatcher
        - ExpansionStrategy
        - ExpansionContext
        - MetricCollector
        - Sink
        - Clock
        - Record

## Settings

::: treehawk.core.config
    options:
      show_root_heading: false

## Models

::: treehawk.core.models
    options:
      show_root_heading: false
      members:
        - ProcInfo
        - ProcSample
        - GroupMetrics
        - Snapshot
        - HostInfo
        - LaunchedWorkload

## Errors

::: treehawk.core.errors
    options:
      show_root_heading: false

## Reading logs

::: treehawk.report
    options:
      show_root_heading: false
      members:
        - Report
        - build_report
        - read_records

::: treehawk.charts.report.write_pdf_report

## Narrowing log values

::: treehawk.core.values
    options:
      show_root_heading: false
