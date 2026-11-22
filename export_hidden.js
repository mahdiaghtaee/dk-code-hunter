(() => {
    if (!/(^|\.)digikala\.com$/i.test(location.hostname) ||
        /\/(?:profile|cart|checkout|login|register|account|prize-catcher)(?:\/|$)/i.test(location.pathname)) {
        console.warn('Run only on a public Digikala content page.');
        return;
    }
    const images = new Map();
    const svg_text = new Set();
    function visible(el) {
        const r = el.getBoundingClientRect();
        if (r.width < 16 || r.height < 16) return false;
        if (el.closest('[hidden], [aria-hidden="true"]')) return false;
        const css = getComputedStyle(el);
        return css.display !== 'none' && css.visibility === 'visible' && css.opacity !== '0' &&
            (!el.checkVisibility || el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}));
    }
    function add(el, value, kind, width, height, alt = '') {
        if (!value || !visible(el)) return;
        let u;
        try { u = new URL(value, location.href); } catch { return; }
        if (u.protocol !== 'https:' || !/(^|\.)(digikala\.com|digikalausercontent\.com)$/i.test(u.hostname)) return;
        if (/\/(?:digikala-products|products?|product_images?)\//i.test(u.pathname) ||
            /\.(?:mp4|webm|mov|mp3|m4v|pdf|ico|svgz)$/i.test(u.pathname)) return;
        const r = el.getBoundingClientRect();
        const region = el.closest('footer, [role="contentinfo"]') ? 'footer' : '';
        const row = {
            url: u.href, kind, alt: String(alt).slice(0, 300), region,
            width: Math.round(width || r.width), height: Math.round(height || r.height),
            displayWidth: Math.round(r.width), displayHeight: Math.round(r.height)
        };
        const key = [row.url, kind, region].join('|');
        if (!images.has(key)) images.set(key, row);
    }
    for (const img of document.images) {
        if (img.complete && img.naturalWidth && img.naturalHeight)
            add(img, img.currentSrc || img.src, 'image', img.naturalWidth, img.naturalHeight, img.alt);
    }
    // Read computed styles only; this does not fetch additional resources.
    for (const el of document.querySelectorAll('body *')) {
        if (!visible(el) || el.closest('video, audio, iframe')) continue;
        const css = getComputedStyle(el);
        for (const value of [css.backgroundImage, css.maskImage, css.webkitMaskImage]) {
            for (const match of (value || '').matchAll(/url\(\s*(?:"([^"]+)"|'([^']+)'|([^)]*))\s*\)/g))
                add(el, match[1] || match[2] || match[3], 'background', 0, 0);
        }
    }
    for (const el of document.querySelectorAll('svg text')) {
        const parent = el.closest('svg');
        if (!parent || !visible(parent) || el.closest('defs, symbol, [hidden], [aria-hidden="true"]')) continue;
        const css = getComputedStyle(el);
        const bounds = el.getBoundingClientRect();
        if (css.visibility !== 'visible' || css.display === 'none' || css.opacity === '0' || !bounds.width || !bounds.height) continue;
        const value = (el.textContent || '').trim();
        if (/(?<![A-Za-z0-9])DK[A-Z0-9]{3,4}(?![A-Za-z0-9])/.test(value))
            svg_text.add(value.slice(0, 300));
    }
    const manifest = {page: location.origin + location.pathname, images: [...images.values()], svg_text: [...svg_text]};
    const url = URL.createObjectURL(new Blob([JSON.stringify(manifest, null, 2)], {type: 'application/json'}));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'dk-hidden-images.json';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    console.log('Exported assets:', images.size, 'SVG text matches:', svg_text.size);
})();
