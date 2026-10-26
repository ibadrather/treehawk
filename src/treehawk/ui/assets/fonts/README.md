# Brand fonts

Poppins (regular, semibold, bold) and Fira Code (regular), the fonts of the
treehawk docs. The HTML report inlines the `.woff2` files and the PDF report
registers the `.ttf` files with matplotlib, so both look the same on any
machine without fetching anything.

Both are under the SIL Open Font License 1.1 (`OFL-*.txt`). The files are
subsets - Latin, Latin Extended, general punctuation and arrows - made with
fontTools; Fira Code's ligatures are left out, so a command line reads exactly
as it was typed. Anything outside the subset falls back to a system font.

```bash
pyftsubset Poppins-Regular.ttf --no-hinting --desubroutinize \
  --unicodes="U+0020-007E,U+00A0-024F,U+0300-036F,U+2000-206F,U+2070-209F,U+20AC,U+2122,U+2190-21FF,U+2212,U+2264-2265,U+2248,U+2260,U+221E" \
  --layout-features='kern,liga,calt,tnum,lnum' [--flavor=woff2]
# Fira Code: --layout-features='kern,tnum,lnum'
```
