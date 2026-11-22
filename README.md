# DK Code Hunter

A small Python utility for finding candidate Digikala campaign codes in public web pages and banner images. Codes start with `DK` and contain five or six alphanumeric characters in total.

## Setup (Windows)

Python 3.10 or newer is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

For image scanning, install Tesseract OCR with English language data:

```powershell
winget install --id UB-Mannheim.TesseractOCR -e
```

If needed, specify its executable with `--tesseract`. Use `--no-ocr` for text-only scanning.

Optional browser rendering:

```powershell
python -m pip install -r requirements-render.txt
python -m playwright install chromium
```

## Usage

```powershell
python dk_code_hunter.py
python dk_code_hunter.py --url https://www.digikala.com/mag/ --max-pages 40 --max-images 100
python dk_code_hunter.py --broad --render --max-pages 100 --max-images 200 --depth 3
python dk_code_hunter.py --help
```

The default run checks up to 18 pages and 40 images. Use `--deep-ocr` for more image passes and animation frames, or `--exclude DKABC` to ignore a confirmed irrelevant token. Raw HTML identifiers are no longer treated as codes. Weak matches are kept in `unconfirmed.csv`; candidate codes are still unverified. The console reports successful page parses, OCR passes and blocked or unavailable requests.

Results are written to `dk_results/`: `results.csv` contains source URLs and discovery methods, `codes.txt` lists distinct candidates, `new_codes.txt` excludes previously listed codes, and `evidence/` contains matching OCR images. Results are candidates, not verified discount codes. Existing result lists are replaced by a new run.

The crawler uses public HTTPS pages, respects robots.txt, limits requests, and stops requesting hosts that return HTTP 403 or 429. Browser rendering is restricted and may not work on pages requiring API calls. Review the site's rules before scanning; do not use this utility to bypass access restrictions or submit automated coupon attempts.

## Tests

```powershell
python -m unittest discover -s tests -v
```

### Windows console encoding

For UTF-8 logs, use `$env:PYTHONIOENCODING = 'utf-8'` and `python -X utf8 dk_code_hunter.py`. Legacy console encodings also fall back to escaped characters instead of aborting a scan. SVG and ICO assets are skipped by the raster OCR scanner.

### Targeted image diagnostics

The default scan skips dated uploads before the current month, small thumbnails, product images and video files. Use `--campaign-month YYYY-MM` to choose another campaign month, or `--broad` to include older and lower-priority images. `--image-url` scans specified public image URLs without crawling other pages; robots and HTTP restrictions still apply. `--ocr-debug` stores raw recognised text in `ocr_text/`. `--tiles` adds overlapping image crops, which may help small lettering but increases OCR time. Video files are not scanned.

### Offline media scanning

Save public advertising videos or screenshots you are permitted to use to your computer. To scan video files, install `requirements-video.txt`. The local scanner makes no website requests. For images only, the base requirements suffice.

```powershell
python -m pip install -r requirements-video.txt
python scan_media.py video.mp4 screenshot.png --tiles --max-frames 60
```

Video sampling can miss brief appearances of codes; use a smaller interval and a higher frame limit when appropriate. Results and image evidence are stored under `dk_results/media/`. Candidates need visual review and manual validation.
