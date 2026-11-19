import os
import unittest
from unittest.mock import patch

from agentic_ops.service import REPOSITORY_ROOT, _load_openai_key_from_dotenv


class DotenvConfigurationTests(unittest.TestCase):
    def test_loads_only_openai_key_from_project_env(self):
        values = {
            "OPENAI_API_KEY": "from-dotenv",
            "ANOTHER_SECRET": "must-not-be-exported",
        }
        with patch.dict(os.environ, {}, clear=True):
            with patch("agentic_ops.service.dotenv_values", return_value=values) as read_env:
                _load_openai_key_from_dotenv()
            read_env.assert_called_once_with(REPOSITORY_ROOT / ".env")
            self.assertEqual(os.environ.get("OPENAI_API_KEY"), "from-dotenv")
            self.assertNotIn("ANOTHER_SECRET", os.environ)

    def test_existing_environment_key_takes_precedence(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "from-shell"}, clear=True):
            with patch("agentic_ops.service.dotenv_values") as read_env:
                _load_openai_key_from_dotenv()
            read_env.assert_not_called()
            self.assertEqual(os.environ.get("OPENAI_API_KEY"), "from-shell")


if __name__ == "__main__":
    unittest.main()
