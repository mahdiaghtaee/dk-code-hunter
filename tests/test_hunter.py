import unittest

from dk_code_hunter import PATTERN, allowed_host, canonical, priority


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


if __name__ == '__main__':
    unittest.main()
