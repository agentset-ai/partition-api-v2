import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src import web_app
from src.schema import CrawlRequest, IngestRequest
from tests.fakes import EU, US

HEADERS = {"api-key": "partition_key"}
INGEST_BODY = {
    "url": "https://storage.example.com/doc.pdf?X-Amz-Signature=abc",
    "filename": "doc.pdf",
    "trigger_token_id": "waitpoint_1",
    "trigger_access_token": "tr_token",
    "namespace_id": "ns_1",
    "document_id": "doc_1",
}
CRAWL_BODY = {
    "url": "https://example.com",
    "trigger_token_id": "waitpoint_1",
    "trigger_access_token": "tr_token",
    "namespace_id": "ns_1",
}
YOUTUBE_BODY = {**CRAWL_BODY, "urls": ["https://www.youtube.com/watch?v=abc"]}


def spawnable(call_id: str) -> MagicMock:
    function = MagicMock()
    function.spawn.return_value = MagicMock(object_id=call_id)
    return function


class WebAppTestCase(unittest.TestCase):
    client = TestClient(web_app.web_app)

    def post(self, path, body, env, headers=HEADERS):
        with patch.dict(os.environ, {**env, "AGENTSET_API_KEY": "partition_key"}):
            return self.client.post(path, json=body, headers=headers)


class IngestEndpointTest(WebAppTestCase):
    def test_us_spawns_with_the_request(self):
        operation = spawnable("fc_1")
        with patch.object(web_app, "ingest_operation", operation):
            response = self.post("/ingest", INGEST_BODY, US)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"call_id": "fc_1"})
        (request,), _ = operation.spawn.call_args
        self.assertEqual(request, IngestRequest(**INGEST_BODY))

    def test_eu_stores_the_request_and_spawns_with_the_job_id(self):
        operation = spawnable("fc_1")
        with (
            patch.object(web_app, "ingest_operation", operation),
            patch.object(web_app, "store_job", return_value="job_1") as store_job,
        ):
            response = self.post("/ingest", INGEST_BODY, EU)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"call_id": "fc_1"})
        store_job.assert_called_once_with(IngestRequest(**INGEST_BODY))
        operation.spawn.assert_called_once_with("job_1")

    def test_eu_store_failure(self):
        operation = spawnable("fc_1")
        output = io.StringIO()
        with (
            patch.object(web_app, "ingest_operation", operation),
            patch.object(
                web_app, "store_job", side_effect=ConnectionError("redis down")
            ),
            redirect_stdout(output),
        ):
            response = self.post("/ingest", INGEST_BODY, EU)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"status": 503, "message": "Failed to queue job"}
        )
        operation.spawn.assert_not_called()
        self.assertIn(
            "Failed to store ingest job: ConnectionError namespace_id=ns_1 document_id=doc_1",
            output.getvalue(),
        )

    def test_eu_refuses_jobs_when_misconfigured(self):
        operation = spawnable("fc_1")
        output = io.StringIO()
        env = {**EU, "R2_ENDPOINT_URL": "https://account.r2.cloudflarestorage.com"}
        with (
            patch.object(web_app, "ingest_operation", operation),
            patch.object(web_app, "store_job") as store_job,
            redirect_stdout(output),
        ):
            response = self.post("/ingest", INGEST_BODY, env)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"status": 503, "message": "Failed to queue job"}
        )
        store_job.assert_not_called()
        operation.spawn.assert_not_called()
        self.assertIn("R2_ENDPOINT_URL must be an EU", output.getvalue())
        self.assertNotIn("account.r2", output.getvalue())

    def test_us_spawns_without_the_eu_config(self):
        operation = spawnable("fc_1")
        env = {**US, "R2_ENDPOINT_URL": "https://account.r2.cloudflarestorage.com"}
        with patch.object(web_app, "ingest_operation", operation):
            response = self.post("/ingest", INGEST_BODY, env)

        self.assertEqual(response.status_code, 200)

    def test_invalid_api_key(self):
        for env in (US, EU):
            response = self.post(
                "/ingest", INGEST_BODY, env, headers={"api-key": "wrong"}
            )
            self.assertEqual(response.status_code, 401)
            self.assertEqual(
                response.json(), {"status": 401, "message": "api-key is not valid!"}
            )


class ValidationErrorTest(WebAppTestCase):
    body = {**INGEST_BODY, "parse_options": {"mode": "document text"}}

    def test_us_returns_the_default_validation_error(self):
        response = self.post("/ingest", self.body, US)

        self.assertEqual(response.status_code, 422)
        (error,) = response.json()["detail"]
        self.assertEqual(error["input"], "document text")
        self.assertEqual(error["loc"], ["body", "parse_options", "mode"])

    def test_eu_doesnt_echo_the_input(self):
        response = self.post("/ingest", self.body, EU)

        self.assertEqual(response.status_code, 422)
        (error,) = response.json()["detail"]
        self.assertEqual(set(error), {"loc", "msg", "type"})
        self.assertEqual(error["loc"], ["body", "parse_options", "mode"])
        self.assertNotIn("document text", response.text)


class CrawlAndYouTubeTest(WebAppTestCase):
    def test_us_spawns(self):
        for path, body, name in (
            ("/crawl", CRAWL_BODY, "crawl_operation"),
            ("/youtube", YOUTUBE_BODY, "youtube_operation"),
        ):
            operation = spawnable("fc_1")
            with patch.object(web_app, name, operation):
                response = self.post(path, body, US)
            self.assertEqual(response.json(), {"call_id": "fc_1"})
            operation.spawn.assert_called_once()

    def test_eu_crawl_stores_the_request_and_spawns_with_the_job_id(self):
        operation = spawnable("fc_1")
        with (
            patch.object(web_app, "crawl_operation", operation),
            patch.object(web_app, "store_job", return_value="job_1") as store_job,
        ):
            response = self.post("/crawl", CRAWL_BODY, EU)

        self.assertEqual(response.json(), {"call_id": "fc_1"})
        store_job.assert_called_once_with(CrawlRequest(**CRAWL_BODY))
        operation.spawn.assert_called_once_with("job_1")

    def test_eu_crawl_store_failure(self):
        operation = spawnable("fc_1")
        output = io.StringIO()
        with (
            patch.object(web_app, "crawl_operation", operation),
            patch.object(
                web_app, "store_job", side_effect=ConnectionError("redis down")
            ),
            redirect_stdout(output),
        ):
            response = self.post("/crawl", CRAWL_BODY, EU)

        self.assertEqual(response.status_code, 503)
        operation.spawn.assert_not_called()
        self.assertIn(
            "Failed to store crawl job: ConnectionError namespace_id=ns_1",
            output.getvalue(),
        )

    def test_eu_youtube_returns_403(self):
        operation = spawnable("fc_1")
        with patch.object(web_app, "youtube_operation", operation):
            response = self.post("/youtube", YOUTUBE_BODY, EU)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            {"status": 403, "message": "youtube is not available in this region"},
        )
        operation.spawn.assert_not_called()


class ResultsEndpointTest(WebAppTestCase):
    def test_eu_results_endpoints_are_not_available(self):
        with patch.dict(os.environ, {**EU, "AGENTSET_API_KEY": "partition_key"}):
            for path in (
                "/ingest/results/fc_1",
                "/crawl/results/fc_1",
                "/youtube/results/fc_1",
            ):
                response = self.client.get(path, headers=HEADERS)
                self.assertEqual(response.status_code, 404)

    def test_us_results_endpoint_reads_the_call(self):
        call = MagicMock()
        call.get.return_value = {"status": 200}
        with (
            patch.dict(os.environ, {**US, "AGENTSET_API_KEY": "partition_key"}),
            patch.object(
                web_app.modal.FunctionCall, "from_id", return_value=call
            ) as from_id,
        ):
            response = self.client.get("/ingest/results/fc_1", headers=HEADERS)

        from_id.assert_called_once_with("fc_1")
        self.assertEqual(response.json(), {"status": 200})


if __name__ == "__main__":
    unittest.main()
