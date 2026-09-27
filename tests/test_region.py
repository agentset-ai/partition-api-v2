import hashlib
import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import requests

from src import region
from tests.fakes import FakeResponse

OPTIONAL_KEYS = [
    "FIRECRAWL_API_KEY",
    "YOUTUBE_API_KEY",
    "PROXY_USERNAME",
    "PROXY_PASSWORD",
]


class GetRegionTest(unittest.TestCase):
    def test_defaults_to_us(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTSET_REGION", None)
            self.assertEqual(region.get_region(), "us")
            self.assertFalse(region.is_eu())

    def test_empty_value_is_us(self):
        with patch.dict(os.environ, {"AGENTSET_REGION": ""}):
            self.assertEqual(region.get_region(), "us")

    def test_eu(self):
        with patch.dict(os.environ, {"AGENTSET_REGION": "eu"}):
            self.assertEqual(region.get_region(), "eu")
            self.assertTrue(region.is_eu())

    def test_rejects_unknown_region(self):
        with patch.dict(os.environ, {"AGENTSET_REGION": "de"}):
            with self.assertRaisesRegex(ValueError, "AGENTSET_REGION"):
                region.get_region()


class ModalConfigTest(unittest.TestCase):
    def test_app_names(self):
        self.assertEqual(region.app_name("us"), "agentset-ingest-v3")
        self.assertEqual(region.app_name("eu"), "agentset-ingest-eu")

    def test_us_required_secret_keys(self):
        keys = region.required_secret_keys("us")
        self.assertEqual(
            keys,
            [
                "DATALAB_API_KEY",
                "REDIS_HOST",
                "REDIS_PORT",
                "REDIS_PASSWORD",
                "AGENTSET_API_KEY",
                "R2_ACCESS_KEY_ID",
                "R2_SECRET_ACCESS_KEY",
                "R2_BUCKET_NAME",
                "R2_CHUNKS_BUCKET_NAME",
                "R2_ENDPOINT_URL",
                "R2_PUBLIC_URL",
            ],
        )
        for key in OPTIONAL_KEYS:
            self.assertNotIn(key, keys)

    def test_eu_required_secret_keys(self):
        keys = region.required_secret_keys("eu")
        self.assertEqual(
            keys[: len(region.REQUIRED_SECRET_KEYS)], region.REQUIRED_SECRET_KEYS
        )
        self.assertIn("AGENTSET_REGION", keys)
        self.assertIn("DATALAB_PROCESSING_LOCATION", keys)
        for key in OPTIONAL_KEYS:
            self.assertNotIn(key, keys)

    def test_function_options(self):
        self.assertEqual(region.function_options("us"), {})
        self.assertEqual(region.function_options("eu"), {"region": "eu"})

    def test_web_function_options(self):
        self.assertEqual(region.web_function_options("us"), {})
        self.assertEqual(
            region.web_function_options("eu"),
            {"region": "eu", "routing_region": "eu-west"},
        )


class CheckDeployTargetTest(unittest.TestCase):
    def test_us_deploy_to_default_environment(self):
        region.check_deploy_target("us", None, "1.2.1")
        region.check_deploy_target("us", "main", "1.2.1")

    def test_us_deploy_to_eu_environment_fails(self):
        with self.assertRaisesRegex(RuntimeError, "AGENTSET_REGION=eu"):
            region.check_deploy_target("us", "eu", "1.5.5")

    def test_eu_deploy_needs_eu_environment(self):
        for environment in (None, "", "main"):
            with self.assertRaisesRegex(RuntimeError, "MODAL_ENVIRONMENT=eu"):
                region.check_deploy_target("eu", environment, "1.5.5")

    def test_eu_deploy_needs_routing_region_support(self):
        with self.assertRaisesRegex(RuntimeError, "modal>=1.4.3"):
            region.check_deploy_target("eu", "eu", "1.2.1")
        with self.assertRaisesRegex(RuntimeError, "modal>=1.4.3"):
            region.check_deploy_target("eu", "eu", "1.4.2")

    def test_eu_deploy(self):
        region.check_deploy_target("eu", "eu", "1.4.3")
        region.check_deploy_target("eu", "eu", "1.5.5")
        region.check_deploy_target("eu", "eu", "2.0.0.dev3")


class EuConfigTest(unittest.TestCase):
    env = {
        "R2_ENDPOINT_URL": "https://account.eu.r2.cloudflarestorage.com",
        "REDIS_HOST": "eu-redis.example.com",
        "DATALAB_PROCESSING_LOCATION": "eu",
    }

    def test_accepts_eu_sinks(self):
        self.assertEqual(region.eu_config_issues(self.env), [])

    def test_names_each_misconfigured_variable_without_its_value(self):
        us_host = "us-only-redis.example.com"
        us_hash = hashlib.sha256(us_host.encode()).hexdigest()
        with patch.object(region, "US_ONLY_HOST_SHA256", (us_hash,)):
            issues = region.eu_config_issues(
                {
                    "R2_ENDPOINT_URL": "https://account.r2.cloudflarestorage.com",
                    "REDIS_HOST": us_host.upper(),
                    "DATALAB_PROCESSING_LOCATION": "us",
                }
            )

        self.assertEqual(
            issues,
            [
                "R2_ENDPOINT_URL must be an EU jurisdiction R2 endpoint (*.eu.r2.cloudflarestorage.com)",
                "REDIS_HOST points to a US-only host",
                'DATALAB_PROCESSING_LOCATION must be "eu" in the EU region',
            ],
        )
        self.assertNotIn("example.com", " ".join(issues))

    def test_requires_the_sinks(self):
        self.assertEqual(
            region.eu_config_issues({}),
            [
                "R2_ENDPOINT_URL must be an EU jurisdiction R2 endpoint (*.eu.r2.cloudflarestorage.com)",
                "REDIS_HOST is required in the EU region",
                'DATALAB_PROCESSING_LOCATION must be "eu" in the EU region',
            ],
        )

    def test_rejects_lookalike_r2_hosts(self):
        for endpoint in (
            "https://eu.r2.cloudflarestorage.com.example.com",
            "https://account.r2.cloudflarestorage.com/.eu.r2.cloudflarestorage.com",
            "not a url",
        ):
            issues = region.eu_config_issues({**self.env, "R2_ENDPOINT_URL": endpoint})
            self.assertEqual(len(issues), 1, endpoint)

    def check(self, env):
        output = io.StringIO()
        with patch.dict(os.environ, env), redirect_stdout(output):
            result = region.check_eu_config()
        return result, output.getvalue()

    def test_check_is_a_no_op_on_us(self):
        result, output = self.check({"AGENTSET_REGION": "us", "REDIS_HOST": ""})
        self.assertTrue(result)
        self.assertEqual(output, "")

    def test_check_prints_the_issues_on_eu(self):
        result, output = self.check(
            {**self.env, "AGENTSET_REGION": "eu", "DATALAB_PROCESSING_LOCATION": ""}
        )
        self.assertFalse(result)
        self.assertEqual(
            output.strip(),
            'Invalid EU region configuration: DATALAB_PROCESSING_LOCATION must be "eu" in the EU region',
        )

    def test_check_passes_on_eu(self):
        result, output = self.check({**self.env, "AGENTSET_REGION": "eu"})
        self.assertTrue(result)
        self.assertEqual(output, "")


class LogErrorTest(unittest.TestCase):
    def test_logs_type_status_and_ids_only(self):
        error = requests.HTTPError(
            "403 Error for url: https://storage.example.com/doc.pdf?X-Amz-Signature=abc",
            response=FakeResponse(status_code=403),
        )
        output = io.StringIO()
        with redirect_stdout(output):
            region.log_error(
                "Failed to download file", error, namespace_id="ns_1", document_id=None
            )

        self.assertEqual(
            output.getvalue().strip(),
            "Failed to download file: HTTPError status=403 namespace_id=ns_1",
        )

    def test_omits_message(self):
        output = io.StringIO()
        with redirect_stdout(output):
            region.log_error("Failed", ValueError("document text"), job_id="job_1")

        self.assertEqual(output.getvalue().strip(), "Failed: ValueError job_id=job_1")


if __name__ == "__main__":
    unittest.main()
