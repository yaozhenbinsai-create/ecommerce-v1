import json
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch

from studio import providers


class ModelConnectionsTests(unittest.TestCase):
    def test_fresh_install_ignores_other_stores_and_ambient_keys(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            other = root / 'other'
            other.mkdir()
            (other / 'credentials.json').write_text(json.dumps({'1': secrets.token_urlsafe(24)}))
            (other / 'shop_ops.db').write_bytes(b'unrelated database')
            child = root / 'studio'
            with patch.dict(os.environ, {'ECOMMERCE1_DATA_DIR': str(child), 'STUDIO_SUPPLIER_DATA': str(other),
                                         'STUDIO_SUPPLIER_ROOT': str(other), 'OPENAI_API_KEY': secrets.token_urlsafe(24),
                                         'STUDIO_MODEL_KEY_1': secrets.token_urlsafe(24)}):
                self.assertEqual(providers.models()['items'], [])
                self.assertEqual(providers.models()['plans'], [])
                with self.assertRaises(providers.ProviderError):
                    providers.select_model()

    def test_only_explicit_model_and_route_use_the_deployments_key(self):
        with tempfile.TemporaryDirectory() as scratch, patch.dict(os.environ, {'ECOMMERCE1_DATA_DIR': scratch}):
            supplied = secrets.token_urlsafe(24)
            model = {'id': 1, 'name': '本地测试', 'model_identifier': 'fixture', 'capability': '文本',
                     'protocol': 'responses', 'base_url': 'https://provider.example.invalid/v1'}
            path = Path(scratch) / 'model-connections.json'
            path.write_text(json.dumps({'models': [model], 'plans': []}), encoding='utf-8')
            with patch.dict(os.environ, {'STUDIO_MODEL_KEY_1': supplied}):
                self.assertEqual(providers.select_model(model_id=1), (model, supplied))
                with self.assertRaises(providers.ProviderError):
                    providers.select_model()
                plan = {'plan_type': '通用', 'model_ids': [1]}
                path.write_text(json.dumps({'models': [model], 'plans': [plan]}), encoding='utf-8')
                self.assertEqual(providers.select_model(), (model, supplied))
                self.assertNotIn(supplied, json.dumps(providers.models()))
