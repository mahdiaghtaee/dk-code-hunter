(() => {
    const images = [...document.images].filter(i => {
        const url = i.currentSrc || i.src;
        return i.complete && i.naturalWidth >= 250 && i.naturalHeight >= 70 &&
            /\/digikala-(?:adservice-banners|static)\//i.test(url);
    }).map(i => ({
        url: i.currentSrc || i.src,
        alt: i.alt,
        width: i.naturalWidth,
        height: i.naturalHeight
    }));
    const manifest = {page: location.origin + location.pathname, images};
    const blob = new Blob([JSON.stringify(manifest, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'dk-browser-images.json';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    console.log('Exported image URLs:', images.length);
})();
