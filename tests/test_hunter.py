import unittest

from dk_code_hunter import PATTERN, allowed_host, canonical, priority, candidate_matches, image_priority


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


if __name__ == '__main__':
    unittest.main()
