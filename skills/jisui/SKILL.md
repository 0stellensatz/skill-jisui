---
name: jisui
description: Jisui (自炊) processing of scanned page images into a clean ZIP, CBZ or PDF — deskew (rotation + sheet-feed shear), minimal margins with one uniform page size, B&W binarization with pictures kept grayscale, cover/jacket handling (include, leave out, or save as separate files; from a given file, the scans, or the web), OCR text layer (ocrmypdf + Apple OCR), PDF bookmarks from the table of contents, and Title/Author metadata editing without recompression. Use this whenever the user has a folder of scanned images (from a ScanSnap or other scanner) of a book, magazine, manual, notes, or any document and wants them turned into a ZIP/CBZ/PDF, cleaned up, deskewed, cropped, binarized, OCR'd or bookmarked — even if they don't say "jisui", e.g. "turn these scans into a PDF", "make a PDF from my scanned images", "OCR this scanned PDF", "add a TOC to this scan".
---

# Jisui (自炊) processing

Rules for scanned images → ZIP / CBZ / PDF. Applies not only to books but to anything scanned (magazines, documents, notes, etc.).
This skill does not decide the output file's name or location. Follow the caller (the project's CLAUDE.md or the user's instructions); if neither says, ask the user.

## Before starting

Ask the user (one `AskUserQuestion` call) for whatever the caller hasn't already decided:

- Output format: ZIP / CBZ / PDF.
- Cover (front cover): placement — first page of the output / not used / separate file next to the output; and source — a file the user gives / the first scan / an image found on the web.
- Jacket (cover jacket scan or image): the same two questions; source — a file the user gives / the last scan / an image found on the web.

A source from the scans is taken out of the body pages even when its placement is "not used" (it isn't processed or kept). If there is no cover/jacket source, every scan is a body page. For a web image, search for the edition matching the scans (title, publisher, ISBN if visible), download the largest version into the scratch area, show it to the user and get it confirmed before using it.

## Page processing

0. Cover / jacket (as decided in "Before starting"). All the steps below apply to body pages only.
   - Cover: used as-is, unprocessed (still in color, dimensions unchanged). Included → the first page.
   - Jacket: only rotate it to the correct orientation (still in color, dimensions unchanged). Rotate pixel-exactly; if the JPEG's dimensions don't allow lossless rotation, use PNG. Included → the last page; in the PDF, make only that page actual size (per its DPI).
   - Separate file: save it next to the output as `<output name>-cover.<ext>` / `<output name>-jacket.png`, unchanged from what the script wrote.
1. Skew correction: for each page, detect the skew within ±3° in 0.1° steps and correct it.
   - Detect and correct rotation (the lines are tilted) and shear (the lines are level but only the left/right edges of the text block are slanted — this happens when the paper slides sideways in a sheet feeder) separately.
   - Pages with little text (title pages, half-titles, short pages) can't be checked reliably for skew, so don't correct them.
2. Margin minimization: trim to leave only 2mm (24px at 300dpi) of margin on every side of the text block.
   - Don't include scanner shadows, thin lines at the page edges, or small specks in the text block. Whiten shadows and lines at the page edges.
3. Uniform dimensions: all body pages of one volume get the same dimensions.
   - Decide one crop-box size per volume, and position it per page to fit that page's text block (the text block position shifts a few mm from scan to scan). Pages with little text (title pages, chapter openings, short pages) use the position of a nearby page on the same side (odd/even), so they keep their original layout.
   - Pages that overflow the box are scaled down slightly to fit.
   - Only when instructed: take image pages such as full-page illustrations and title pages out of the body box and give them a shared image-page box with the same height as the body but wider (`--picture`). This allows smaller margins on the body pages.
4. Blank pages: don't skip them; keep them as blank pages.
5. Keep every page: don't delete any pages, including advertisements.
6. B&W: binarize to black and white only when instructed (it is lossy, so not by default).
   - Binarize text, but don't binarize image areas such as photos and illustrations; make them grayscale.
   - Use a threshold of 200 (0–255; pixels darker than this become black) as the baseline. Automatic selection (Otsu, around 160) made the text too faint.
   - After the work, report the threshold used and ask the user whether that value looks right.
7. OCR: only when instructed, add an OCR text layer to the PDF (`ocrmypdf -j 2 -l jpn+eng --output-type pdf`, images unchanged; match `-l` to the content's language, e.g. `kor+eng` for Korean). Apple OCR tends to time out on large page counts, so split with `qpdf` into 50-page chunks, OCR each, redo only the failed ones, then merge.
   - When used repeatedly, Apple OCR (LiveText) can start silently returning empty text layers partway through, without any error. Before merging, check each page with `pdftotext` to confirm text was extracted; for chunks with empty body pages, wait a bit and redo them. If that doesn't fix it, restart the Mac.
8. Bookmarks (indexing): only when instructed, add table-of-contents bookmarks (outline) to the PDF.
   - Read the table-of-contents pages and add parts/chapters/sections in the same hierarchy as the TOC, with headings exactly as printed (include the preface, afterword, commentary, notes, index, etc. if they are in the TOC). Put an entry for the table of contents itself first, pointing to the TOC page. Parts/chapters with no page number in the TOC point to their title page or the page with their heading.
   - Determine the mapping between printed page numbers and PDF pages by reading page numbers in several places. It can shift (e.g. because of frontispieces), so check that the heading actually appears on the target page for every top-level entry and some lower-level ones.
   - Write it with `pikepdf` using the same procedure as metadata editing (no image recompression), and set `/PageMode /UseOutlines`. Read it back after writing to verify. If there is no table of contents, don't add bookmarks; tell the user.

## Running

`uv run <this skill's folder>/scripts/jisui.py <image folder> <output folder> [--threshold N | --gray | --color] [--no-deskew 1,12,...] [--picture 3,24,...] [--dpi N] [--cover first|FILE] [--jacket last|FILE] [--jacket-rotate 0|90|180|270]` performs step 0 (`--cover`: copies the first scan or FILE as-is as `0000.<ext>`; `--jacket`: takes the last scan or FILE, rotates it clockwise by `--jacket-rotate`, and writes it as the last `NNNN.png`; pass them whenever there is a cover/jacket source, whatever its placement), steps 1–3, and step 6 (B&W) (with `--gray`, no B&W and pages stay grayscale; with `--color`, pages stay in color), and writes the page images (PNG) and a report (`<output folder>-report.json`: threshold, box dimensions, blank/scaled-down pages and pages with grayscale areas). Page numbers are the output numbers (`NNNN.png`; the cover is 0, the first body page 1). Pass page numbers not to deskew to `--no-deskew`, and image-page numbers to `--picture` (the report then also gives the image-page box dimensions; the height is the same as the body, so one `--imgsize` is enough). Put working output in the scratch area; never write to the original scans or the user's folders.

- Don't assume a trim size. By default it minimizes margins and makes the body pages uniform in size. The resolution for mm↔px conversion comes from the image's DPI tag; if there is no tag or it is below 150 (a placeholder like 72), assume 300dpi (otherwise pass `--dpi N`).
- When making a PDF, set the size with `img2pdf --imgsize x<box height in mm>` (the output PNGs carry no DPI info). Build the cover-jacket page separately without `--imgsize` (actual size per its DPI tag) and append it with `pikepdf`.
- After processing, look at a few pages as images (skew, trimming, binarization) and check that the blank/scaled-down pages in the report make sense before producing output.

## Output

Before packing, take the cover/jacket images whose placement is "not used" or "separate file" out of the page set (and save the separate ones as in step 0).

- ZIP: put the processed page images at the top level of the archive, with filenames in page order. Don't recompress the images (`zip -0`). Don't include `.DS_Store` and the like.
- CBZ: the same as ZIP, with the `.cbz` extension. Readers use the first image as the thumbnail. Add `ComicInfo.xml` only through metadata editing.
- PDF: lay out the same images as the ZIP without recompression (`img2pdf`). OCR follows step 7, bookmarks step 8.
- Mode names: decided by the body pages — `color` / `grayscale` / `bw` (a color cover alone doesn't make it `color`). Add `+ocr` if there is an OCR layer and `+toc` if there are bookmarks (never on a ZIP/CBZ). Whether to put it in the filename is up to the caller.

## Metadata editing (Title / Author)

Take the values from the caller (catalog, user). Never guess and fill them in.

- Only the Title and Author fields. Don't put translators in Author.
- PDF: write Title and Author to both the Info dictionary and XMP, without recompressing pages (`pikepdf`).
- EPUB: change only the OPF's main `dc:title` and each `dc:creator` (and the `dcterms:*` / `file-as` meta that `refines` them); touch nothing else. Rewrite the ZIP keeping the entry order and compression methods, with `mimetype` first and uncompressed. Standard Ebooks' subtitle / long-title entries can be left as they are.
- CBZ: write `ComicInfo.xml` (`<Title>`, `<Writer>`) at the end of the archive, stored (`zip -0`), keeping the image entries unchanged. For right-to-left books (Japanese vertical text, manga), also add `<Manga>YesAndRightToLeft</Manga>` if the user confirms.
- ZIP (scanned images): has no metadata, so skip it.
- Work on a copy in the scratch area, verify it (page count / entries unchanged, metadata read back matches the given values), then replace the original file. Replacing overwrites the user's file, so confirm first unless the caller has approved it.
