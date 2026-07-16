from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_console.models import ModelCatalogue, estimate_models, lowest_cost_model, parse_verbose_models


def record(model_id: str, output: float | None, input_cost: float | None, **extra):
    value = {
        "id": model_id, "providerID": "openrouter", "name": model_id,
        "status": extra.get("status", "active"),
        "cost": {"input": input_cost, "output": output, "cache": {"read": extra.get("cache")}},
        "limit": {"context": 1000, "output": 100},
        "capabilities": {"reasoning": True, "attachment": False, "toolcall": True},
    }
    return f"openrouter/{model_id}\n{json.dumps(value)}\n"


class ModelTests(unittest.TestCase):
    def test_parse_sort_unknown_and_deprecated(self):
        models = parse_verbose_models(
            record("expensive", 2.0, 1.0) + record("free", 0.0, 0.0)
            + record("unknown", None, None) + record("old", 1.0, 1.0, status="deprecated"),
            "openrouter",
        )
        self.assertEqual([m["id"] for m in models], ["free", "old", "expensive", "unknown"])
        self.assertFalse(models[1]["selectable"])

    def test_estimate_fallbacks_and_cheapest(self):
        models = parse_verbose_models(record("a", 2, 1) + record("b", 1, 3, cache=0.1), "openrouter")
        result = estimate_models(models, uncached_input_tokens=1000, cached_input_tokens=1000, output_tokens=1000, reasoning_tokens=1000)
        self.assertTrue(any(m["cheapest"] for m in result))
        self.assertAlmostEqual(next(m for m in result if m["id"] == "a")["estimated_usd"], 0.006)

    def test_lowest_cost_default_skips_unselectable_and_unknown(self):
        models = parse_verbose_models(
            record("unknown", None, None)
            + record("deprecated-free", 0, 0, status="deprecated")
            + record("expensive", 2, 1)
            + record("cheapest", 0.25, 0.1),
            "openrouter",
        )
        self.assertEqual(lowest_cost_model(models)["id"], "cheapest")

    def test_catalogue_cache_and_failed_refresh_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            def runner(*args, **kwargs):
                calls.append(1)
                return subprocess.CompletedProcess(args[0], 0, record("one", 1, 1), "") if len(calls) == 1 else subprocess.CompletedProcess(args[0], 1, "", "failed")
            catalogue = ModelCatalogue(Path(temp), executable="opencode", ttl_seconds=0, runner=runner)
            self.assertFalse(catalogue.list("openrouter", refresh=True)["stale"])
            stale = catalogue.list("openrouter", refresh=True)
            self.assertTrue(stale["stale"])
            self.assertNotIn("failed", json.dumps(stale))


if __name__ == "__main__":
    unittest.main()
