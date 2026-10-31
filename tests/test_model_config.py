import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trippilot.models.config import (  # noqa: E402
    ConfigError,
    load_dotenv,
    resolve_model_config,
)


class ModelConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env_path = Path(self.tmp.name) / ".env"

    def tearDown(self):
        self.tmp.cleanup()

    def write_env(self, text):
        self.env_path.write_text(text, encoding="utf-8")

    def test_dotenv_parses_quotes_comments_and_export(self):
        self.write_env(
            "# comment\n"
            "export OPENAI_API_KEY='file-key'\n"
            'OPENAI_BASE_URL="https://example.invalid/v1"\n'
        )
        env = {}
        load_dotenv(self.env_path, env)
        self.assertEqual(env["OPENAI_API_KEY"], "file-key")
        self.assertEqual(env["OPENAI_BASE_URL"], "https://example.invalid/v1")

    def test_dotenv_does_not_override_real_environment(self):
        self.write_env("OPENAI_API_KEY=file-key\n")
        env = {"OPENAI_API_KEY": "real-key"}
        load_dotenv(self.env_path, env)
        self.assertEqual(env["OPENAI_API_KEY"], "real-key")

    def test_resolve_reads_env_file(self):
        self.write_env(
            "OPENAI_API_KEY=test-key\n"
            "OPENAI_BASE_URL=https://example.invalid/v1\n"
            "CHINATRAVEL_OPENAI_MODEL=test/model\n"
        )
        config = resolve_model_config(environ={}, env_file=self.env_path)
        self.assertEqual(config.model, "test/model")
        self.assertEqual(config.base_url_host, "example.invalid")
        self.assertNotIn("test-key", repr(config))

    def test_resolve_requires_model_and_key(self):
        with self.assertRaises(ConfigError):
            resolve_model_config(environ={}, env_file=self.env_path)
        self.write_env("CHINATRAVEL_OPENAI_MODEL=test/model\n")
        with self.assertRaises(ConfigError):
            resolve_model_config(environ={}, env_file=self.env_path)


if __name__ == "__main__":
    unittest.main()
