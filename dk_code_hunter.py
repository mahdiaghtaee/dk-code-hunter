#!/usr/bin/env python3
"""Search public Digikala pages and banner images for DK campaign codes."""
import argparse
import csv
import heapq
import hashlib
import io
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from protego import Protego

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps
import pytesseract

UA = 'DKCampaignCodeFinder/1.0'
PATTERN = re.compile(r'(?<![A-Za-z0-9])DK[A-Z0-9]{3,4}(?![A-Za-z0-9])')
KNOWN = set('DKBLG DKOFF DKTPC DKDDK DKPLS DKBZR DKVIBE DKGLD DKSTY DKMCI DKCRM DKSIT DKRBK DKJET'.split())
SEEDS = [
    'https://www.digikala.com/landings/prize-catcher/',
    'https://www.digikala.com/',
    'https://www.digikala.com/mag/',
    'https://www.digikala.com/mag/tag/black-friday/',
    'https://www.digikala.com/mag/video/digikala-black-friday-1405-prize-catcher-tutorial/',
    'https://www.digikala.com/landings/new-app/',
    'https://www.digikala.com/landing/voucher-digikala/',
]
HINTS = ('black-friday','blackfriday','prize','catcher','jakodi','jako','campaign',
         'banner','voucher','landing','promotion','festival','newsroom',
         'بلک','جاکدی','جاکُدی','کمپین','جایزه')
BLOCK = ('/product/', '/search/', '/cart', '/checkout', '/profile', '/login',
         '/register', '/api/', '/wp-json/', '/wp-admin/', '/tag/page/', '/page/')
ASSET_DOMAINS = ('digikala.com', 'digikalausercontent.com')
IMG_EXT = re.compile(r'\.(?:png|jpe?g|webp|gif|avif)(?:$|\?)', re.I)
CSS_URL = re.compile(r'url\(\s*[\'"]?([^\)\'"\s]+)', re.I)


def configure_console():
    # Windows PowerShell pipes may use a legacy code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='backslashreplace')


def is_raster_candidate(url):
    return not urlsplit(url).path.lower().endswith(('.svg', '.svgz', '.ico'))


def allowed_host(url, asset=False):
    p = urlsplit(url)
    h = (p.hostname or '').lower()
    domains = ASSET_DOMAINS if asset else ('digikala.com',)
    return p.scheme == 'https' and any(h == d or h.endswith('.' + d) for d in domains)


def canonical(url, keep_query=False):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path or '/', p.query if keep_query else '', ''))


CONTEXT = re.compile(r'جاک[ُُ]?دی|کد\s*(?:تخفیف|جایزه|هدیه|مخفی)|black[\s-]?friday|promo\s*code|coupon|prize[\s-]?catcher|voucher', re.I)
CAMPAIGN_PATH = re.compile(r'jakodi|prize-catcher|black-friday|blackfriday|campaign|voucher|banner|slider', re.I)
PRODUCT_IMAGE = re.compile(r'/products?/|/product_images?/|/icons?/|/logos?/', re.I)


def candidate_matches(text, context='', excluded=()):
    for m in PATTERN.finditer(text or ''):
        code = m.group()
        if code in excluded:
            continue
        nearby = text[max(0, m.start() - 80):m.end() + 80]
        if code in KNOWN or CONTEXT.search(nearby) or CAMPAIGN_PATH.search(context):
            yield code, True
        else:
            yield code, False


def image_priority(url, page, label=''):
    return (5 * bool(CAMPAIGN_PATH.search(url))
            + 3 * bool(CONTEXT.search(label))
            + 2 * bool(CAMPAIGN_PATH.search(page))
            + 2 * bool(re.search(r'banner|slider|hero|campaign', label, re.I))
            - 8 * bool(PRODUCT_IMAGE.search(url)))


def priority(url, text=''):
    s = (url + ' ' + text).lower()
    return sum(1 for h in HINTS if h in s) + 5 * bool(CAMPAIGN_PATH.search(s))


class Hunter:
    def __init__(self, args):
        self.args = args
        self.session = requests.Session()
        self.session.headers['User-Agent'] = UA
        self.robots = {}
        self.last = defaultdict(float)
        self.findings = set()
        self.images = defaultdict(set)
        self.image_scores = {}
        self.image_hashes = set()
        self.unconfirmed = set()
        self.stats = defaultdict(int)
        self.seen = set()
        self.queued = set()
        self.queue = []
        self.counter = 0
        self.blocked_hosts = set()
        self.out = Path(args.output)
        self.out.mkdir(parents=True, exist_ok=True)

    def wait(self, url, delay=None):
        host = urlsplit(url).netloc
        delay = max(self.args.delay, delay or 0)
        time.sleep(max(0, delay - (time.monotonic() - self.last[host])))
        self.last[host] = time.monotonic()

    def permitted(self, url):
        p = urlsplit(url)
        origin = f'{p.scheme}://{p.netloc}'
        if p.netloc in self.blocked_hosts:
            return False
        if origin not in self.robots:
            self.wait(url)
            try:
                r = self.session.get(origin + '/robots.txt', timeout=12, allow_redirects=False)
                if r.status_code == 200:
                    rp = Protego.parse(r.text)
                elif r.status_code in (404, 410):
                    rp = Protego.parse('User-agent: *\nDisallow:')
                else:
                    print(f'[ROBOTS] Skipping {origin}: HTTP {r.status_code}')
                    self.robots[origin] = None
                    return False
                self.robots[origin] = rp
            except requests.RequestException as exc:
                print(f'[ROBOTS] Skipping {origin}: {exc}')
                self.robots[origin] = None
        rp = self.robots[origin]
        return rp is not None and rp.can_fetch(url, UA)

    def fetch(self, url, asset=False, limit=8_000_000):
        if not allowed_host(url, asset) or not self.permitted(url):
            self.stats['restricted_or_unavailable'] += 1
            print(f'[SKIP] robots/access: {url}')
            return None
        p = urlsplit(url)
        rp = self.robots[f'{p.scheme}://{p.netloc}']
        rate = rp.request_rate(UA)
        self.wait(url, max(rp.crawl_delay(UA) or 0, rate.seconds / rate.requests if rate and rate.requests else 0))
        try:
            with self.session.get(url, timeout=(10, 20), stream=True, allow_redirects=False) as r:
                if r.status_code in (403, 429):
                    self.blocked_hosts.add(p.netloc)
                    self.stats[f'http_{r.status_code}'] += 1
                    print(f'[STOP HOST] {p.netloc}: HTTP {r.status_code}')
                    return None
                if r.status_code != 200:
                    self.stats[f'http_{r.status_code}'] += 1
                    print(f'[HTTP {r.status_code}] {url}')
                    return None
                data = bytearray()
                for chunk in r.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > limit:
                        return None
                return bytes(data), r.headers.get('Content-Type', '')
        except requests.RequestException as exc:
            print(f'[HTTP] {url}: {exc}')
            return None

    def report(self, text, source, method, asset=''):
        context = asset if method == 'ocr' and self.image_scores.get(asset, 0) >= 5 else ''
        if method == 'ocr' and self.image_scores.get(asset, 0) >= 5:
            context += ' banner'
        for code, plausible in candidate_matches(text, context, self.args.exclude):
            if not plausible:
                self.unconfirmed.add((code, method, source, asset))
                continue
            row = (code, 'previously_listed' if code in KNOWN else 'new_candidate', method, source, asset)
            if row in self.findings:
                continue
            self.findings.add(row)
            print(f'  [CODE] {code} | {row[1]} | {method} | {source}')
            # Flush once per page or image, rather than on every match.

    def save(self):
        with (self.out / 'results.csv').open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(('code', 'status', 'method', 'page_url', 'image_url'))
            w.writerows(sorted(self.findings, key=lambda x: (x[1] != 'new_candidate', x)))
        with (self.out / 'unconfirmed.csv').open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(('code', 'method', 'page_url', 'image_url'))
            w.writerows(sorted(self.unconfirmed))
        (self.out / 'codes.txt').write_text('\n'.join(sorted({r[0] for r in self.findings})), encoding='utf-8')
        (self.out / 'new_codes.txt').write_text('\n'.join(sorted({r[0] for r in self.findings if r[0] not in KNOWN})), encoding='utf-8')

    def enqueue(self, url, depth=0, text='', seed=False):
        if not allowed_host(url) or depth > self.args.depth:
            return
        url = canonical(url)
        if url in self.queued:
            return
        path = urlsplit(url).path.lower()
        if not seed and (any(x in path for x in BLOCK) or re.search(r'\.[a-z0-9]{2,5}$', path)):
            return
        score = priority(url, text)
        if not seed and score == 0 and not self.args.broad:
            return
        self.queued.add(url)
        self.counter += 1
        heapq.heappush(self.queue, (-1000 if seed else -score, depth, self.counter, url))

    def add_image(self, value, page, label=''):
        if not value or value.startswith(('data:', 'blob:')):
            return
        url = canonical(urljoin(page, value.strip()), True)
        if allowed_host(url, asset=True) and is_raster_candidate(url):
            score = image_priority(url, page, label)
            if not self.args.broad and score < 0:
                return
            self.images[url].add(page)
            self.image_scores[url] = max(score, self.image_scores.get(url, -100))

    def parse(self, html, url, depth, method='html'):
        soup = BeautifulSoup(html, 'html.parser')
        for a in soup.find_all('a', href=True):
            self.enqueue(urljoin(url, a['href']), depth + 1, a.get_text(' ', strip=True))
        for tag in soup.find_all(['img', 'source']):
            label = ' '.join([tag.get('alt', ''), tag.get('title', ''), ' '.join(tag.get('class', []))])
            variants = (tag.get('data-srcset') or tag.get('srcset') or '').split(',')
            variants = [v.strip().split() for v in variants if v.strip()]
            def size(v):
                try:
                    return float(v[1][:-1]) if len(v) > 1 else 0
                except ValueError:
                    return 0
            if variants:
                self.add_image(max(variants, key=size)[0], url, label)
            else:
                value = next((tag.get(k) for k in ('data-src', 'data-original', 'data-lazy-src', 'data-lazy', 'src') if tag.get(k)), '')
                self.add_image(value, url, label)
            self.report(tag.get('alt', ''), url, method + ':image_alt')
            self.report(tag.get('title', ''), url, method + ':image_title')
        for t in soup.find_all(style=True):
            for val in CSS_URL.findall(t['style']):
                self.add_image(val, url)
        for t in soup.find_all('meta', content=True):
            if t.get('property') in ('og:image', 'og:image:secure_url') or t.get('name') == 'twitter:image':
                self.add_image(t['content'], url)
        for t in soup.find_all(['script', 'style', 'noscript']):
            t.decompose()
        self.report(soup.get_text(' ', strip=True), url, method + ':text')
        self.stats['pages_parsed'] += 1
        self.save()

    def render(self, url, depth):
        # Render only previously accessible public documents, without login or API calls.
        if not self.args.render or not self.permitted(url):
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print('[RENDER] Install playwright and chromium first.')
            self.args.render = False
            return
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(user_agent=UA, service_workers='block')
                def route_request(route):
                    req = route.request
                    u = req.url
                    if (req.method != 'GET' or req.resource_type not in ('document', 'script', 'stylesheet')
                            or not allowed_host(u, asset=True) or not self.permitted(u)
                            or (req.resource_type == 'document' and canonical(u) != canonical(url))):
                        route.abort()
                        return
                    p = urlsplit(u)
                    rp = self.robots[f'{p.scheme}://{p.netloc}']
                    rate = rp.request_rate(UA)
                    self.wait(u, max(rp.crawl_delay(UA) or 0, rate.seconds / rate.requests if rate and rate.requests else 0))
                    try:
                        response = route.fetch(timeout=15000, max_redirects=0)
                        if response.status in (403, 429):
                            self.blocked_hosts.add(p.netloc)
                            route.abort()
                        elif 300 <= response.status < 400:
                            route.abort()
                        else:
                            route.fulfill(response=response)
                    except Exception:
                        route.abort()
                context.route('**/*', route_request)
                page = context.new_page()
                page.goto(url, wait_until='domcontentloaded', timeout=45000)
                page.wait_for_timeout(1200)
                self.parse(page.content(), url, depth, method='rendered')
                browser.close()
        except Exception as exc:
            print(f'[RENDER SKIP] {exc}')

    def crawl(self):
        for seed in self.args.url or SEEDS:
            self.enqueue(seed, seed=True)
        for n in range(self.args.max_pages):
            if not self.queue:
                break
            _, depth, _, url = heapq.heappop(self.queue)
            self.seen.add(url)
            print(f'[PAGE {n+1}/{self.args.max_pages}] {url}')
            result = self.fetch(url, limit=12_000_000)
            if result:
                data, content_type = result
                if 'html' in content_type.lower() or data.lstrip().startswith(b'<'):
                    self.parse(data.decode('utf-8', errors='replace'), url, depth)
                    if self.args.render and n < self.args.render_pages:
                        self.render(url, depth)
        self.save()

    def ocr(self):
        if self.args.no_ocr:
            print('[OCR] Disabled by --no-ocr')
            return
        try:
            pytesseract.get_tesseract_version()
        except Exception as exc:
            print(f'[OCR] Tesseract unavailable: {exc}')
            return
        # Campaign-related images first, then remaining image URLs.
        ranked = sorted(self.images, key=lambda u: -self.image_scores.get(u, 0))
        print(f'[OCR] {len(ranked)} image URLs collected; scanning up to {self.args.max_images}')
        evidence = self.out / 'evidence'
        evidence.mkdir(exist_ok=True)
        for i, url in enumerate(ranked[:self.args.max_images], 1):
            print(f'[IMAGE {i}/{min(len(ranked), self.args.max_images)}] {url}')
            result = self.fetch(url, asset=True)
            if not result:
                continue
            content_type = result[1].split(';', 1)[0].strip().lower()
            if content_type in ('image/svg+xml', 'text/html', 'application/xhtml+xml'):
                self.stats['non_raster_images'] += 1
                continue
            digest = hashlib.sha256(result[0]).digest()
            if digest in self.image_hashes:
                self.stats['duplicate_images'] += 1
                continue
            self.image_hashes.add(digest)
            try:
                with Image.open(io.BytesIO(result[0])) as original:
                    if original.width < 100 or original.height < 50 or original.width * original.height > 25_000_000:
                        continue
                    self.stats['images_ocr'] += 1
                    count = getattr(original, 'n_frames', 1)
                    limit = 8 if self.args.deep_ocr else 3
                    frames = sorted({round(i * (count - 1) / max(1, min(count, limit) - 1)) for i in range(min(count, limit))})
                    for frame in frames:
                        original.seek(frame)
                        img = original.convert('RGB')
                        gray = ImageOps.autocontrast(ImageOps.grayscale(img))
                        scale = min(3, max(1, 1500 / max(gray.size)))
                        if scale > 1:
                            gray = gray.resize(tuple(round(v * scale) for v in gray.size), Image.Resampling.LANCZOS)
                        variants = (gray, ImageOps.invert(gray)) if self.args.deep_ocr else (gray,)
                        for variant in variants:
                            text = pytesseract.image_to_string(variant, lang='eng', config='--psm 11', timeout=10)
                            self.stats['ocr_passes'] += 1
                            if PATTERN.search(text):
                                img.save(evidence / f'image_{i:05}_{frame}.png')
                                for page in sorted(self.images[url]):
                                    self.report(text, page, 'ocr', url)
                                self.save()
            except Exception as exc:
                self.stats['ocr_errors'] += 1
                print(f'[OCR SKIP] {url}: {exc}')
        self.save()


def main():
    configure_console()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', action='append', help='Start URL; repeat for several URLs')
    p.add_argument('--max-pages', type=int, default=18)
    p.add_argument('--max-images', type=int, default=40)
    p.add_argument('--depth', type=int, default=2)
    p.add_argument('--delay', type=float, default=2.5)
    p.add_argument('--output', default='dk_results')
    p.add_argument('--broad', action='store_true', help='Also follow non-campaign public links')
    p.add_argument('--no-ocr', action='store_true')
    p.add_argument('--deep-ocr', action='store_true', help='Try inverted images and additional animation frames')
    p.add_argument('--exclude', action='append', default=[], type=str.upper, help='Ignore a known irrelevant code; repeat as needed')
    p.add_argument('--render', action='store_true', help='Optionally render public HTML and scripts with Playwright')
    p.add_argument('--render-pages', type=int, default=3, help='Maximum number of initial pages to render')
    p.add_argument('--tesseract', default=r'C:\Program Files\Tesseract-OCR\tesseract.exe')
    a = p.parse_args()
    if a.max_pages < 1 or a.max_images < 0 or a.depth < 0 or a.delay < 2:
        p.error('Use positive page counts, nonnegative image/depth counts and delay >= 2 seconds.')
    if Path(a.tesseract).is_file():
        pytesseract.pytesseract.tesseract_cmd = a.tesseract
    hunter = Hunter(a)
    try:
        hunter.crawl()
        hunter.ocr()
    except KeyboardInterrupt:
        print('\n[STOP] Saving collected results.')
    finally:
        hunter.save()
    print(f'\n[DONE] {len({r[0] for r in hunter.findings})} unique candidates. Results: {hunter.out.resolve()}')
    print('[STATS]', dict(hunter.stats))
    print(f'[REVIEW] {len(hunter.unconfirmed)} unconfirmed occurrences: unconfirmed.csv')
    print('Candidates are not verified discount codes; inspect source/evidence before use.')


if __name__ == '__main__':
    main()
