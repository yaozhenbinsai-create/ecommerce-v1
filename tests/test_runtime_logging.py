"""Verify bounded files on actual disk; no application database or model calls."""
import logging
from logging.config import dictConfig
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.runtime_logging import configuration


class RuntimeLoggingTests(unittest.TestCase):
    def test_rotation_is_bounded_and_business_files_are_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / 'checkpoint.json'
            evidence.write_text('{"request":"preserved"}', encoding='utf-8')
            spec = configuration(root)
            spec['handlers']['runtime'].update(maxBytes=512, backupCount=2)
            dictConfig(spec)
            logger = logging.getLogger('studio.rotation_test')
            for index in range(120):
                logger.info('event %03d %s', index, 'bounded ' * 10)
            for handler in logger.parent.handlers:
                handler.flush()
            files = sorted((root / 'logs').glob('runtime.log*'))
            self.assertEqual(len(files), 3)
            self.assertLessEqual(sum(x.stat().st_size for x in files), 3 * 512)
            self.assertIn('event 119', (root / 'logs/runtime.log').read_text('utf-8'))
            self.assertEqual(evidence.read_text('utf-8'), '{"request":"preserved"}')
            logging.shutdown()


if __name__ == '__main__':
    unittest.main()
