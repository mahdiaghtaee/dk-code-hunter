#!/usr/bin/env python3
"""Search public Digikala pages and banner images for DK campaign codes."""
import argparse
import csv
import heapq
import hashlib
import io
import json
import re
import sys
import time
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
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
SPOT_SEEDS = [
    'https://www.digikala.com/',
    'https://www.digikala.com/landing/black-friday/',
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
    return not urlsplit(url).path.lower().endswith(('.svg', '.svgz', '.ico', '.mp4', '.webm', '.mov', '.mp3', '.m4v', '.pdf'))


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
PRODUCT_IMAGE = re.compile(r'/(?:digikala-products|products?|product_images?|icons?|logos?)/', re.I)
THUMBNAIL = re.compile(r'-(?:60|100|150|200)x(?:60|100|150|200)(?=\.[a-z]+$)', re.I)
UPLOAD_MONTH = re.compile(r'/uploads/(\d{4})/(\d{2})/')
JAKODI = re.compile(r'jakodi|prize-catcher|jako-di', re.I)


def skip_image(url, campaign_month, broad=False):
    if not is_raster_candidate(url):
        return True
    if broad:
        return False
    path = urlsplit(url).path
    uploaded = UPLOAD_MONTH.search(path)
    if uploaded and '-'.join(uploaded.groups()) < campaign_month:
        return True
    return bool(PRODUCT_IMAGE.search(path) or THUMBNAIL.search(path))


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
    return (20 * bool(JAKODI.search(url))
            + 5 * bool(CAMPAIGN_PATH.search(url))
            + 3 * bool(CONTEXT.search(label))
            + 2 * bool(CAMPAIGN_PATH.search(page))
            + 2 * bool(re.search(r'banner|slider|hero|campaign', label, re.I))
            - 8 * bool(PRODUCT_IMAGE.search(url)))


SPOT_HINT = re.compile(
    r'digi.?plus|digi.?gold|digi.?style|digi.?jet|digi.?pay|bazaar|vibe|'
    r'gift.?card|supermarket|new-app|newsroom|about-us|contact-us|faq|'
    r'دیجی[\s‌-]*(?:پلاس|استایل|جت|پی)|اشتراک\s*پلاس|طلای دیجیتال|'
    r'طلا و نقره دیجیتال|بازار دیجی|وایب|سوپرمارکت|کارت هدیه|'
    r'دانلود اپلیکیشن|اتاق خبر|درباره دیجی|تماس با دیجی|پرسش‌های متداول', re.I)
SPOT_IMAGE = re.compile(r'banner|slider|hero|carousel|promotion|campaign|footer|billboard|badge|shortcut|بنر', re.I)


def spot_page_priority(url, label=''):
    path = urlsplit(url).path.lower()
    if 'prize-catcher' in path or '/video/' in path:
        return -100
    return 15 * bool(SPOT_HINT.search(url + ' ' + label)) + 5 * bool(
        CAMPAIGN_PATH.search(url + ' ' + label))


def spot_image_priority(url, label='', page=''):
    explicit_ad = re.compile(r'banner|advertis|promotion|campaign|billboard|بنر|تبلیغ', re.I)
    if re.search(r'jakodi-tutorial|jakodi-featured', url, re.I):
        return -12
    strong = bool(explicit_ad.search(url + ' ' + label))
    service = bool(SPOT_HINT.search(url + ' ' + label))
    if '/mag/' in urlsplit(page).path.lower():
        # News carousel membership alone is not advertising evidence.
        return 20 * strong + 8 * (service and strong)
    return 20 * strong + 8 * service + 5 * bool(SPOT_IMAGE.search(label))


def browser_banner_rows(payload, include_static=False):
    rows = payload if isinstance(payload, list) else payload.get('images', [])
    if not isinstance(rows, list):
        raise ValueError('Browser manifest images must be a list')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('url'), str):
            continue
        url = canonical(row['url'], True)
        if not allowed_host(url, asset=True) or not is_raster_candidate(url):
            continue
        path = urlsplit(url).path.lower()
        ad = '/digikala-adservice-banners/' in path
        static = '/digikala-static/' in path and include_static
        if not (ad or static) or url in seen:
            continue
        try:
            w, h = int(row.get('width', 0)), int(row.get('height', 0))
        except (ValueError, TypeError, OverflowError):
            continue
        if w < 250 or h < 70:
            continue
        seen.add(url)
        page = row.get('page') or (payload.get('page') if isinstance(payload, dict) else None)
        if not isinstance(page, str) or not allowed_host(page):
            page = url
        label = str(row.get('alt') or '')
        score = (100 if ad else 30) + 20 * bool(CONTEXT.search(label))
        yield url, canonical(page, page == url), score


def hidden_rows(payload):
    rows = payload if isinstance(payload, list) else payload.get('images', [])
    if not isinstance(rows, list):
        raise ValueError('Browser manifest images must be a list')
    selected = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('url'), str):
            continue
        url = canonical(row['url'], True)
        page = row.get('page') or (payload.get('page') if isinstance(payload, dict) else None)
        if not allowed_host(url, asset=True) or not isinstance(page, str) or not allowed_host(page):
            continue
        path = urlsplit(url).path.lower()
        if re.search(r'/(?:digikala-products|products?|product_images?)/', path):
            continue
        if not (is_raster_candidate(url) or path.endswith('.svg')):
            continue
        try:
            w, h = int(row.get('width', 0)), int(row.get('height', 0))
            dw, dh = int(row.get('displayWidth', w)), int(row.get('displayHeight', h))
        except (ValueError, TypeError, OverflowError):
            continue
        if min(w, h, dw, dh) < 16:
            continue
        label = str(row.get('alt') or '')
        kind = row.get('kind', 'image')
        region = row.get('region', '')
        ad = '/digikala-adservice-banners/' in path
        # Deliberately omit prominent public campaigns, retain compact/footer assets.
        if ad and (dw >= 650 or w >= 2400) and region != 'footer':
            continue
        if ad and CONTEXT.search(label):
            continue
        if max(dw, dh) > 1000 and not (kind == 'background' or region == 'footer'):
            continue
        score = (35 if kind == 'background' else 0) + (25 if region == 'footer' else 0)
        score += 25 if max(dw, dh) <= 200 else 10 if max(dw, dh) <= 500 else 0
        score += 15 if path.endswith('.svg') else 0
        score += 15 if SPOT_HINT.search(label + ' ' + path) else 0
        score += 10 if re.search(r'/digikala-static/|/icons?/|/logos?/', path) else 0
        if score <= 0:
            continue
        key = (url, canonical(page))
        selected[key] = max(score, selected.get(key, 0))
    for (url, page), score in sorted(selected.items(), key=lambda item: -item[1]):
        yield url, page, score


def svg_text(data):
    # Inspect only textual SVG elements, never scripts, metadata or path data.
    # No rendering, URL resolution or XML entity expansion.
    if len(data) > 1_000_000 or re.search(br'<!\s*(?:DOCTYPE|ENTITY)', data, re.I):
        return ''
    if b'\x00' in data:
        return ''
    root = ET.fromstring(data)
    if root.tag.rsplit('}', 1)[-1] != 'svg':
        return ''
    ignored = {'defs', 'metadata', 'script', 'style', 'symbol', 'title', 'desc', 'clipPath', 'mask'}
    def walk(node):
        tag = node.tag.rsplit('}', 1)[-1]
        style = node.attrib.get('style', '').replace(' ', '').lower()
        if (tag in ignored or node.attrib.get('display') == 'none'
                or node.attrib.get('visibility') == 'hidden'
                or 'display:none' in style or 'visibility:hidden' in style):
            return
        if tag == 'text':
            yield ''.join(node.itertext())
        else:
            for child in node:
                yield from walk(child)
    return '\n'.join(walk(root))


def tile_boxes(width, height):
    # Use additional horizontal strips for exceptionally wide banners.
    nx = max(2, min(16, math.ceil(width / max(1, height * 2))))
    ny = max(1, min(4, math.ceil(height / max(1, width * 2))))
    if nx == 2 and ny == 1 and width / max(1, height) < 3:
        ny = 2
    for x in range(nx):
        for y in range(ny):
            yield (int(max(0, x - .25) * width / nx),
                   int(max(0, y - .25) * height / ny),
                   math.ceil(min(nx, x + 1.25) * width / nx),
                   math.ceil(min(ny, y + 1.25) * height / ny))


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
        self.page_inventory = []
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
        score = spot_page_priority(url, text) if self.args.spots else priority(url, text)
        if self.args.spots and (score < 0 or (not seed and '/mag/' in path and not self.args.include_mag)):
            return
        if not seed and score == 0 and not self.args.broad:
            return
        self.queued.add(url)
        self.counter += 1
        heapq.heappush(self.queue, (-1000 if seed else -score, depth, self.counter, url))

    def add_image(self, value, page, label=''):
        if not value or value.startswith(('data:', 'blob:')):
            return
        url = canonical(urljoin(page, value.strip()), True)
        if allowed_host(url, asset=True) and not skip_image(url, self.args.campaign_month, self.args.broad):
            score = (spot_image_priority(url, label, page) if self.args.spots else image_priority(url, page, label))
            if (self.args.spots and score <= 0) or (not self.args.broad and score < 0):
                return
            self.images[url].add(page)
            self.image_scores[url] = max(score, self.image_scores.get(url, -100))

    def parse(self, html, url, depth, method='html'):
        soup = BeautifulSoup(html, 'html.parser')
        for a in soup.find_all('a', href=True):
            label = ' '.join([a.get_text(' ', strip=True), a.get('title', ''),
                              a.get('aria-label', '')] +
                             [im.get('alt', '') for im in a.find_all('img')])
            self.enqueue(urljoin(url, a['href']), depth + 1, label)
            if self.args.spots:
                self.report(label, url, method + ':link_label')
        for tag in soup.find_all(['img', 'source']):
            if tag.name == 'source' and tag.find_parent(['video', 'audio']):
                continue
            label_parts = [tag.get('alt', ''), tag.get('title', ''), ' '.join(tag.get('class', []))]
            if self.args.spots:
                if '/mag/' not in urlsplit(url).path.lower():
                    for parent in list(tag.parents)[:2]:
                        label_parts.extend([' '.join(parent.get('class', [])), parent.get('id', ''),
                                            parent.get('aria-label', '')])
                link = tag.find_parent('a')
                if link:
                    label_parts.extend([link.get('title', ''), link.get('href', '')])
            label = ' '.join(label_parts)
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
        accepted = sum(url in pages for pages in self.images.values())
        self.page_inventory.append((url, method, len(soup.find_all('img')), accepted, len(html)))
        print(f'[PAGE ASSETS] {url} | img tags={len(soup.find_all("img"))} | selected={accepted}')
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
        for seed in self.args.url or (SPOT_SEEDS if self.args.spots else SEEDS):
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

    def write_inventory(self):
        ranked = sorted(self.images, key=lambda u: -self.image_scores.get(u, 0))
        with (self.out / 'image_inventory.csv').open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(('score', 'image_url', 'page_urls'))
            for u in ranked:
                w.writerow((self.image_scores[u], u, ' | '.join(sorted(self.images[u]))))
        with (self.out / 'page_inventory.csv').open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(('page_url', 'method', 'img_tags', 'selected_images', 'html_chars'))
            w.writerows(self.page_inventory)

    def ocr(self):
        if self.args.no_ocr:
            print('[OCR] Disabled by --no-ocr')
            return
        try:
            engine = str(pytesseract.get_tesseract_version())
        except Exception as exc:
            print(f'[OCR] Tesseract unavailable: {exc}')
            return
        cache = Path(self.args.cache_dir)
        if not self.args.no_cache:
            cache.mkdir(parents=True, exist_ok=True)

        # Campaign-related images first, then remaining image URLs.
        ranked = sorted(self.images, key=lambda u: -self.image_scores.get(u, 0))
        print(f'[OCR] {len(ranked)} image URLs collected; scanning up to {self.args.max_images}')
        evidence = self.out / 'evidence'
        evidence.mkdir(exist_ok=True)
        debug = self.out / 'ocr_text'
        if self.args.ocr_debug:
            debug.mkdir(exist_ok=True)
        for i, url in enumerate(ranked[:self.args.max_images], 1):
            print(f'[IMAGE {i}/{min(len(ranked), self.args.max_images)}] {url}')
            result = self.fetch(url, asset=True)
            if not result:
                self.stats['images_unavailable'] += 1
                continue
            content_type = result[1].split(';', 1)[0].strip().lower()
            if self.args.hidden and (content_type == 'image/svg+xml' or urlsplit(url).path.lower().endswith('.svg')):
                try:
                    value = svg_text(result[0])
                    self.stats['svg_text_checked'] += 1
                    for page in sorted(self.images[url]):
                        self.report(value, page, 'svg_text', url)
                    if PATTERN.search(value):
                        (evidence / f'svg_{i:05}.txt').write_text(url + '\n\n' + value, encoding='utf-8')
                except (ET.ParseError, ValueError) as exc:
                    self.stats['svg_errors'] += 1
                    print(f'[SVG SKIP] {url}: {exc}')
                continue
            if content_type.startswith(('video/', 'audio/')) or content_type in ('image/svg+xml', 'text/html', 'application/xhtml+xml'):
                self.stats['non_raster_images'] += 1
                continue
            digest = hashlib.sha256(result[0]).digest()
            if digest in self.image_hashes:
                self.stats['duplicate_images'] += 1
                continue
            self.image_hashes.add(digest)
            try:
                with Image.open(io.BytesIO(result[0])) as original:
                    min_w, min_h = (16, 16) if self.args.hidden else (100, 50)
                    if original.width < min_w or original.height < min_h or original.width * original.height > 25_000_000:
                        self.stats['images_size_skipped'] += 1
                        continue
                    self.stats['images_ocr'] += 1
                    count = getattr(original, 'n_frames', 1)
                    limit = 8 if self.args.deep_ocr else 3
                    frames = sorted({round(i * (count - 1) / max(1, min(count, limit) - 1)) for i in range(min(count, limit))})
                    for frame in frames:
                        original.seek(frame)
                        img = original.convert('RGB')
                        gray = ImageOps.autocontrast(ImageOps.grayscale(img))
                        scale = min(6 if self.args.hidden else 3, max(1, 1500 / max(gray.size)))
                        if scale > 1:
                            gray = gray.resize(tuple(round(v * scale) for v in gray.size), Image.Resampling.LANCZOS)
                        variants = [(gray, 11)]
                        if self.args.deep_ocr:
                            variants.extend([(img, 11), (ImageOps.invert(gray), 11)])
                        if self.args.hidden and max(img.size) <= 200:
                            variants.append((gray, 7))
                        if self.args.tiles and (not self.args.hidden or max(img.size) > 300):
                            for coords in tile_boxes(*gray.size):
                                crop = gray.crop(coords)
                                if min(crop.size) > 0:
                                    factor = max(1, min(3, 1200 / max(crop.size)))
                                    crop = crop.resize(tuple(round(v * factor) for v in crop.size), Image.Resampling.LANCZOS)
                                    variants.append((crop, 11))
                        for pass_no, (variant, psm) in enumerate(variants, 1):
                            key = hashlib.sha256(
                                f'{engine}|eng|psm={psm}|{variant.mode}|{variant.size}|'.encode()
                                + variant.tobytes()).hexdigest()
                            entry = cache / (key + '.txt')
                            if not self.args.no_cache and entry.is_file():
                                text = entry.read_text(encoding='utf-8')
                                self.stats['ocr_cache_hits'] += 1
                            else:
                                text = pytesseract.image_to_string(variant, lang='eng', config=f'--psm {psm}', timeout=10)
                                self.stats['ocr_passes'] += 1
                                if not self.args.no_cache:
                                    entry.write_text(text, encoding='utf-8')
                            if self.args.ocr_debug:
                                (debug / f'{i:05}_{frame}_{pass_no}.txt').write_text(url + '\n\n' + text, encoding='utf-8')
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
    p.add_argument('--image-url', action='append', help='Scan only the specified public image URLs')
    p.add_argument('--browser-json', type=Path, help='Import banner URLs exported from the visible browser DOM')
    p.add_argument('--hidden', action='store_true', help='Prioritize small and unusual public browser assets instead of large banners')
    p.add_argument('--include-static', action='store_true', help='Also include non-product static images from browser manifest')
    p.add_argument('--cache-dir', default='dk_ocr_cache', help='Local OCR text cache shared by scans')
    p.add_argument('--no-cache', action='store_true', help='Always perform OCR again')
    p.add_argument('--campaign-month', default=date.today().strftime('%Y-%m'), help='Earliest dated upload month, YYYY-MM; --broad disables the filter')
    p.add_argument('--ocr-debug', action='store_true', help='Save raw OCR text for each pass')
    p.add_argument('--tiles', action='store_true', help='Also scan overlapping image crops; slower')
    p.add_argument('--max-pages', type=int, default=18)
    p.add_argument('--max-images', type=int, default=40)
    p.add_argument('--depth', type=int, default=2)
    p.add_argument('--delay', type=float, default=2.5)
    p.add_argument('--output', default='dk_results')
    p.add_argument('--broad', action='store_true', help='Also follow non-campaign public links')
    p.add_argument('--spots', action='store_true', help='Prioritize public service shortcuts and unusual static banner locations; skip video and redemption pages')
    p.add_argument('--include-mag', action='store_true', help='Allow following magazine links in spot mode')
    p.add_argument('--no-ocr', action='store_true', help='Collect page and image inventories without downloading images')
    p.add_argument('--deep-ocr', action='store_true', help='Try inverted images and additional animation frames')
    p.add_argument('--exclude', action='append', default=[], type=str.upper, help='Ignore a known irrelevant code; repeat as needed')
    p.add_argument('--render', action='store_true', help='Optionally render public HTML and scripts with Playwright')
    p.add_argument('--render-pages', type=int, default=3, help='Maximum number of initial pages to render')
    p.add_argument('--tesseract', default=r'C:\Program Files\Tesseract-OCR\tesseract.exe')
    a = p.parse_args()
    if a.max_pages < 1 or a.max_images < 0 or a.depth < 0 or a.delay < 2:
        p.error('Use positive page counts, nonnegative image/depth counts and delay >= 2 seconds.')
    if not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', a.campaign_month):
        p.error('--campaign-month must be YYYY-MM')
    if a.spots and a.broad:
        p.error('--spots and --broad cannot be combined')
    if a.hidden and (not a.browser_json or a.spots or a.broad):
        p.error('--hidden requires --browser-json and cannot be combined with --spots or --broad')
    if a.browser_json and (a.url or a.image_url or a.render):
        p.error('--browser-json cannot be combined with --url, --image-url or --render')
    if a.image_url and (a.url or a.no_ocr or len(a.image_url) > a.max_images):
        p.error('--image-url is OCR-only and must fit --max-images')
    if a.image_url and any(not allowed_host(u, asset=True) or not is_raster_candidate(u) for u in a.image_url):
        p.error('--image-url requires public Digikala HTTPS raster images')
    if Path(a.tesseract).is_file():
        pytesseract.pytesseract.tesseract_cmd = a.tesseract
    hunter = Hunter(a)
    try:
        if a.browser_json:
            try:
                manifest = json.loads(a.browser_json.read_text(encoding='utf-8-sig'))
                if not isinstance(manifest, (dict, list)):
                    raise ValueError('Expected an object or list')
                rows = hidden_rows(manifest) if a.hidden else browser_banner_rows(manifest, a.include_static)
                for url, page, score in rows:
                    hunter.images[url].add(page)
                    hunter.image_scores[url] = max(score, hunter.image_scores.get(url, 0))
                if a.hidden and isinstance(manifest, dict):
                    page = manifest.get('page', '')
                    if isinstance(page, str) and allowed_host(page):
                        for value in manifest.get('svg_text', []):
                            if isinstance(value, str):
                                hunter.report(value[:500], canonical(page), 'browser_svg_text')
            except (OSError, ValueError) as exc:
                p.error(f'Invalid browser manifest: {exc}')
            print(f'[BROWSER] {len(hunter.images)} selected {"hidden-location assets" if a.hidden else "banner URLs"}')
        elif a.image_url:
            for url in a.image_url:
                url = canonical(url, True)
                hunter.images[url].add(url)
                hunter.image_scores[url] = image_priority(url, url)
        else:
            hunter.crawl()
        hunter.write_inventory()
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
