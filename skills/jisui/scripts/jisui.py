# /// script
# dependencies = ["numpy", "opencv-python-headless", "pillow"]
# ///
"""Jisui page processing (SKILL.md of the jisui skill): deskew, crop to one frame per book, B&W with pictures kept grayscale.

usage: uv run jisui.py SRC_DIR OUT_DIR [--threshold N | --gray | --color] [--no-deskew 1,12,...] [--picture 3,24,...] [--dpi N]
                       [--cover first|FILE] [--jacket last|FILE] [--jacket-rotate 0|90|180|270]
The scan resolution comes from the images' DPI tag (--dpi overrides; 300 if neither); no page size is assumed.
--cover: the first scan (first) or FILE is copied unchanged to OUT_DIR/0000.<ext>; without it every scan is a body page.
--jacket: the last scan (last) or FILE is only rotated upright (clockwise degrees), colour and size kept, as the last page.
Writes OUT_DIR/NNNN.png (1-bit, or grayscale when the page has a picture) and OUT_DIR/../<name>-report.json.
"""
import json
import shutil
import sys
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

DEFAULT_DPI = 300  # only when the images carry no DPI tag and --dpi isn't given
MARGIN_MM = 2
ANGLES = np.round(np.arange(-3, 3.001, 0.1), 1)
INK = 160  # fixed level for finding content (not the B&W threshold)
THRESHOLD = 200  # B&W: darker than this becomes black (Otsu gave ~160, too faint)


def mm(dpi, x):
    return int(round(x * dpi / 25.4))


def load(path, body_h, color=False):
    im = cv2.imread(str(path), cv2.IMREAD_COLOR if color else cv2.IMREAD_GRAYSCALE)
    if im.shape[0] != body_h:  # e.g. 600dpi colour cover: bring it to body resolution
        s = body_h / im.shape[0]
        im = cv2.resize(im, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return im


def geometry(w, h, rot, shear):
    """Rotation by rot° about the centre, then horizontal shear by shear° (sheet-fed scans drift sideways:
    text lines stay level while the margins slant)."""
    r = np.vstack([cv2.getRotationMatrix2D((w / 2, h / 2), rot, 1), [0, 0, 1]])
    t = np.tan(np.radians(shear))
    s = np.array([[1, t, -t * h / 2], [0, 1, 0], [0, 0, 1]])
    return (s @ r)[:2]


def rotate(im, geo, fill):
    """Apply geo = (rotation°, shear°)."""
    if geo == (0, 0):
        return im
    h, w = im.shape[:2]
    return cv2.warpAffine(im, geometry(w, h, *geo), (w, h), flags=cv2.INTER_LINEAR, borderValue=(fill, fill, fill))


def components(im, dpi, mask=None):
    """Ink components that count as content: no dust, no shadows/lines along the scan edge.
    Returns their boxes; mask="text" gives a boolean image of those components, mask="edge" one of the
    shadows/lines along the scan edge (erased when rendering, as a tight frame can reach them)."""
    h, w = im.shape
    n, lab, st, _ = cv2.connectedComponentsWithStats((im < INK).astype(np.uint8), connectivity=8)
    keep, ids, edge = [], [], []
    for i, (x, y, cw, ch, area) in enumerate(st[1:], 1):
        if area < mm(dpi, 0.3) ** 2:
            continue
        on_edge = x == 0 or y == 0 or x + cw == w or y + ch == h
        near = mm(dpi, 3)  # thin scanner lines/dashes a few px inside the edge (or moved in by deskew)
        near_edge = x < near or y < near or x + cw > w - near or y + ch > h - near
        if (on_edge and min(cw, ch) < mm(dpi, 5)) or (near_edge and min(cw, ch) < mm(dpi, 2)):
            edge.append(i)
            continue
        keep.append((x, y, x + cw, y + ch))
        ids.append(i)
    # Specks: a small mark with no larger ink within 3 mm is dust, not punctuation (it would widen the box).
    small = [max(b[2] - b[0], b[3] - b[1]) < mm(dpi, 1) for b in keep]
    big = np.array([b for b, s in zip(keep, small) if not s]).reshape(-1, 4)
    r = mm(dpi, 3)
    lone = [s and not ((big[:, 0] < b[2] + r) & (big[:, 2] > b[0] - r) & (big[:, 1] < b[3] + r) & (big[:, 3] > b[1] - r)).any()
            for b, s in zip(keep, small)]
    keep = [b for b, d in zip(keep, lone) if not d]
    ids = [i for i, d in zip(ids, lone) if not d]
    return {"text": lambda: np.isin(lab, ids), "edge": lambda: np.isin(lab, edge), None: lambda: keep}[mask]()


def deskew_angle(im, dpi):
    """(rotation°, shear°): rotation levels the text lines (row profile), then shear straightens the
    margins (column profile). Scored on text only, since the scanner's edge lines are straight in the
    scan and would pull every page to 0°."""
    b = components(im, dpi, mask="text")
    h, w = b.shape
    if b.any(1).sum() < 0.1 * h:  # a few lines (title, divider, short page) can't show a skew reliably
        return (0.0, 0.0)
    ys, xs = np.nonzero(b[::2, ::2])
    pts = np.stack([xs * 2.0, ys * 2.0, np.ones(len(xs))])

    def sharp(v):  # transform the ink pixels' coordinates and histogram them: no image interpolation,
        v = np.bincount(np.round(v - v.min()).astype(int) // 2).astype(float)  # which blurs all but 0°
        return (np.diff(v) ** 2).sum()

    def best(scores):
        i = int(np.argmax(scores))
        return float(ANGLES[i]) if scores[i] > 1.005 * scores[len(ANGLES) // 2] else 0.0

    rot = best([sharp((geometry(w, h, a, 0) @ pts)[1]) for a in ANGLES])

    # Shear from the flush margins: each pixel row's first/last ink, sheared, should pile up on one x.
    # (The column profile of body text has false peaks at large shears.)
    x, y = geometry(w, h, rot, 0) @ pts
    r = np.round(y / 2).astype(int)
    r -= r.min()
    left = np.full(r.max() + 1, np.inf)
    right = np.full(r.max() + 1, -np.inf)
    np.minimum.at(left, r, x)
    np.maximum.at(right, r, x)
    rows = np.isfinite(left)
    ry = np.nonzero(rows)[0] * 2.0 + y.min() - h / 2
    left, right = left[rows], right[rows]
    if np.ptp(ry) < h / 3:  # too little text to tell a slant from ragged lines
        return (rot, 0.0)

    def flush(s):
        t = np.tan(np.radians(s))
        return sum(np.bincount(np.round((e + t * ry - (e + t * ry).min()) / 3).astype(int)).max() for e in (left, right))

    scores = [flush(s) for s in ANGLES]
    i = int(np.argmax(scores))
    return (rot, float(ANGLES[i]) if scores[i] > 1.1 * scores[len(ANGLES) // 2] else 0.0)


def analyze(job):
    path, body_h, dpi, skip_deskew = job
    im = load(path, body_h)
    angle = (0.0, 0.0) if skip_deskew else deskew_angle(im, dpi)
    im = rotate(im, angle, 255)
    comps = components(im, dpi)
    box = None
    if comps:
        a = np.array(comps)
        box = [int(a[:, 0].min()), int(a[:, 1].min()), int(a[:, 2].max()), int(a[:, 3].max())]
    return {"file": path.name, "angle": angle, "box": box, "shape": im.shape}


def picture_mask(im, dpi):
    """Areas dense with mid-tones or ink (photos, figures) stay grayscale; text is sparse black on white."""
    mid = ((im > 60) & (im < 200)).astype(np.float32)
    dark = (im < 200).astype(np.float32)
    k = mm(dpi, 3)
    k5 = mm(dpi, 5)
    dense = ((cv2.blur(mid, (k, k)) > 0.35) | (cv2.blur(dark, (k5, k5)) > 0.6)).astype(np.uint8)
    dense = cv2.morphologyEx(dense, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(dense)
    mask = np.zeros_like(dense)
    pad = mm(dpi, 1)
    for i, (x, y, w, h, area) in enumerate(st[1:], 1):
        if area >= mm(dpi, 10) ** 2:
            mask[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad] = 1
    return mask.astype(bool)


def render(job):
    path, body_h, dpi, angle, box, frame, threshold, color, out = job
    im = rotate(load(path, body_h, color), angle, 255)
    im[components(im if im.ndim == 2 else cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), dpi, mask="edge")] = 255
    pad = max(im.shape)
    im = cv2.copyMakeBorder(im, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    fx0, fy0, fx1, fy1 = (v + pad for v in frame)
    fw, fh = fx1 - fx0, fy1 - fy0
    shrunk = None
    if box:
        m = mm(dpi, MARGIN_MM)
        bx0, by0, bx1, by1 = (box[0] + pad - m, box[1] + pad - m, box[2] + pad + m, box[3] + pad + m)
        if bx0 < fx0 or by0 < fy0 or bx1 > fx1 or by1 > fy1:  # content sticks out: shrink it into the frame
            rx0, ry0, rx1, ry1 = min(fx0, bx0), min(fy0, by0), max(fx1, bx1), max(fy1, by1)
            region = im[ry0:ry1, rx0:rx1]
            s = shrunk = min(fw / region.shape[1], fh / region.shape[0])
            region = cv2.resize(region, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            page = np.full((fh, fw) + im.shape[2:], 255, np.uint8)
            oy, ox = (fh - region.shape[0]) // 2, (fw - region.shape[1]) // 2
            page[oy:oy + region.shape[0], ox:ox + region.shape[1]] = region
    if shrunk is None:
        page = im[fy0:fy1, fx0:fx1]
    if threshold is None:  # --gray / --color: no B&W
        cv2.imwrite(str(out), page)
        return shrunk, False
    pic = picture_mask(page, dpi)
    bw = np.where(page >= threshold, 255, 0).astype(np.uint8)
    if pic.any():
        cv2.imwrite(str(out), np.where(pic, page, bw))
    else:
        cv2.imwrite(str(out), bw, [cv2.IMWRITE_PNG_BILEVEL, 1])
    return shrunk, bool(pic.any())


def register(boxes, dpi, m):
    """One frame size per book, placed per page on that page's text block: scans wander by several mm,
    so a fixed position would need that much extra margin. Anchor = where a full page's text block
    starts (x0, y0). Pages whose own box can't show it (short, centred, chapter openers, blank) take it
    from the nearest pages of the same parity, so they keep their original layout."""
    n = len(boxes)
    have = [k for k in range(n) if boxes[k]]
    W = np.median([boxes[k][2] - boxes[k][0] for k in have])
    H = np.median([boxes[k][3] - boxes[k][1] for k in have])
    ax = {k: boxes[k][0] for k in have if abs(boxes[k][2] - boxes[k][0] - W) <= 0.02 * W}  # full-width text
    ay = {k: boxes[k][1] for k in have if boxes[k][3] - boxes[k][1] >= 0.97 * H}  # full-height text

    def near(vals, k):  # ponytail: median of the 4 nearest same-parity pages; smooth fit if scans drift fast
        c = [j for j in vals if j % 2 == k % 2] or list(vals)
        return float(np.median([vals[j] for j in sorted(c, key=lambda j: abs(j - k))[:4]]))

    tol = mm(dpi, 5)  # a short page's top still lines up (running head); a chapter opener's sinks lower
    X = [ax[k] if k in ax else near(ax, k) for k in range(n)]
    Y = [ay[k] if k in ay else boxes[k][1] if boxes[k] and abs(boxes[k][1] - near(ay, k)) < tol else near(ay, k)
         for k in range(n)]
    fw = int(np.percentile([boxes[k][2] - X[k] for k in have], 98)) + 2 * m
    fh = int(np.percentile([boxes[k][3] - Y[k] for k in have], 98)) + 2 * m
    frames = [(int(X[k]) - m, int(Y[k]) - m, int(X[k]) - m + fw, int(Y[k]) - m + fh) for k in range(n)]
    return frames, fw, fh


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    threshold = float(sys.argv[sys.argv.index("--threshold") + 1]) if "--threshold" in sys.argv else THRESHOLD
    color = "--color" in sys.argv  # analysis stays grayscale; only the rendered pages keep colour
    if "--gray" in sys.argv or color:
        threshold = None
    no_deskew = {int(k) for k in sys.argv[sys.argv.index("--no-deskew") + 1].split(",")} if "--no-deskew" in sys.argv else set()
    pictures = {int(k) for k in sys.argv[sys.argv.index("--picture") + 1].split(",")} if "--picture" in sys.argv else set()
    arg = lambda f: sys.argv[sys.argv.index(f) + 1] if f in sys.argv else None
    cover, jacket = arg("--cover"), arg("--jacket")
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in src.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff"))
    if cover:  # inserted as-is
        cover, files = (files[0], files[1:]) if cover == "first" else (Path(cover), files)
        shutil.copy(cover, out / f"0000{cover.suffix.lower()}")
    if jacket:
        jacket, files = (files[-1], files[:-1]) if jacket == "last" else (Path(jacket), files)
        cw = int(arg("--jacket-rotate") or 0) % 360
        with Image.open(jacket) as im:
            dpi_tag = im.info.get("dpi", (DEFAULT_DPI, DEFAULT_DPI))
            (im.rotate(-cw, expand=True) if cw else im).save(out / f"{len(files) + 1:04d}.png", dpi=dpi_tag)
    heights = [cv2.imread(str(p), cv2.IMREAD_GRAYSCALE).shape[0] for p in files]
    body_h = int(np.median(heights))
    if "--dpi" in sys.argv:
        dpi = float(sys.argv[sys.argv.index("--dpi") + 1])
    else:
        tag = float(np.median([Image.open(p).info.get("dpi", (0, 0))[1] for p in files]))
        dpi = tag if tag >= 150 else DEFAULT_DPI  # 72/96 are placeholder tags (e.g. pdfimages), not scan resolution
        if tag < 150:
            print(f"no usable DPI tag ({tag:g}): assuming {DEFAULT_DPI} dpi (pass --dpi N if that's wrong)", file=sys.stderr)
    dpi *= body_h / np.median(heights)  # load() brings every page to body_h

    with Pool() as pool:
        info = pool.map(analyze, [(p, body_h, dpi, k in no_deskew) for k, p in enumerate(files, 1)])

    m = mm(dpi, MARGIN_MM)
    frames, fw, fh = register([None if k in pictures else i["box"] for k, i in enumerate(info, 1)], dpi, m)
    # Picture pages (--picture: full-bleed art, title spreads) don't size the text frame. Their own frame has the
    # text height and the width the widest picture needs at that height, centred on each picture.
    pw = fw
    for k in pictures:
        if b := info[k - 1]["box"]:
            pw = max(pw, int(np.ceil((b[2] - b[0] + 2 * m) * min(1, fh / (b[3] - b[1] + 2 * m)))))
    for k in pictures:
        if b := info[k - 1]["box"]:
            x0, y0 = (b[0] + b[2]) // 2 - pw // 2, (b[1] + b[3]) // 2 - fh // 2
            frames[k - 1] = (x0, y0, x0 + pw, y0 + fh)

    jobs = [(p, body_h, dpi, i["angle"], i["box"], frames[k - 1], threshold, color, out / f"{k:04d}.png")
            for k, (p, i) in enumerate(zip(files, info), 1)]
    with Pool() as pool:
        res = pool.map(render, jobs)

    report = {
        "dpi": round(dpi, 1), "threshold": threshold, "frame_px": [fw, fh],
        "frame_mm": [round(fw / dpi * 25.4, 1), round(fh / dpi * 25.4, 1)],
        "pictures": sorted(pictures), "picture_frame_px": [pw, fh], "picture_frame_mm": [round(pw / dpi * 25.4, 1), round(fh / dpi * 25.4, 1)],
        "pages": len(files) + bool(cover) + bool(jacket), "blank": [k for k, i in enumerate(info, 1) if not i["box"]],
        "deskewed": {k: i["angle"] for k, i in enumerate(info, 1) if any(i["angle"])},
        "shrunk": {k: round(r[0], 3) for k, r in enumerate(res, 1) if r[0] is not None},
        "grayscale_parts": [k for k, r in enumerate(res, 1) if r[1]],
    }
    (out.parent / f"{out.name}-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: (v if not isinstance(v, (list, dict)) or len(v) < 30 else f"{len(v)} items")
                      for k, v in report.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
