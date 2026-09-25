import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from prototype.app.settings import server_settings


class SettingsTests(unittest.TestCase):
    def test_private_file_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'.env'
            path.write_text('OPENAI_API_KEY=test-only\nOPENAI_MODEL=test-model\nUNRELATED=ignored\n')
            with patch('prototype.app.settings.ENV_PATH', path), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(server_settings(), {'OPENAI_API_KEY':'test-only','OPENAI_MODEL':'test-model'})
                with patch.dict(os.environ, {'OPENAI_API_KEY':''}):
                    self.assertEqual(server_settings()['OPENAI_API_KEY'], '')

    def test_missing_file_is_optional(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('prototype.app.settings.ENV_PATH', Path(folder)/'.env'), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(server_settings(), {})
