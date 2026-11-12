# jisui (自炊)

A Claude Code skill for processing scanned page images (ScanSnap or any scanner) into a clean ZIP, CBZ or PDF:

- deskew (rotation and sheet-feed shear, ±3° in 0.1° steps)
- minimal 2 mm margins, one uniform page size per book
- optional B&W binarization with pictures kept grayscale
- cover / jacket handling: include, leave out, or save separately; from a given file, the scans, or the web
- optional OCR text layer (ocrmypdf + Apple OCR)
- optional PDF bookmarks from the table of contents
- Title / Author metadata editing without recompression

The skill instructions (`skills/jisui/SKILL.md`) are written in English; Claude follows them regardless of the language you talk to it in.

## Install

```sh
claude plugin marketplace add 0stellensatz/skill-jisui
claude plugin install jisui@0stellensatz-plugins
```

Then just ask, e.g. "turn the scans in ~/Scans/book into a PDF", or invoke `/jisui:jisui`.

## Requirements

- [uv](https://docs.astral.sh/uv/) (runs `scripts/jisui.py` with its own dependencies: numpy, opencv, pillow)
- For PDF output: `img2pdf`, `pikepdf`
- For OCR: `ocrmypdf`, `qpdf`, `pdftotext` (poppler); Apple OCR requires macOS

## License

MIT
