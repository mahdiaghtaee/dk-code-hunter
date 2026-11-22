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
