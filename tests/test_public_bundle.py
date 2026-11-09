"""Regression: the public bundle generator crashed (UnicodeDecodeError) on bundled WOFF2 fonts
and omitted docs/assets referenced by the README. Build the real archive and inspect it."""
import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_public_bundle', ROOT/'scripts'/'build_public_bundle.py')
bundle = importlib.util.module_from_spec(spec); spec.loader.exec_module(bundle)


class PublicBundleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)/'PUBLIC_SOURCE.zip'
        cls.inv = bundle.build(cls.out, ROOT)
        cls.names = zipfile.ZipFile(cls.out).namelist()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_binary_fonts_are_included_not_decoded(self):
        fonts = [n for n in self.names if n.endswith('.woff2')]
        self.assertGreaterEqual(len(fonts), 4)
        self.assertTrue(any(n.endswith('OFL-Inter.txt') for n in self.names))
        self.assertGreater(self.inv['binary_files'], 0)

    def test_readme_images_resolve_inside_archive(self):
        readme = (ROOT/'README.md').read_text()
        refs = re.findall(r'(?:src="|\]\()((?:docs|src)/[^")\s]+)', readme)
        self.assertTrue(refs)
        for ref in refs:
            self.assertIn(f'kuku-promo-lab/{ref}', self.names, ref)

    def test_nothing_private_or_generated(self):
        for n in self.names:
            parts = n.split('/')
            self.assertFalse({'private', 'dist', '__pycache__', '.venv', 'build'} & set(parts), n)
            self.assertFalse(n.endswith(('.zip', '.log', '.pyc')), n)
        self.assertIn('kuku-promo-lab/src/kuku_promo_lab/studio/index.html', self.names)
        self.assertIn('kuku-promo-lab/tests/fixtures/synthetic/orbit-1.txt', self.names)

    def test_inventory_matches_archive_and_build_is_deterministic(self):
        z = zipfile.ZipFile(self.out)
        for f in self.inv['files']:
            self.assertEqual(hashlib.sha256(z.read('kuku-promo-lab/'+f['path'])).hexdigest(), f['sha256'])
        self.assertEqual(self.inv['file_count'], len(self.names))
        self.assertEqual(hashlib.sha256(self.out.read_bytes()).hexdigest(), self.inv['sha256'])
        again = Path(self.tmp.name)/'again.zip'
        self.assertEqual(bundle.build(again, ROOT)['sha256'], self.inv['sha256'])
        with self.assertRaises(SystemExit):
            bundle.build(self.out, ROOT)

    def test_secret_and_binary_handling(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'README.md').write_text('# x\n')
            (root/'src/kuku_promo_lab').mkdir(parents=True)
            (root/'src/kuku_promo_lab/font.woff2').write_bytes(b'wOF2\x94\x00\xff\xfe')  # invalid UTF-8
            (root/'src/kuku_promo_lab/ok.py').write_text('x = 1\n')
            inv = bundle.build(root/'out'/'b.zip', root)
            self.assertEqual(inv['binary_files'], 1)
            (root/'src/kuku_promo_lab/leak.py').write_text('KEY = "sk-' + 'a'*30 + '"\n')
            with self.assertRaises(SystemExit):
                bundle.build(root/'out'/'c.zip', root)


if __name__ == '__main__':
    unittest.main()
