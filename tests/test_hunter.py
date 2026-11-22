import unittest

from dk_code_hunter import PATTERN, allowed_host, canonical, priority, candidate_matches, image_priority, configure_console, is_raster_candidate, skip_image


class HunterTests(unittest.TestCase):
    def test_code_lengths(self):
        self.assertEqual(PATTERN.findall('DKABC DKAB12 DKAB DKABCDE XDKABC'),
                         ['DKABC', 'DKAB12'])

    def test_url_scope(self):
        self.assertTrue(allowed_host('https://www.digikala.com/mag/'))
        self.assertFalse(allowed_host('https://digikala.com.example.org/'))
        self.assertFalse(allowed_host('http://www.digikala.com/'))
        self.assertFalse(allowed_host('https://example.org/'))

    def test_canonical_url(self):
        self.assertEqual(canonical('https://www.digikala.com/mag/?q=test#top'),
                         'https://www.digikala.com/mag/')

    def test_campaign_priority(self):
        self.assertGreater(priority('https://www.digikala.com/black-friday/'),
                           priority('https://www.digikala.com/'))

    def test_unrelated_token(self):
        self.assertEqual(list(candidate_matches('product DKABC')), [('DKABC', False)])

    def test_coupon_context(self):
        self.assertEqual(list(candidate_matches('کد تخفیف: DKABC')), [('DKABC', True)])

    def test_excluded_token(self):
        self.assertEqual(list(candidate_matches('کد تخفیف: DKABC', excluded=['DKABC'])), [])

    def test_image_order(self):
        self.assertGreater(image_priority('https://www.digikala.com/banner.jpg', '', ''),
                           image_priority('https://www.digikala.com/products/photo.jpg', '', ''))

    def test_robots_wildcards(self):
        from protego import Protego
        parser = Protego.parse('User-agent: *\nDisallow: *?*\nAllow: *?x-oss-process=image/*')
        self.assertFalse(parser.can_fetch('https://www.digikala.com/path?q=1', 'DKCampaignCodeFinder'))
        self.assertTrue(parser.can_fetch('https://www.digikala.com/path', 'DKCampaignCodeFinder'))

    def test_non_raster_images(self):
        self.assertFalse(is_raster_candidate('https://www.digikala.com/loading.SVG?v=2'))
        self.assertTrue(is_raster_candidate('https://www.digikala.com/banner.webp'))

    def test_legacy_console(self):
        import io
        from unittest.mock import patch
        stream = io.TextIOWrapper(io.BytesIO(), encoding='cp1256')
        with patch('sys.stdout', stream), patch('sys.stderr', stream):
            configure_console()
            print('\u06cc')
            stream.flush()
            self.assertIn(b'\\u06cc', stream.buffer.getvalue())

    def test_campaign_image_selection(self):
        base = 'https://www.digikala.com/mag/wp-content/uploads/'
        self.assertTrue(skip_image(base + '2025/11/black-friday.jpg', '2026-11'))
        self.assertTrue(skip_image(base + '2026/11/jakodi-150x150.jpg', '2026-11'))
        self.assertTrue(skip_image(base + '2026/11/jakodi.mp4', '2026-11', broad=True))
        self.assertTrue(skip_image('https://dkstatics-public.digikala.com/digikala-products/photo.jpg', '2026-11'))
        self.assertFalse(skip_image(base + '2026/11/jakodi-tutorial-2.jpg', '2026-11'))
        self.assertFalse(skip_image(base + '2025/11/black-friday.jpg', '2026-11', broad=True))

    def test_jakodi_priority(self):
        self.assertGreater(image_priority('https://www.digikala.com/jakodi-tutorial-2.jpg', '', ''),
                           image_priority('https://www.digikala.com/black-friday.jpg', '', ''))

    def test_spot_page_priority(self):
        from dk_code_hunter import spot_page_priority
        self.assertLess(spot_page_priority('https://www.digikala.com/landings/prize-catcher/'), 0)
        self.assertLess(spot_page_priority('https://www.digikala.com/mag/video/example/'), 0)
        self.assertGreater(spot_page_priority('https://www.digikala.com/landing/example/', 'اشتراک پلاس'),
                           spot_page_priority('https://www.digikala.com/mag/black-friday/'))

    def test_spot_image_priority(self):
        from dk_code_hunter import spot_image_priority
        self.assertGreater(spot_image_priority('https://www.digikala.com/abc.webp', 'hero banner'),
                           spot_image_priority('https://www.digikala.com/jakodi-tutorial-2.jpg'))

    def test_magazine_carousel_is_not_ad(self):
        from dk_code_hunter import spot_image_priority
        page = 'https://www.digikala.com/mag/'
        self.assertEqual(spot_image_priority('https://www.digikala.com/mag/gta.jpg', 'carousel', page), 0)
        self.assertGreater(spot_image_priority('https://www.digikala.com/mag/banner.jpg', '', page), 0)

    def test_browser_banner_manifest(self):
        from dk_code_hunter import browser_banner_rows
        base = 'https://dkstatics-public.digikala.com/'
        rows = [{'url': base + kind + '/x.jpg', 'width': 2800, 'height': 100}
                for kind in ('digikala-adservice-banners', 'digikala-static', 'digikala-products')]
        self.assertEqual(len(list(browser_banner_rows(rows))), 1)
        self.assertEqual(len(list(browser_banner_rows(rows, True))), 2)

    def test_wide_banner_tiles(self):
        from dk_code_hunter import tile_boxes
        boxes = list(tile_boxes(14251, 512))
        self.assertGreater(len(boxes), 4)
        self.assertLessEqual(len(boxes), 16)
        self.assertEqual(boxes[0][0], 0)
        self.assertEqual(boxes[-1][2], 14251)


if __name__ == '__main__':
    unittest.main()
