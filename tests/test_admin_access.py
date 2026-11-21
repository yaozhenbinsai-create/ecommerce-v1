import os
import secrets
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from studio.admin_auth import require_admin, save_password


class AdminAccessTests(unittest.TestCase):
    def test_password_setup_and_rotation(self):
        app = FastAPI()
        app.middleware('http')(require_admin)

        @app.get('/')
        def home():
            return {'ok': True}

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ECOMMERCE1_DATA_DIR': directory}), TestClient(app) as client:
            self.assertEqual(client.get('/').status_code, 401)
            password = secrets.token_urlsafe(24)
            save_password(password)
            self.assertEqual(client.get('/', auth=('OTHER', password)).status_code, 401)
            self.assertEqual(client.get('/', auth=('ADMIN', password)).status_code, 200)
            next_password = secrets.token_urlsafe(24)
            save_password(next_password)
            self.assertEqual(client.get('/', auth=('ADMIN', password)).status_code, 401)
            self.assertEqual(client.get('/', auth=('ADMIN', next_password)).status_code, 200)
