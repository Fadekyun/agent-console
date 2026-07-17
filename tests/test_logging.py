from __future__ import annotations

import io
import logging
import os
import re
import unittest

from agent_console.logging_config import configure_logging, get_logger, reset_logging


class LoggingConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_logging()
        self.capture = io.StringIO()
        self.handler = logging.StreamHandler(self.capture)
        self.handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )

    def tearDown(self) -> None:
        reset_logging()

    def test_configure_logging_adds_handler(self) -> None:
        configure_logging()
        logger = get_logger("test_logger")
        root = logging.getLogger()
        self.assertGreater(len(root.handlers), 0)

    def test_logging_respects_log_level_env(self) -> None:
        os.environ["AGENT_CONSOLE_LOG_LEVEL"] = "WARNING"
        try:
            configure_logging()
        finally:
            del os.environ["AGENT_CONSOLE_LOG_LEVEL"]

    def test_logger_emits_expected_format(self) -> None:
        logging.getLogger().addHandler(self.handler)
        test_log = logging.getLogger("test.capture")
        test_log.setLevel(logging.DEBUG)
        test_log.info("session=%s action=create tool=%s", "test-session", "codex")
        output = self.capture.getvalue()
        self.assertIn("info", output.lower())
        self.assertIn("session=test-session", output)
        self.assertIn("action=create", output)
        self.assertIn("tool=codex", output)

    def test_logger_emits_key_value_pairs(self) -> None:
        logging.getLogger().addHandler(self.handler)
        test_log = logging.getLogger("test.kv")
        test_log.setLevel(logging.DEBUG)
        test_log.info("session=%s action=kill managed=%s", "kill-me", True)
        output = self.capture.getvalue()
        self.assertIn("session=kill-me", output)
        self.assertIn("action=kill", output)


if __name__ == "__main__":
    unittest.main()
