import unittest
from unittest.mock import patch

from src import jobs
from src.schema import IngestRequest
from tests.fakes import FakeRedis


def make_request(**overrides) -> IngestRequest:
    return IngestRequest(
        **{
            "url": "https://storage.example.com/doc.pdf",
            "filename": "doc.pdf",
            "trigger_token_id": "waitpoint_1",
            "trigger_access_token": "t",
            "namespace_id": "ns_1",
            "document_id": "doc_1",
            **overrides,
        }
    )


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        patcher = patch.object(jobs, "get_redis", return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_store_job_writes_request_with_3h_ttl(self):
        request = make_request()
        job_id = jobs.store_job(request)

        self.assertEqual(len(self.redis.set_calls), 1)
        call = self.redis.set_calls[0]
        self.assertEqual(call["key"], f"job:{job_id}")
        self.assertEqual(call["ex"], 3 * 60 * 60)
        self.assertEqual(IngestRequest.model_validate_json(call["value"]), request)

    def test_load_job_keeps_the_record(self):
        request = make_request()
        job_id = jobs.store_job(request)

        self.assertEqual(jobs.load_job(job_id, IngestRequest), request)
        self.assertEqual(jobs.load_job(job_id, IngestRequest), request)

    def test_delete_job(self):
        job_id = jobs.store_job(make_request())

        jobs.delete_job(job_id)

        self.assertNotIn(f"job:{job_id}", self.redis.store)
        self.assertIsNone(jobs.load_job(job_id, IngestRequest))

    def test_load_missing_job(self):
        self.assertIsNone(jobs.load_job("missing", IngestRequest))


if __name__ == "__main__":
    unittest.main()
