import unittest

from scan_media import frame_indices, regions
from PIL import Image


class MediaTests(unittest.TestCase):
    def test_sample_limit(self):
        indices = frame_indices(3000, 30, 1, 60)
        self.assertLessEqual(len(indices), 60)
        self.assertEqual(indices[0], 0)
        self.assertTrue(all(0 <= i < 3000 for i in indices))

    def test_short_video(self):
        self.assertEqual(frame_indices(31, 30, 1, 60), [0, 30])

    def test_tiles(self):
        self.assertEqual(len(list(regions(Image.new('RGB', (200, 100)), True))), 5)
