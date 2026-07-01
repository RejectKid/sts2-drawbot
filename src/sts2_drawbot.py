from __future__ import annotations

import argparse
import dataclasses
import json
import os
import platform
import re
import sys
import time
import urllib.parse
import math
from pathlib import Path
from typing import Iterable


@dataclasses.dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


@dataclasses.dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclasses.dataclass(frozen=True)
class DrawTransform:
    scale: float
    offset_x: float
    offset_y: float


Stroke = list[Point]

STOP_QUERY_WORDS = {
    "a", "an", "and", "art", "black", "by", "clip", "coloring", "drawing",
    "for", "icon", "in", "line", "of", "outline", "page", "simple", "svg",
    "the", "to", "white", "with",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def is_windows() -> bool:
    return platform.system() == "Windows"


def ensure_runtime_imports(include_automation: bool = False):
    missing: list[str] = []
    try:
        import PIL  # noqa: F401
    except Exception:
        missing.append("pillow")
    try:
        import requests  # noqa: F401
    except Exception:
        missing.append("requests")
    try:
        import ddgs  # noqa: F401
    except Exception:
        missing.append("ddgs")
    if include_automation:
        try:
            import pyautogui  # noqa: F401
        except Exception:
            missing.append("pyautogui")
        if is_windows():
            try:
                import win32gui  # noqa: F401
            except Exception:
                missing.append("pywin32")
            try:
                import psutil  # noqa: F401
            except Exception:
                missing.append("psutil")

    if missing:
        print("Missing dependencies:", ", ".join(missing), file=sys.stderr)
        print("Run the install command for your platform first.", file=sys.stderr)
        raise SystemExit(2)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80] or "image"


def prompt_terms(prompt: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", prompt.lower()) if len(w) > 2 and w not in STOP_QUERY_WORDS]


def fetch_wikimedia_image(prompt: str, out_dir: Path) -> Path:
    import requests

    session = requests.Session()
    session.headers.update({"User-Agent": "sts2-drawbot/1.0"})

    candidates = collect_search_candidates(session, prompt)

    if not candidates:
        raise RuntimeError(f"No downloadable web image found for {prompt!r}. Try --image or --url instead.")

    downloaded = download_ranked_candidates(session, prompt, candidates, out_dir, max_downloads=8, max_candidates=40)
    if downloaded:
        _, target, title, mime, image_url = downloaded[0]
        print(f"Selected: {title}")
        print(f"Mime: {mime}")
        print(f"Source: {image_url}")
        return target

    raise RuntimeError(f"Could not download any search result for {prompt!r}.")


def collect_search_candidates(session, prompt: str) -> list[tuple[int, str, str, str]]:
    base_query = prompt.strip()
    terms = prompt_terms(base_query)
    candidates = search_openverse_images(session, base_query, terms)
    candidates.extend(search_ddg_images(base_query, terms))
    candidates.extend(search_google_custom_images(session, base_query, terms))
    candidates.extend(search_commons_images(session, base_query, terms))
    candidates.sort(reverse=True, key=lambda c: c[0])
    return candidates


def download_ranked_candidates(
    session,
    prompt: str,
    candidates: list[tuple[int, str, str, str]],
    out_dir: Path,
    max_downloads: int = 8,
    max_candidates: int = 40,
) -> list[tuple[float, Path, str, str, str]]:
    last_error: Exception | None = None
    skipped = 0
    downloaded: list[tuple[float, Path, str, str, str]] = []
    for candidate_index, (search_score, title, image_url, mime) in enumerate(candidates[:max_candidates]):
        try:
            target, content_type = download_candidate_image(session, prompt, image_url, out_dir, candidate_index)
            visual_score = score_downloaded_image(target)
            total_score = search_score + visual_score
            downloaded.append((total_score, target, title, mime or content_type, image_url))
            print(f"Candidate: {title} score={total_score:.1f}")
            if len(downloaded) >= max_downloads:
                break
        except Exception as exc:
            skipped += 1
            last_error = exc
            continue

    if downloaded:
        downloaded.sort(reverse=True, key=lambda c: c[0])
        return downloaded

    raise RuntimeError(f"Could not download any search result for {prompt!r} after skipping {skipped} blocked results: {last_error}")


def download_candidate_image(session, prompt: str, image_url: str, out_dir: Path, candidate_index: int = 0) -> tuple[Path, str]:
    parsed = urllib.parse.urlparse(image_url)
    ext = Path(parsed.path).suffix or ".jpg"
    if ext.lower() not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}:
        ext = ".jpg"
    target = out_dir / f"{slugify(prompt)}-candidate-{candidate_index:02d}{ext}"
    origin = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    if origin:
        headers["Referer"] = origin
    img = session.get(image_url, timeout=30, headers=headers, allow_redirects=True)
    img.raise_for_status()
    content_type = img.headers.get("content-type", "unknown")
    if content_type != "unknown" and not content_type.startswith("image/") and "octet-stream" not in content_type:
        raise RuntimeError(f"Downloaded URL was not an image: {content_type}")
    if len(img.content) < 100:
        raise RuntimeError("Downloaded image was unexpectedly small.")
    target.write_bytes(img.content)
    return target, content_type


def score_downloaded_image(image_path: Path) -> float:
    from PIL import Image, ImageOps, ImageStat

    try:
        usable_path = rasterize_if_needed(image_path)
        img = Image.open(usable_path).convert("RGBA")
        background = Image.new("RGBA", img.size, "WHITE")
        background.alpha_composite(img)
        rgb = background.convert("RGB")
        gray = ImageOps.grayscale(rgb)
        gray.thumbnail((256, 256), Image.Resampling.LANCZOS)
        rgb.thumbnail((256, 256), Image.Resampling.LANCZOS)

        pixels = list(gray.getdata())
        total = max(1, len(pixels))
        dark_ratio = sum(1 for value in pixels if value < 120) / total
        mid_ratio = sum(1 for value in pixels if 120 <= value <= 220) / total
        light_ratio = sum(1 for value in pixels if value > 235) / total
        stat = ImageStat.Stat(gray)
        contrast = stat.stddev[0]

        color_stat = ImageStat.Stat(rgb)
        color_spread = sum(color_stat.stddev) / 3

        score = 0.0
        score += min(contrast, 80) * 1.4
        score += light_ratio * 55
        score -= mid_ratio * 30
        score -= color_spread * 0.25

        if 0.006 <= dark_ratio <= 0.22:
            score += 95
        elif 0.22 < dark_ratio <= 0.38:
            score += 35
        else:
            score -= 80

        aspect = max(gray.width / max(1, gray.height), gray.height / max(1, gray.width))
        if aspect > 3:
            score -= 50

        return score
    except Exception:
        return -200.0


def search_openverse_images(session, base_query: str, terms: list[str]) -> list[tuple[int, str, str, str]]:
    queries = [
        f"{base_query} line art",
        f"{base_query} outline drawing",
        f"{base_query} black white drawing",
        f"{base_query} clipart",
        f"{base_query} silhouette",
        f"{base_query} icon",
        base_query,
    ]
    if "poop" in base_query.lower() or "poo" in base_query.lower():
        queries.extend([
            "poop icon",
            "pile of poo emoji",
            "poop emoji",
        ])
    candidates: list[tuple[int, str, str, str]] = []
    seen_urls: set[str] = set()

    for query_index, query in enumerate(queries):
        response = session.get(
            "https://api.openverse.org/v1/images/",
            params={"q": query, "page_size": 15},
            timeout=20,
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            print(f"Openverse search skipped for {query!r}: {exc}")
            continue
        for result in response.json().get("results", []):
            title = result.get("title") or "Openverse image"
            mime = result.get("mimetype") or ""
            for url_key, score_offset in (("url", 0), ("thumbnail", -8), ("foreign_landing_url", -35)):
                url = result.get(url_key)
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                if url_key == "foreign_landing_url":
                    title_for_score = f"{title} landing"
                else:
                    title_for_score = title
                score = score_line_art_candidate(title_for_score, url, mime, query_index, terms) + score_offset
                candidates.append((score, title, url, mime))
    return candidates


def search_ddg_images(base_query: str, terms: list[str]) -> list[tuple[int, str, str, str]]:
    from ddgs import DDGS

    queries = [
        f"{base_query} line art",
        f"{base_query} outline drawing",
        f"{base_query} black white drawing",
        f"{base_query} clipart",
        f"{base_query} icon",
        base_query,
    ]
    candidates: list[tuple[int, str, str, str]] = []
    seen_urls: set[str] = set()

    for query_index, query in enumerate(queries):
        try:
            results = DDGS().images(query, max_results=20, safesearch="off")
        except Exception as exc:
            print(f"DuckDuckGo image search skipped for {query!r}: {exc}")
            continue
        for result in results:
            title = result.get("title") or "DuckDuckGo image"
            for key, score_offset in (("image", 0), ("thumbnail", -10)):
                url = result.get(key)
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                score = score_line_art_candidate(title, url, "", query_index, terms) + score_offset
                candidates.append((score, title, url, ""))
    return candidates


def search_google_custom_images(session, base_query: str, terms: list[str]) -> list[tuple[int, str, str, str]]:
    api_key = os.environ.get("GOOGLE_API_KEY")
    cx = os.environ.get("GOOGLE_CSE_ID")
    if not api_key or not cx:
        return []

    queries = [
        f"{base_query} line art",
        f"{base_query} outline drawing",
        f"{base_query} black white drawing",
        base_query,
    ]
    candidates: list[tuple[int, str, str, str]] = []
    seen_urls: set[str] = set()

    for query_index, query in enumerate(queries):
        try:
            response = session.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": api_key,
                    "cx": cx,
                    "q": query,
                    "searchType": "image",
                    "num": 10,
                    "safe": "off",
                },
                timeout=20,
            )
            response.raise_for_status()
        except Exception as exc:
            print(f"Google Custom Search skipped for {query!r}: {exc}")
            continue
        for item in response.json().get("items", []):
            url = item.get("link")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            title = item.get("title") or "Google image"
            mime = item.get("mime") or ""
            score = score_line_art_candidate(title, url, mime, query_index, terms) + 12
            candidates.append((score, title, url, mime))
    return candidates


def search_commons_images(session, base_query: str, terms: list[str]) -> list[tuple[int, str, str, str]]:
    queries = [
        f"{base_query} line art",
        f"{base_query} outline drawing",
        f"{base_query} black white line drawing",
        f"{base_query} clip art svg",
        f"{base_query} silhouette",
        base_query,
    ]
    search_url = "https://commons.wikimedia.org/w/api.php"

    candidates: list[tuple[int, str, str, str]] = []
    seen_urls: set[str] = set()
    for query_index, query in enumerate(queries):
        search_params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": "6",
            "gsrlimit": "12",
            "prop": "imageinfo",
            "iiprop": "url|mime",
            "iiurlwidth": "1000",
        }
        response = session.get(search_url, params=search_params, timeout=20)
        try:
            response.raise_for_status()
        except Exception as exc:
            print(f"Commons search skipped for {query!r}: {exc}")
            if response.status_code == 429:
                break
            continue
        pages = response.json().get("query", {}).get("pages", {})

        for page in pages.values():
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            mime = info.get("mime", "")
            url = info.get("thumburl") or info.get("url")
            if not url or not mime.startswith("image/") or url in seen_urls:
                continue
            if any(bad in mime.lower() for bad in ("djvu", "pdf")):
                continue
            seen_urls.add(url)
            title = page.get("title", "image")
            candidates.append((score_line_art_candidate(title, url, mime, query_index, terms), title, url, mime))
    return candidates


def score_line_art_candidate(title: str, url: str, mime: str, query_index: int, terms: list[str]) -> int:
    haystack = f"{title} {url}".lower()
    score = 100 - query_index * 10
    boosts = {
        "line": 35,
        "outline": 35,
        "drawing": 30,
        "diagram": 18,
        "clip": 18,
        "icon": 14,
        "svg": 25,
        "silhouette": 14,
        "black": 10,
        "white": 10,
        "coloring": 24,
        "contour": 20,
    }
    penalties = {
        "photo": -45,
        "photograph": -45,
        "player": -35,
        "map": -25,
        "logo": -20,
        "coat_of_arms": -20,
        "flag": -25,
        "djvu": -140,
        "volume": -90,
        "magazine": -90,
        "newspaper": -90,
        "archive": -70,
        "page1": -70,
        "scan": -70,
        "texaco": -90,
    }
    for word, value in boosts.items():
        if word in haystack:
            score += value
    for word, value in penalties.items():
        if word in haystack:
            score += value
    if mime.endswith("svg+xml"):
        score += 35
    if terms:
        matched = 0
        for term in terms:
            if term in haystack or term.rstrip("s") in haystack:
                matched += 1
        score += matched * 55
        if matched == 0:
            score -= 160
    return score


def download_image_url(url: str, out_dir: Path) -> Path:
    import requests

    response = requests.get(url, headers={"User-Agent": "sts2-drawbot/1.0"}, timeout=30)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if content_type and not content_type.startswith("image/"):
        raise RuntimeError(f"URL did not return an image content type: {content_type}")

    parsed = urllib.parse.urlparse(url)
    ext = Path(parsed.path).suffix or ".jpg"
    target = out_dir / f"{slugify(Path(parsed.path).stem or 'url-image')}{ext}"
    target.write_bytes(response.content)
    print(f"Downloaded URL: {url}")
    return target


def generate_builtin_prompt_image(prompt: str, out_dir: Path) -> Path | None:
    from PIL import Image, ImageDraw

    words = set(re.findall(r"[a-z0-9]+", prompt.lower()))
    shape = None
    if "sailor" in words and "moon" in words:
        shape = "sailor-moon"
    for candidate in ("star", "heart", "circle", "triangle", "square", "arrow", "smiley", "smile", "poop", "poo"):
        if shape is not None:
            break
        if candidate in words:
            shape = "smiley" if candidate == "smile" else candidate
            shape = "poop" if candidate == "poo" else shape
            break
    if shape is None:
        return None

    size = 900
    img = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(img)
    line = 24
    vector_strokes: list[list[tuple[float, float]]] = []

    if shape == "star":
        center = (size / 2, size / 2)
        outer = size * 0.34
        inner = size * 0.14
        points = []
        for i in range(10):
            angle = -math.pi / 2 + i * math.pi / 5
            radius = outer if i % 2 == 0 else inner
            points.append((center[0] + math.cos(angle) * radius, center[1] + math.sin(angle) * radius))
        vector_strokes.append(points + [points[0]])
        vector_strokes.append([(450, 260), (450, 640)])
        vector_strokes.append([(285, 405), (615, 405)])
        draw.line(vector_strokes[-1], fill="black", width=line, joint="curve")
        draw.line(vector_strokes[-2], fill="black", width=line, joint="curve")
        draw.line(vector_strokes[-3], fill="black", width=line, joint="curve")
    elif shape == "heart":
        pts = []
        for i in range(240):
            t = 2 * math.pi * i / 239
            x = 16 * math.sin(t) ** 3
            y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
            pts.append((size / 2 + x * 18, size / 2 - y * 18 + 40))
        vector_strokes.append(pts)
        draw.line(vector_strokes[-1], fill="black", width=line, joint="curve")
    elif shape == "circle":
        vector_strokes.append(arc_points((220, 220, 680, 680), 0, 360, 120))
        draw.line(vector_strokes[-1], fill="black", width=line, joint="curve")
    elif shape == "triangle":
        pts = [(450, 180), (180, 690), (720, 690), (450, 180)]
        vector_strokes.append(pts)
        draw.line(vector_strokes[-1], fill="black", width=line, joint="curve")
    elif shape == "square":
        vector_strokes.append([(220, 220), (680, 220), (680, 680), (220, 680), (220, 220)])
        draw.line(vector_strokes[-1], fill="black", width=line, joint="curve")
    elif shape == "arrow":
        vector_strokes.extend([
            [(180, 450), (690, 450)],
            [(690, 450), (510, 280)],
            [(690, 450), (510, 620)],
        ])
        for stroke in vector_strokes:
            draw.line(stroke, fill="black", width=line, joint="curve")
    elif shape == "smiley":
        vector_strokes.extend([
            arc_points((190, 190, 710, 710), 0, 360, 130),
            arc_points((335, 350, 380, 395), 0, 360, 35),
            arc_points((520, 350, 565, 395), 0, 360, 35),
            arc_points((320, 330, 580, 610), 25, 155, 50),
        ])
        for stroke in vector_strokes:
            draw.line(stroke, fill="black", width=line, joint="curve")
    elif shape == "poop":
        vector_strokes.extend([
            arc_points((230, 510, 670, 810), 180, 360, 80),
            arc_points((170, 420, 440, 700), 130, 310, 70),
            arc_points((345, 405, 730, 700), 220, 415, 80),
            arc_points((250, 295, 610, 585), 155, 385, 90),
            arc_points((340, 190, 560, 425), 115, 380, 90),
            [(450, 195), (500, 285)],
            arc_points((320, 465, 395, 545), 0, 360, 45),
            arc_points((505, 465, 580, 545), 0, 360, 45),
            arc_points((365, 510, 535, 655), 20, 160, 45),
            arc_points((258, 548, 322, 610), 200, 340, 28),
            arc_points((578, 548, 642, 610), 200, 340, 28),
            arc_points((390, 250, 520, 365), 205, 330, 32),
        ])
        for stroke in vector_strokes:
            draw.line(stroke, fill="black", width=line, joint="curve")
    elif shape == "sailor-moon":
        vector_strokes.extend([
            arc_points((250, 190, 650, 590), 25, 335, 120),
            arc_points((285, 205, 615, 520), 200, 340, 80),
            arc_points((290, 145, 610, 310), 20, 160, 65),
            arc_points((325, 305, 395, 375), 0, 360, 40),
            arc_points((505, 305, 575, 375), 0, 360, 40),
            arc_points((338, 318, 382, 362), 0, 360, 24),
            arc_points((518, 318, 562, 362), 0, 360, 24),
            [(382, 382), (450, 410), (518, 382)],
            arc_points((350, 390, 550, 520), 25, 155, 55),
            arc_points((185, 165, 355, 335), 0, 360, 60),
            arc_points((545, 165, 715, 335), 0, 360, 60),
            arc_points((220, 205, 320, 305), 210, 25, 34),
            arc_points((580, 205, 680, 305), 155, 330, 34),
            [(270, 300), (170, 230), (120, 300), (200, 360)],
            [(630, 300), (730, 230), (780, 300), (700, 360)],
            arc_points((365, 120, 535, 360), 235, 305, 45),
            [(370, 255), (450, 225), (530, 255)],
            arc_points((405, 205, 495, 285), 205, 335, 34),
            [(450, 560), (390, 725), (510, 725), (450, 560)],
            [(388, 590), (512, 590)],
            [(330, 650), (570, 650)],
            [(330, 650), (270, 735)],
            [(570, 650), (630, 735)],
            [(405, 725), (370, 810)],
            [(495, 725), (530, 810)],
        ])
        for stroke in vector_strokes:
            draw.line(stroke, fill="black", width=line, joint="curve")

    target = out_dir / f"{slugify(prompt)}-builtin.png"
    img.save(target)
    if vector_strokes:
        sidecar = target.with_suffix(".strokes.json")
        sidecar.write_text(json.dumps({"size": [size, size], "strokes": vector_strokes}), encoding="utf-8")
    print(f"Generated built-in {shape} line drawing.")
    return target


def arc_points(bbox: tuple[int, int, int, int], start: float, end: float, steps: int) -> list[tuple[float, float]]:
    left, top, right, bottom = bbox
    cx = (left + right) / 2
    cy = (top + bottom) / 2
    rx = (right - left) / 2
    ry = (bottom - top) / 2
    if end < start:
        end += 360
    points = []
    for i in range(steps):
        angle = math.radians(start + (end - start) * i / max(1, steps - 1))
        points.append((cx + math.cos(angle) * rx, cy + math.sin(angle) * ry))
    return points


def load_sidecar_strokes(image_path: Path) -> tuple[list[Stroke], tuple[int, int]] | None:
    sidecar = image_path.with_suffix(".strokes.json")
    if not sidecar.exists():
        return None
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    width, height = data["size"]
    strokes = [
        [Point(int(round(x)), int(round(y))) for x, y in stroke]
        for stroke in data["strokes"]
        if len(stroke) >= 2
    ]
    print(f"Loaded exact built-in vector strokes: {len(strokes)}")
    return strokes, (int(width), int(height))


def rasterize_if_needed(image_path: Path) -> Path:
    if image_path.suffix.lower() != ".svg":
        return image_path

    try:
        import cairosvg
    except Exception as exc:
        raise RuntimeError(
            "SVG images require the optional CairoSVG/native Cairo stack. "
            "Try another candidate, or install CairoSVG with its platform Cairo dependency."
        ) from exc

    raster_path = image_path.with_suffix(".png")
    cairosvg.svg2png(url=str(image_path), write_to=str(raster_path), output_width=1000, output_height=1000)
    return raster_path


def parse_area(value: str) -> Rect:
    parts = [int(p.strip()) for p in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--area must be X,Y,W,H")
    if parts[2] <= 0 or parts[3] <= 0:
        raise argparse.ArgumentTypeError("--area width and height must be positive")
    return Rect(*parts)


def find_game_window(process_name: str, title_part: str) -> Rect:
    if not is_windows():
        raise RuntimeError("Slay the Spire 2 window targeting is currently supported on Windows only.")
    import psutil
    import win32gui
    import win32process

    process_name = process_name.lower()
    title_part = title_part.lower()
    matches: list[tuple[int, str, Rect]] = []

    def enum(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            exe = (psutil.Process(pid).name() or "").lower()
        except Exception:
            exe = ""
        if exe == process_name or title_part in title.lower():
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if right > left and bottom > top:
                matches.append((hwnd, title, Rect(left, top, right - left, bottom - top)))

    win32gui.EnumWindows(enum, None)
    if not matches:
        raise RuntimeError(f"Could not find window for {process_name!r} or title containing {title_part!r}.")

    hwnd, title, rect = matches[0]
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    print(f"Target window: {title} at {rect.x},{rect.y} {rect.w}x{rect.h}")
    return rect


def default_canvas_for_window(window: Rect) -> Rect:
    left = int(window.w * 0.16)
    top = int(window.h * 0.145)
    right = int(window.w * 0.81)
    bottom = int(window.h * 0.96)
    return Rect(
        window.x + left,
        window.y + top,
        max(1, right - left),
        max(1, bottom - top),
    )


def default_canvas_for_screen() -> Rect:
    import pyautogui

    width, height = pyautogui.size()
    margin_x = max(20, int(width * 0.08))
    margin_y = max(20, int(height * 0.08))
    return Rect(
        margin_x,
        margin_y,
        max(1, width - margin_x * 2),
        max(1, height - margin_y * 2),
    )


def image_to_strokes(
    image_path: Path,
    threshold: int,
    fit_size: int,
    max_strokes: int,
    mode: str,
    connect_gap: int,
) -> tuple[list[Stroke], tuple[int, int]]:
    from PIL import Image, ImageFilter, ImageOps

    image_path = rasterize_if_needed(image_path)
    img = Image.open(image_path).convert("RGBA")
    background = Image.new("RGBA", img.size, "WHITE")
    background.alpha_composite(img)
    img = background.convert("L")
    img = crop_dark_frame(img)
    img.thumbnail((fit_size, fit_size), Image.Resampling.LANCZOS)

    if mode == "edges":
        working = ImageOps.autocontrast(img).filter(ImageFilter.FIND_EDGES)
        working = ImageOps.autocontrast(working)
        is_ink = lambda value: value >= threshold
        trace_kind = "edges"
    elif mode == "sketch":
        working, threshold = build_sketch_edge_image(img, threshold)
        is_ink = lambda value: value >= threshold
        trace_kind = "sketch"
        print(f"Trace mode: sketch edges, threshold {threshold}")
    elif mode == "dark":
        working, threshold = build_dark_line_image(img, threshold)
        is_ink = lambda value: value <= threshold
        trace_kind = "dark"
        print(f"Trace mode: dark lines, threshold {threshold}")
    else:
        working, dark_threshold = build_dark_line_image(img, threshold)
        working_values = image_values(working)
        dark_count = sum(1 for value in working_values if value <= dark_threshold)
        dark_ratio = dark_count / max(1, img.width * img.height)
        mid_count = sum(1 for value in working_values if dark_threshold < value < 235)
        mid_ratio = mid_count / max(1, img.width * img.height)
        if 0.002 <= dark_ratio <= 0.22 and mid_ratio < 0.22:
            threshold = dark_threshold
            is_ink = lambda value: value <= threshold
            trace_kind = "dark"
            print(f"Trace mode: dark lines, threshold {threshold}")
        else:
            working, threshold = build_sketch_edge_image(img, threshold)
            is_ink = lambda value: value >= threshold
            trace_kind = "sketch"
            print(f"Trace mode: sketch edges, threshold {threshold}")

    width, height = working.size
    pixels = working.load()
    remaining = {(x, y) for y in range(height) for x in range(width) if is_ink(pixels[x, y])}
    if trace_kind in {"edges", "sketch"}:
        remaining = {
            (x, y)
            for x, y in remaining
            if 2 < x < width - 3 and 2 < y < height - 3
        }
    if trace_kind == "dark":
        before = len(remaining)
        remaining = close_binary_pixels(remaining, width, height)
        remaining = thin_binary_pixels(remaining, width, height)
        print(f"Closed/thinned dark lines: {before} pixels -> {len(remaining)} pixels")
    strokes: list[Stroke] = []
    min_points = max(3, fit_size // 165)
    if trace_kind == "sketch":
        simplify_distance = max(2.7, fit_size / 205)
    else:
        simplify_distance = max(1.0, fit_size / 360)

    while remaining:
        component = pop_component(remaining, connect_gap)
        if len(component) < min_points:
            continue
        if is_noise_component(component, width, height) and not (trace_kind == "sketch" and len(component) > width * height * 0.01):
            continue

        for raw in order_component_paths(component, connect_gap):
            if len(raw) < min_points:
                continue
            simplified = simplify_path(raw, simplify_distance)
            if trace_kind == "sketch":
                simplified = smooth_stroke(simplified, passes=2)
            elif trace_kind == "edges":
                simplified = smooth_stroke(simplified, passes=1)
            elif trace_kind == "dark":
                simplified = smooth_stroke(simplified, passes=1)
            if len(simplified) >= 2:
                strokes.append([Point(x, y) for x, y in simplified])

    strokes.sort(key=lambda s: (s[0].y, s[0].x))
    strokes = stitch_nearby_strokes(strokes, max_gap=max(5, fit_size // 42), max_passes=3)
    if len(strokes) > max_strokes:
        step = len(strokes) / max_strokes
        strokes = [strokes[int(i * step)] for i in range(max_strokes)]

    return strokes, (width, height)


def crop_dark_frame(img):
    width, height = img.size
    pixels = img.load()

    def dark_row(y: int) -> float:
        return sum(1 for x in range(width) if pixels[x, y] < 45) / max(1, width)

    def dark_col(x: int) -> float:
        return sum(1 for y in range(height) if pixels[x, y] < 45) / max(1, height)

    left = 0
    right = width - 1
    top = 0
    bottom = height - 1
    max_x_trim = max(2, int(width * 0.04))
    max_y_trim = max(2, int(height * 0.04))

    while left < max_x_trim and dark_col(left) > 0.55:
        left += 1
    while right > width - max_x_trim - 1 and dark_col(right) > 0.55:
        right -= 1
    while top < max_y_trim and dark_row(top) > 0.55:
        top += 1
    while bottom > height - max_y_trim - 1 and dark_row(bottom) > 0.55:
        bottom -= 1

    if left > 0 or top > 0 or right < width - 1 or bottom < height - 1:
        return img.crop((left, top, right + 1, bottom + 1))
    return img


def build_dark_line_image(img, fallback_threshold: int):
    from PIL import ImageFilter, ImageOps

    working = ImageOps.autocontrast(img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=130, threshold=3)))
    threshold = adaptive_dark_threshold(working, fallback_threshold)
    return working, threshold


def adaptive_dark_threshold(img, fallback_threshold: int) -> int:
    values = image_values(img)
    if not values:
        return fallback_threshold

    light_ratio = sum(1 for value in values if value >= 238) / len(values)
    very_dark_ratio = sum(1 for value in values if value <= fallback_threshold) / len(values)
    if light_ratio < 0.55 or not (0.002 <= very_dark_ratio <= 0.18):
        return fallback_threshold

    otsu = otsu_threshold(values)
    # Line drawings often contain gray antialias pixels around black ink. Keeping a bit above
    # Otsu recovers those hairline details before thinning collapses the stroke back to center.
    boosted = otsu + 18
    return max(fallback_threshold, min(195, boosted))


def otsu_threshold(values: list[int]) -> int:
    histogram = [0] * 256
    for value in values:
        histogram[max(0, min(255, int(value)))] += 1

    total = len(values)
    weighted_total = sum(index * count for index, count in enumerate(histogram))
    background_weight = 0
    background_sum = 0
    best_threshold = 0
    best_variance = -1.0

    for threshold, count in enumerate(histogram):
        background_weight += count
        if background_weight == 0:
            continue
        foreground_weight = total - background_weight
        if foreground_weight == 0:
            break
        background_sum += threshold * count
        background_mean = background_sum / background_weight
        foreground_mean = (weighted_total - background_sum) / foreground_weight
        variance = background_weight * foreground_weight * (background_mean - foreground_mean) ** 2
        if variance > best_variance:
            best_variance = variance
            best_threshold = threshold

    return best_threshold


def build_sketch_edge_image(img, fallback_threshold: int):
    from PIL import ImageChops, ImageFilter, ImageOps

    smoothed = ImageOps.autocontrast(img).filter(ImageFilter.GaussianBlur(radius=1.15))
    tonal = smoothed.point(lambda value: int(round(value / 32) * 32))
    edges = ImageChops.lighter(
        smoothed.filter(ImageFilter.FIND_EDGES),
        tonal.filter(ImageFilter.FIND_EDGES),
    )
    edges = ImageOps.autocontrast(edges.filter(ImageFilter.SMOOTH_MORE))
    values = sorted(image_values(edges))
    if not values:
        return edges, fallback_threshold
    # Keep the strongest edge pixels. This behaves better on shaded portraits than a fixed threshold.
    index = int(len(values) * 0.855)
    threshold = max(54, min(180, values[index]))
    return edges, threshold


def image_values(img) -> list[int]:
    data_getter = getattr(img, "get_flattened_data", None)
    if data_getter:
        return list(data_getter())
    return list(img.getdata())


def smooth_stroke(points: list[tuple[int, int]], passes: int = 1) -> list[tuple[int, int]]:
    if len(points) < 4:
        return points
    smoothed: list[tuple[float, float]] = [(float(x), float(y)) for x, y in points]
    for _ in range(max(1, passes)):
        next_points: list[tuple[float, float]] = [smoothed[0]]
        for a, b in zip(smoothed, smoothed[1:]):
            q = (a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25)
            r = (a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75)
            next_points.extend([q, r])
        next_points.append(smoothed[-1])
        smoothed = next_points
    return smoothed


def is_noise_component(component: set[tuple[int, int]], width: int, height: int) -> bool:
    min_x = min(x for x, _ in component)
    max_x = max(x for x, _ in component)
    min_y = min(y for _, y in component)
    max_y = max(y for _, y in component)
    box_w = max_x - min_x + 1
    box_h = max_y - min_y + 1
    count = len(component)

    if count < 12:
        return True
    top_edge = sum(1 for x, y in component if y <= 1)
    bottom_edge = sum(1 for x, y in component if y >= height - 2)
    left_edge = sum(1 for x, y in component if x <= 1)
    right_edge = sum(1 for x, y in component if x >= width - 2)
    if (
        top_edge > width * 0.35
        or bottom_edge > width * 0.35
        or left_edge > height * 0.35
        or right_edge > height * 0.35
    ):
        return True
    if min_y > height * 0.88 and box_h < height * 0.08:
        return True
    if min_x > width * 0.72 and min_y > height * 0.78 and box_h < height * 0.12:
        return True
    if box_w < width * 0.015 and box_h < height * 0.015:
        return True
    return False


def stitch_nearby_strokes(strokes: list[Stroke], max_gap: int, max_passes: int) -> list[Stroke]:
    if not strokes:
        return strokes

    stitched = [list(stroke) for stroke in strokes]
    for _ in range(max_passes):
        changed = False
        used = [False] * len(stitched)
        merged: list[Stroke] = []

        for index, stroke in enumerate(stitched):
            if used[index] or len(stroke) < 2:
                continue
            current = list(stroke)
            used[index] = True

            while True:
                best_index = None
                best_reverse = False
                best_distance = max_gap * max_gap + 1
                for other_index, other in enumerate(stitched):
                    if used[other_index] or len(other) < 2:
                        continue
                    start_distance = point_distance_sq(current[-1], other[0])
                    end_distance = point_distance_sq(current[-1], other[-1])
                    if start_distance < best_distance and continuation_is_reasonable(current, other):
                        best_index = other_index
                        best_reverse = False
                        best_distance = start_distance
                    if end_distance < best_distance and continuation_is_reasonable(current, list(reversed(other))):
                        best_index = other_index
                        best_reverse = True
                        best_distance = end_distance

                if best_index is None:
                    break

                next_stroke = list(reversed(stitched[best_index])) if best_reverse else stitched[best_index]
                current.extend(next_stroke)
                used[best_index] = True
                changed = True

            merged.append(current)

        for index, stroke in enumerate(stitched):
            if not used[index] and len(stroke) >= 2:
                merged.append(stroke)

        stitched = merged
        if not changed:
            break

    return stitched


def continuation_is_reasonable(first: Stroke, second: Stroke) -> bool:
    if len(first) < 2 or len(second) < 2:
        return True
    gap = point_distance_sq(first[-1], second[0])
    if gap <= 4:
        return True
    incoming = (first[-1].x - first[-2].x, first[-1].y - first[-2].y)
    outgoing = (second[1].x - second[0].x, second[1].y - second[0].y)
    dot = incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
    return dot >= -2


def point_distance_sq(a: Point, b: Point) -> int:
    dx = a.x - b.x
    dy = a.y - b.y
    return dx * dx + dy * dy


def neighbor_offsets(radius: int) -> list[tuple[int, int]]:
    offsets: list[tuple[int, int]] = []
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            if dx * dx + dy * dy <= radius * radius:
                offsets.append((dx, dy))
    offsets.sort(key=lambda p: (p[0] * p[0] + p[1] * p[1], p[1], p[0]))
    return offsets


def thin_binary_pixels(pixels: set[tuple[int, int]], width: int, height: int) -> set[tuple[int, int]]:
    # Zhang-Suen thinning turns thick black strokes into one-pixel center lines.
    pixels = set(pixels)
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            to_remove: list[tuple[int, int]] = []
            for x, y in pixels:
                if x <= 0 or y <= 0 or x >= width - 1 or y >= height - 1:
                    continue
                n = zhang_suen_neighbors(pixels, x, y)
                black_neighbors = sum(n)
                if black_neighbors < 2 or black_neighbors > 6:
                    continue
                transitions = sum((not n[i] and n[(i + 1) % 8]) for i in range(8))
                if transitions != 1:
                    continue
                p2, p4, p6, p8 = n[0], n[2], n[4], n[6]
                if step == 0:
                    if p2 and p4 and p6:
                        continue
                    if p4 and p6 and p8:
                        continue
                else:
                    if p2 and p4 and p8:
                        continue
                    if p2 and p6 and p8:
                        continue
                to_remove.append((x, y))
            if to_remove:
                pixels.difference_update(to_remove)
                changed = True
    return pixels


def close_binary_pixels(pixels: set[tuple[int, int]], width: int, height: int) -> set[tuple[int, int]]:
    dilated = set(pixels)
    for x, y in pixels:
        for dx, dy in neighbor_offsets(1):
            nx = x + dx
            ny = y + dy
            if 0 <= nx < width and 0 <= ny < height:
                dilated.add((nx, ny))

    eroded: set[tuple[int, int]] = set()
    for x, y in dilated:
        keep = True
        for dx, dy in neighbor_offsets(1):
            nx = x + dx
            ny = y + dy
            if not (0 <= nx < width and 0 <= ny < height and (nx, ny) in dilated):
                keep = False
                break
        if keep:
            eroded.add((x, y))
    return eroded


def zhang_suen_neighbors(pixels: set[tuple[int, int]], x: int, y: int) -> list[bool]:
    return [
        (x, y - 1) in pixels,
        (x + 1, y - 1) in pixels,
        (x + 1, y) in pixels,
        (x + 1, y + 1) in pixels,
        (x, y + 1) in pixels,
        (x - 1, y + 1) in pixels,
        (x - 1, y) in pixels,
        (x - 1, y - 1) in pixels,
    ]


def pop_component(remaining: set[tuple[int, int]], radius: int) -> set[tuple[int, int]]:
    start = remaining.pop()
    component = {start}
    stack = [start]
    offsets = neighbor_offsets(radius)
    while stack:
        x, y = stack.pop()
        for dx, dy in offsets:
            candidate = (x + dx, y + dy)
            if candidate in remaining:
                remaining.remove(candidate)
                component.add(candidate)
                stack.append(candidate)
    return component


def order_component_paths(component: set[tuple[int, int]], radius: int) -> list[list[tuple[int, int]]]:
    offsets = neighbor_offsets(1)
    adjacency: dict[tuple[int, int], set[tuple[int, int]]] = {}
    for x, y in component:
        point = (x, y)
        adjacency[point] = {
            (x + dx, y + dy)
            for dx, dy in offsets
            if (x + dx, y + dy) in component
        }

    visited_edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    paths: list[list[tuple[int, int]]] = []

    endpoints = [point for point, neighbors in adjacency.items() if len(neighbors) <= 1]
    starts = sorted(endpoints, key=lambda p: (p[1], p[0]))
    starts.extend(p for p in sorted(component, key=lambda p: (p[1], p[0])) if p not in endpoints)

    for start in starts:
        for next_point in sorted(adjacency[start], key=lambda p: (p[1], p[0])):
            edge = normalized_edge(start, next_point)
            if edge in visited_edges:
                continue
            path, used = trace_continuous_graph_path(start, next_point, adjacency, visited_edges)
            visited_edges.update(used)
            if len(path) >= 2:
                paths.append(path)

    return paths


def trace_continuous_graph_path(
    start: tuple[int, int],
    next_point: tuple[int, int],
    adjacency: dict[tuple[int, int], set[tuple[int, int]]],
    already_visited: set[tuple[tuple[int, int], tuple[int, int]]],
) -> tuple[list[tuple[int, int]], set[tuple[tuple[int, int], tuple[int, int]]]]:
    path = [start, next_point]
    used = {normalized_edge(start, next_point)}
    previous = start
    current = next_point

    while True:
        candidates = [
            p for p in adjacency[current]
            if p != previous
            and normalized_edge(current, p) not in used
            and normalized_edge(current, p) not in already_visited
        ]
        if not candidates:
            break
        following = min(candidates, key=lambda p: turn_cost(previous, current, p))
        edge = normalized_edge(current, following)
        used.add(edge)
        path.append(following)
        previous, current = current, following

    return path, used


def normalized_edge(a: tuple[int, int], b: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
    return (a, b) if a <= b else (b, a)


def turn_cost(previous: tuple[int, int], current: tuple[int, int], following: tuple[int, int]) -> float:
    ax = current[0] - previous[0]
    ay = current[1] - previous[1]
    bx = following[0] - current[0]
    by = following[1] - current[1]
    return abs(ax * by - ay * bx) - 0.01 * (ax * bx + ay * by)


def simplify_path(points: list[tuple[int, int]], min_distance: float) -> list[tuple[int, int]]:
    if len(points) <= 2:
        return points
    simplified = rdp_simplify(points, min_distance)
    return simplified if len(simplified) >= 2 else [points[0], points[-1]]


def rdp_simplify(points: list[tuple[int, int]], epsilon: float) -> list[tuple[int, int]]:
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]

    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue

        a = points[start]
        b = points[end]
        farthest_index = None
        farthest_distance = -1.0
        for index in range(start + 1, end):
            distance = perpendicular_distance(points[index], a, b)
            if distance > farthest_distance:
                farthest_distance = distance
                farthest_index = index

        if farthest_index is not None and farthest_distance > epsilon:
            keep.add(farthest_index)
            stack.append((start, farthest_index))
            stack.append((farthest_index, end))

    return [points[index] for index in sorted(keep)]


def perpendicular_distance(point: tuple[int, int], start: tuple[int, int], end: tuple[int, int]) -> float:
    if start == end:
        return math.sqrt(distance_sq(point, start))
    x, y = point
    x1, y1 = start
    x2, y2 = end
    numerator = abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1)
    denominator = math.hypot(y2 - y1, x2 - x1)
    return numerator / denominator


def distance_sq(a: tuple[int, int], b: tuple[int, int]) -> int:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def save_preview(strokes: Iterable[Stroke], source_size: tuple[int, int], target: Path) -> None:
    from PIL import Image, ImageDraw

    scale = 2
    width, height = source_size
    img = Image.new("RGB", (width * scale, height * scale), "white")
    draw = ImageDraw.Draw(img)
    for stroke in strokes:
        if len(stroke) < 2:
            continue
        pts = [(p.x * scale, p.y * scale) for p in stroke]
        draw.line(pts, fill="black", width=1)
    img.save(target)


def first_draw_point(strokes: Iterable[Stroke]) -> Point | None:
    for stroke in strokes:
        if len(stroke) >= 2:
            return stroke[0]
    return None


def stroke_bounds(strokes: Iterable[Stroke]) -> Rect:
    points = [point for stroke in strokes for point in stroke]
    if not points:
        return Rect(0, 0, 1, 1)
    min_x = min(point.x for point in points)
    min_y = min(point.y for point in points)
    max_x = max(point.x for point in points)
    max_y = max(point.y for point in points)
    return Rect(min_x, min_y, max(1, max_x - min_x), max(1, max_y - min_y))


def inset_rect(rect: Rect, padding: int) -> Rect:
    padding = max(0, min(padding, rect.w // 3, rect.h // 3))
    return Rect(rect.x + padding, rect.y + padding, max(1, rect.w - padding * 2), max(1, rect.h - padding * 2))


def clamp(value: float, low: float, high: float) -> float:
    if high < low:
        return (low + high) / 2
    return max(low, min(high, value))


def build_draw_transform(
    strokes: list[Stroke],
    canvas: Rect,
    anchor_target: Point | None,
    fit_padding: int,
    draw_scale: float,
) -> DrawTransform:
    bounds = stroke_bounds(strokes)
    fit = inset_rect(canvas, fit_padding)
    draw_scale = clamp(draw_scale, 0.1, 1.0)
    scale = min(fit.w / bounds.w, fit.h / bounds.h) * draw_scale

    if anchor_target:
        anchor_source = first_draw_point(strokes) or Point(bounds.x, bounds.y)
        offset_x = anchor_target.x - anchor_source.x * scale
        offset_y = anchor_target.y - anchor_source.y * scale
    else:
        offset_x = fit.x + (fit.w - bounds.w * scale) / 2 - bounds.x * scale
        offset_y = fit.y + (fit.h - bounds.h * scale) / 2 - bounds.y * scale

    min_offset_x = fit.x - bounds.x * scale
    max_offset_x = fit.x + fit.w - (bounds.x + bounds.w) * scale
    min_offset_y = fit.y - bounds.y * scale
    max_offset_y = fit.y + fit.h - (bounds.y + bounds.h) * scale
    offset_x = clamp(offset_x, min_offset_x, max_offset_x)
    offset_y = clamp(offset_y, min_offset_y, max_offset_y)

    print(f"Fit area: {fit.x},{fit.y} {fit.w}x{fit.h}")
    print(f"Drawing bounds: {bounds.w}x{bounds.h}, scale: {scale:.2f} ({draw_scale:.0%} fit)")
    return DrawTransform(scale=scale, offset_x=offset_x, offset_y=offset_y)


def map_point(point: Point, transform: DrawTransform) -> Point:
    return Point(
        int(round(transform.offset_x + point.x * transform.scale)),
        int(round(transform.offset_y + point.y * transform.scale)),
    )


def map_stroke(stroke: Stroke, transform: DrawTransform) -> list[Point]:
    mapped: list[Point] = []
    last: Point | None = None
    for point in stroke:
        current = map_point(point, transform)
        if last is None or current != last:
            mapped.append(current)
            last = current
    return mapped


def draw_strokes(
    strokes: Iterable[Stroke],
    source_size: tuple[int, int],
    canvas: Rect,
    speed: float,
    pause: float,
    backend: str,
    abort_key: str,
    anchor_target: Point | None,
    fit_padding: int,
    draw_scale: float,
) -> None:
    abort_check = make_abort_checker(abort_key)
    stroke_list = list(strokes)
    transform = build_draw_transform(stroke_list, canvas, anchor_target, fit_padding, draw_scale)
    backend = resolve_input_backend(backend)
    if backend == "win32":
        draw_strokes_win32(
            stroke_list,
            transform,
            speed=speed,
            pause=pause,
            abort_check=abort_check,
        )
    else:
        draw_strokes_pyautogui(
            stroke_list,
            transform,
            speed=speed,
            pause=pause,
            abort_check=abort_check,
        )


def resolve_input_backend(backend: str) -> str:
    if backend == "auto":
        return "win32" if is_windows() else "pyautogui"
    if backend == "win32" and not is_windows():
        raise RuntimeError("--input-backend win32 is only available on Windows. Use --input-backend pyautogui.")
    return backend


def make_abort_checker(abort_key: str):
    key_codes = {
        "esc": 0x1B,
        "escape": 0x1B,
        "f8": 0x77,
        "f9": 0x78,
        "f10": 0x79,
        "f12": 0x7B,
        "pause": 0x13,
    }
    key_code = key_codes.get(abort_key.lower())
    if key_code is None:
        raise ValueError(f"Unsupported abort key: {abort_key}. Try esc, f8, f9, f10, f12, or pause.")

    if not is_windows():
        warned = False

        def check() -> None:
            nonlocal warned
            if not warned:
                print("Abort hotkeys are Windows-only; use Ctrl+C or move the mouse to the top-left corner to stop.")
                warned = True

        return check

    import win32api

    def check() -> None:
        if win32api.GetAsyncKeyState(key_code) & 0x8000:
            raise KeyboardInterrupt(f"Aborted by {abort_key.upper()}.")

    return check


def draw_strokes_pyautogui(
    strokes: Iterable[Stroke],
    transform: DrawTransform,
    speed: float,
    pause: float,
    abort_check,
) -> None:
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0

    for stroke in strokes:
        abort_check()
        if len(stroke) < 2:
            continue
        mapped = map_stroke(stroke, transform)
        if len(mapped) < 2:
            continue
        pyautogui.moveTo(mapped[0].x, mapped[0].y, duration=0)
        pyautogui.mouseDown(button="right")
        try:
            for point in mapped[1:]:
                abort_check()
                pyautogui.dragTo(point.x, point.y, duration=speed, button="right")
        finally:
            pyautogui.mouseUp(button="right")
        if pause:
            time.sleep(pause)


def draw_strokes_win32(
    strokes: Iterable[Stroke],
    transform: DrawTransform,
    speed: float,
    pause: float,
    abort_check,
) -> None:
    import pyautogui
    import win32api
    import win32con

    pyautogui.FAILSAFE = True
    fail_safe_check = getattr(pyautogui, "failSafeCheck", None) or getattr(pyautogui, "_failSafeCheck", None)

    for stroke in strokes:
        abort_check()
        if len(stroke) < 2:
            continue
        mapped = map_stroke(stroke, transform)
        if len(mapped) < 2:
            continue
        if fail_safe_check:
            fail_safe_check()
        win32api.SetCursorPos((mapped[0].x, mapped[0].y))
        time.sleep(0.01)
        win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
        try:
            for point in mapped[1:]:
                abort_check()
                if fail_safe_check:
                    fail_safe_check()
                win32api.SetCursorPos((point.x, point.y))
                if speed:
                    time.sleep(speed)
        finally:
            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        if pause:
            time.sleep(pause)


def print_mouse_pos() -> None:
    ensure_runtime_imports(include_automation=True)
    import pyautogui

    print("Move the mouse where you want to measure. Press Ctrl+C to stop.")
    last = None
    try:
        while True:
            pos = pyautogui.position()
            if pos != last:
                print(f"{pos.x},{pos.y}")
                last = pos
            time.sleep(0.25)
    except KeyboardInterrupt:
        print()


def option_help(text: str, show_advanced: bool) -> str:
    return text if show_advanced else argparse.SUPPRESS


def build_parser(show_advanced: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Draw line art in Slay the Spire 2.",
        epilog="Normal use: .\\run.ps1 --prompt \"simple poop emoji\" --draw",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--image", type=Path, help=option_help("Local image path to convert to strokes.", show_advanced))
    source.add_argument("--prompt", help="What to draw. Without --draw, this creates a preview.")
    source.add_argument("--url", help=option_help("Direct image URL to download and convert to strokes.", show_advanced))
    parser.add_argument("--advanced-help", action="store_true", help="Show advanced tuning options.")
    parser.add_argument("--no-builtins", action="store_true", help=option_help("Do not auto-generate simple built-in shapes from prompts.", show_advanced))
    parser.add_argument("--preview-only", action="store_true", help=option_help("Only generate a preview; do not draw.", show_advanced))
    parser.add_argument("--dry-run", action="store_true", help=option_help("Resolve everything but skip mouse drawing.", show_advanced))
    parser.add_argument("--draw", action="store_true", help="Actually send right-click drags to the target canvas.")
    parser.add_argument("--mouse-pos", action="store_true", help=option_help("Print live mouse coordinates for canvas calibration.", show_advanced))
    parser.add_argument("--area", type=parse_area, help=option_help("Screen-coordinate canvas rectangle: X,Y,W,H.", show_advanced))
    parser.add_argument("--window-title", default="Slay the Spire 2", help=option_help("Fallback target window title substring.", show_advanced))
    parser.add_argument("--process", default="slaythespire2.exe", help=option_help("Target process executable name.", show_advanced))
    parser.add_argument("--threshold", type=int, default=125, help=option_help("Line threshold, 0-255. Higher catches lighter lines.", show_advanced))
    parser.add_argument("--mode", choices=["auto", "dark", "edges", "sketch"], default="auto", help=option_help("Trace dark pixels, plain edges, or smoothed sketch edges.", show_advanced))
    parser.add_argument("--connect-gap", type=int, default=2, help=option_help("Pixel gap to bridge when joining a line into one held stroke.", show_advanced))
    parser.add_argument("--scale", type=int, default=610, help=option_help("Fit image into this many pixels before vectorizing.", show_advanced))
    parser.add_argument("--max-strokes", type=int, default=1450, help=option_help("Maximum number of strokes to draw.", show_advanced))
    parser.add_argument("--speed", type=float, default=0.01, help=option_help("Seconds per right-drag segment.", show_advanced))
    parser.add_argument("--pause", type=float, default=0.01, help=option_help("Seconds between strokes.", show_advanced))
    parser.add_argument("--input-backend", choices=["auto", "win32", "pyautogui"], default="auto", help=option_help("Mouse input backend.", show_advanced))
    parser.add_argument("--abort-key", default="esc", help=option_help("Hotkey to stop drawing: esc, f8, f9, f10, f12, or pause.", show_advanced))
    parser.add_argument("--center-in-window", action="store_true", help=option_help("Use the old behavior: center the drawing in the target canvas instead of starting at the mouse.", show_advanced))
    parser.add_argument("--fit-padding", type=int, default=35, help=option_help("Pixels to keep clear inside the safe drawing area.", show_advanced))
    parser.add_argument("--draw-scale", type=float, default=0.70, help=option_help("Fraction of the safe canvas to fill while drawing, from 0.1 to 1.0.", show_advanced))
    parser.add_argument("--countdown", type=int, default=5, help=option_help("Countdown seconds before drawing.", show_advanced))
    return parser


def main() -> int:
    if "--advanced-help" in sys.argv:
        build_parser(show_advanced=True).print_help()
        return 0

    parser = build_parser()
    args = parser.parse_args()

    if args.mouse_pos:
        print_mouse_pos()
        return 0

    if not args.image and not args.prompt and not args.url:
        parser.error('Use --prompt "what to draw". Add --draw when you want to draw in-game.')

    ensure_runtime_imports(include_automation=args.draw)

    root = Path(__file__).resolve().parents[1]
    previews_dir = root / "previews"
    downloads_dir = root / "downloads"
    previews_dir.mkdir(exist_ok=True)
    downloads_dir.mkdir(exist_ok=True)

    if args.image:
        image_path = args.image.expanduser().resolve()
        if not image_path.exists():
            raise FileNotFoundError(image_path)
        label = image_path.stem
    elif args.prompt:
        image_path = None if args.no_builtins else generate_builtin_prompt_image(args.prompt, downloads_dir)
        if image_path is None:
            image_path = fetch_wikimedia_image(args.prompt, downloads_dir)
        label = slugify(args.prompt)
    else:
        image_path = download_image_url(args.url, downloads_dir)
        label = image_path.stem

    sidecar_strokes = load_sidecar_strokes(image_path)
    if sidecar_strokes:
        strokes, source_size = sidecar_strokes
        if len(strokes) > args.max_strokes:
            strokes = strokes[:args.max_strokes]
    else:
        strokes, source_size = image_to_strokes(
            image_path=image_path,
            threshold=max(0, min(255, args.threshold)),
            fit_size=max(64, args.scale),
            max_strokes=max(1, args.max_strokes),
            mode=args.mode,
            connect_gap=max(1, min(5, args.connect_gap)),
        )
    preview_path = previews_dir / f"{slugify(label)}-preview.png"
    save_preview(strokes, source_size, preview_path)

    print(f"Image: {image_path}")
    print(f"Preview: {preview_path}")
    print(f"Strokes: {len(strokes)}")

    if args.preview_only or not args.draw:
        print("Preview only. Re-run the same command with --draw when it looks good.")
        return 0

    backend = resolve_input_backend(args.input_backend)
    if is_windows():
        if args.area:
            canvas = args.area
        else:
            window = find_game_window(args.process, args.window_title)
            canvas = default_canvas_for_window(window)
    else:
        if args.area:
            canvas = args.area
        else:
            canvas = default_canvas_for_screen()
            print("Non-Windows draw mode uses a safe screen canvas. Use --area X,Y,W,H for tighter game-window calibration.")
    print(f"Canvas: {canvas.x},{canvas.y} {canvas.w}x{canvas.h}")
    if args.center_in_window:
        print("Drawing will be centered in the target canvas.")
    else:
        print("Put your mouse where the first line should start.")
    if is_windows():
        print(f"Press {args.abort_key.upper()} or move mouse to the top-left screen corner to abort.")
    else:
        print("Use Ctrl+C or move mouse to the top-left screen corner to abort.")
    for i in range(args.countdown, 0, -1):
        print(f"Drawing in {i}...")
        time.sleep(1)

    if args.dry_run:
        print("Dry run: skipping mouse input.")
        return 0

    anchor_target = None
    if not args.center_in_window:
        import pyautogui

        pos = pyautogui.position()
        anchor_target = Point(pos.x, pos.y)
        print(f"Starting at mouse: {anchor_target.x},{anchor_target.y}")

    draw_strokes(
        strokes,
        source_size,
        canvas,
        speed=args.speed,
        pause=args.pause,
        backend=backend,
        abort_key=args.abort_key,
        anchor_target=anchor_target,
        fit_padding=args.fit_padding,
        draw_scale=args.draw_scale,
    )
    print("Done.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        raise SystemExit(130)
