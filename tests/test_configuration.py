import os
import unittest
from unittest.mock import patch

from agentic_ops.agent import OpenRouterInvestigator
from agentic_ops.service import OperationsRuntime, REPOSITORY_ROOT, _load_openai_key_from_dotenv


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

    def test_openrouter_investigator_uses_pinned_free_model_without_api_call(self):
        with patch("agentic_ops.agent.set_tracing_disabled") as tracing, \
                patch("agentic_ops.agent.AsyncOpenAI") as client_factory:
            investigator = OpenRouterInvestigator(api_key="test-key")

        client_factory.assert_called_once_with(
            api_key="test-key", base_url="https://openrouter.ai/api/v1")
        tracing.assert_called_once_with(disabled=True)
        self.assertEqual(investigator.mode, "openrouter")
        self.assertEqual(investigator.model_name, "nvidia/nemotron-3.5-super-vl:free")

    def test_openrouter_rejects_non_free_model_id(self):
        with patch("agentic_ops.agent.AsyncOpenAI") as client_factory:
            with self.assertRaisesRegex(ValueError, "ending in :free"):
                OpenRouterInvestigator(api_key="test-key", model="provider/model")
        client_factory.assert_not_called()

    def test_runtime_selects_openrouter_without_openai_fallback(self):
        runtime = OperationsRuntime.__new__(OperationsRuntime)
        runtime._provided_investigator = None
        settings = {
            "AOI_LLM_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_MODEL": "nvidia/nemotron-3.5-super-vl:free",
        }
        with patch.dict(os.environ, {"AGENT_MODE": "openai"}, clear=True), \
                patch("agentic_ops.service._configured_setting",
                      side_effect=lambda name, default="": settings.get(name, default)), \
                patch("agentic_ops.service.OpenRouterInvestigator") as openrouter, \
                patch("agentic_ops.service.OpenAIInvestigator") as openai:
            runtime._investigator()

        openrouter.assert_called_once_with(api_key="test-key", model=settings["OPENROUTER_MODEL"])
        openai.assert_not_called()

    def test_openrouter_readiness_requires_key_and_free_model(self):
        runtime = OperationsRuntime.__new__(OperationsRuntime)
        runtime._provided_investigator = None
        settings = {
            "AOI_LLM_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_MODEL": "provider/model",
        }
        with patch.dict(os.environ, {"AGENT_MODE": "openai"}, clear=True), \
                patch("agentic_ops.service._configured_setting",
                      side_effect=lambda name, default="": settings.get(name, default)):
            self.assertFalse(runtime.investigator_configured)
            settings["OPENROUTER_MODEL"] = "nvidia/nemotron-3.5-super-vl:free"
            self.assertTrue(runtime.investigator_configured)


if __name__ == "__main__":
    unittest.main()
