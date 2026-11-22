"""Scan locally saved public campaign images and videos for DK codes."""
import argparse
import csv
import hashlib
import math
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps

from dk_code_hunter import KNOWN, PATTERN, configure_console

VIDEO_EXT = {'.mp4', '.webm', '.mov', '.m4v', '.avi', '.mkv'}


def frame_indices(total, fps, interval, limit):
    if total <= 0 or not math.isfinite(fps) or fps <= 0:
        return []
    stride = max(1, round(fps * interval), math.ceil(total / limit))
    indices = list(range(0, total, stride))
    if len(indices) < limit and indices[-1] != total - 1:
        indices.append(total - 1)
    return indices[:limit]


def frames(path, interval, limit):
    if path.suffix.lower() in VIDEO_EXT:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError('Install requirements-video.txt to scan video files.') from exc
        capture = cv2.VideoCapture(str(path.resolve()))
        try:
            if not capture.isOpened():
                raise RuntimeError('Cannot open video')
            fps = capture.get(cv2.CAP_PROP_FPS)
            total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            indices = frame_indices(total, fps, interval, limit)
            if not indices:
                raise RuntimeError('Video duration or frame rate is unavailable')
            for index in indices:
                capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, frame = capture.read()
                if ok:
                    yield f'{index / fps:.2f}s', Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                else:
                    print(f'[SKIP FRAME] {index}')
        finally:
            capture.release()
    else:
        with Image.open(path) as image:
            count = getattr(image, 'n_frames', 1)
            step = max(1, math.ceil(count / limit))
            for index in range(0, count, step):
                image.seek(index)
                yield f'frame_{index}', image.convert('RGB')


def regions(image, tiles):
    gray = ImageOps.autocontrast(ImageOps.grayscale(image))
    if not tiles:
        yield 'full', gray
        return
    yield 'full', gray
    w, h = gray.size
    for x in (0, 1):
        for y in (0, 1):
            crop = gray.crop((0 if x == 0 else w * 2 // 5,
                              0 if y == 0 else h * 2 // 5,
                              w * 3 // 5 if x == 0 else w,
                              h * 3 // 5 if y == 0 else h))
            if min(crop.size) > 0:
                yield f'tile_{x}_{y}', crop


def main():
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+', type=Path)
    parser.add_argument('--interval', type=float, default=1.0, help='Minimum seconds between video frames')
    parser.add_argument('--max-frames', type=int, default=60, help='Maximum frames per input')
    parser.add_argument('--tiles', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('dk_results/media'))
    parser.add_argument('--tesseract', default=r'C:\Program Files\Tesseract-OCR\tesseract.exe')
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval <= 0 or args.max_frames < 1:
        parser.error('Interval and max-frames must be positive')
    if Path(args.tesseract).is_file():
        pytesseract.pytesseract.tesseract_cmd = args.tesseract
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        parser.error(f'Tesseract unavailable: {exc}')
    args.output.mkdir(parents=True, exist_ok=True)
    seen = set()
    codes = set()
    completed = 0
    errors = 0
    with (args.output / 'results.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['code', 'status', 'file', 'position', 'region', 'evidence'])
        for path in args.files:
            try:
                for position, image in frames(path, args.interval, args.max_frames):
                    digest = hashlib.sha256(image.tobytes()).hexdigest()
                    key = (path, image.size, digest)
                    if key in seen:
                        continue
                    seen.add(key)
                    completed += 1
                    print(f'[FRAME] {path.name} {position}', flush=True)
                    for label, crop in regions(image, args.tiles):
                        factor = max(1, min(3, 1500 / max(crop.size)))
                        if factor > 1:
                            crop = crop.resize(tuple(round(v * factor) for v in crop.size), Image.Resampling.LANCZOS)
                        try:
                            text = pytesseract.image_to_string(crop, lang='eng', config='--psm 11', timeout=10)
                        except Exception as exc:
                            errors += 1
                            print(f'[OCR SKIP] {position} {label}: {exc}')
                            continue
                        matches = sorted(set(PATTERN.findall(text)))
                        for code in matches:
                            status = 'previously_listed' if code in KNOWN else 'unverified_candidate'
                            evidence = args.output / f'{completed:05}_{label}.png'
                            crop.save(evidence)
                            writer.writerow([code, status, str(path), position, label, str(evidence)])
                            stream.flush()
                            codes.add(code)
                            print(f'[CODE] {code} {status} {position} {label}')
            except (OSError, ValueError, RuntimeError) as exc:
                errors += 1
                print(f'[FILE SKIP] {path}: {exc}')
    (args.output / 'codes.txt').write_text('\n'.join(sorted(codes)), encoding='utf-8')
    (args.output / 'new_codes.txt').write_text('\n'.join(sorted(codes - KNOWN)), encoding='utf-8')
    print(f'[DONE] {completed} frames, {errors} errors, {len(codes)} unique unverified candidates')


if __name__ == '__main__':
    main()
