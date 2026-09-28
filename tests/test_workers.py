import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from fastapi.responses import JSONResponse

from src import crawl, notify_trigger, s3, yt
from src.schema import CrawlRequest
from tests.fakes import EU, US, FakeResponse


class NotifyWorkflowTest(unittest.TestCase):
    def notify(self, env):
        with (
            patch.dict(os.environ, env),
            patch.object(
                notify_trigger.requests, "post", return_value=FakeResponse()
            ) as post,
        ):
            result = notify_trigger.notify_workflow(
                status=200,
                body={"total_chunks": 3},
                trigger_token_id="waitpoint_1",
                trigger_access_token="tr_token",
            )
        return result, post

    def test_sends_the_completion_data(self):
        for env in (US, EU):
            _, post = self.notify(env)
            post.assert_called_once_with(
                "https://api.trigger.dev/api/v1/waitpoints/tokens/waitpoint_1/complete",
                headers={"Authorization": "Bearer tr_token"},
                json={"data": {"status": 200, "total_chunks": 3}},
            )

    def test_us_returns_the_response(self):
        result, _ = self.notify(US)
        self.assertIsInstance(result, JSONResponse)
        self.assertEqual(result.body, b'{"status":200,"total_chunks":3}')

    def test_eu_returns_the_status_only(self):
        result, _ = self.notify(EU)
        self.assertEqual(result, {"status": 200})


class ImageUploadTest(unittest.TestCase):
    def upload(self, env, client):
        output = io.StringIO()
        with (
            patch.dict(os.environ, env),
            patch.object(s3, "_s3_client", client),
            patch.object(s3, "_r2_bucket", "images"),
            patch.object(s3, "_r2_public_url", "https://files.example.com/"),
            redirect_stdout(output),
        ):
            url = s3.upload_image_to_r2(
                "_page_1_Picture_0.jpeg", "aW1hZ2U=", "ns_1", "doc_1"
            )
        return url, output.getvalue()

    def test_us_upload_is_unchanged(self):
        client = MagicMock()
        url, _ = self.upload(US, client)

        kwargs = client.put_object.call_args.kwargs
        self.assertEqual(set(kwargs), {"Bucket", "Key", "Body", "ContentType"})
        self.assertEqual(kwargs["ContentType"], "image/jpeg")
        self.assertTrue(
            url.startswith("https://files.example.com/namespaces/ns_1/documents/doc_1/")
        )

    def test_eu_images_are_not_cached(self):
        client = MagicMock()
        url, _ = self.upload(EU, client)

        kwargs = client.put_object.call_args.kwargs
        self.assertEqual(kwargs["CacheControl"], "no-store")
        self.assertEqual(kwargs["ContentType"], "image/jpeg")
        self.assertTrue(
            url.startswith("https://files.example.com/namespaces/ns_1/documents/doc_1/")
        )

    def test_upload_errors(self):
        client = MagicMock()
        client.put_object.side_effect = RuntimeError(
            "bucket images key namespaces/ns_1"
        )

        url, output = self.upload(US, client)
        self.assertEqual(url, "#failed-to-upload-_page_1_Picture_0.jpeg")
        self.assertIn(
            "Error uploading image _page_1_Picture_0.jpeg: bucket images", output
        )

        url, output = self.upload(EU, client)
        self.assertEqual(url, "#failed-to-upload-_page_1_Picture_0.jpeg")
        self.assertEqual(
            output.strip(),
            "Error uploading image: RuntimeError namespace_id=ns_1 document_id=doc_1",
        )


class YouTubeWorkerTest(unittest.TestCase):
    def test_eu_worker_refuses_to_run(self):
        with (
            patch.dict(os.environ, EU),
            patch.object(yt, "notify_workflow") as notify,
        ):
            result = yt.youtube_operation.local(MagicMock())
        self.assertEqual(result, {"status": 403})
        notify.assert_not_called()


def make_crawl_request():
    return CrawlRequest(
        url="https://example.com/private?token=secret-value",
        trigger_token_id="waitpoint_1",
        trigger_access_token="token-value",
        namespace_id="ns_1",
    )


class CrawlWorkerTest(unittest.TestCase):
    def call_operation(self, arg, env, crawl_result=None, crawl_error=None):
        firecrawl = MagicMock()
        if crawl_error:
            firecrawl.crawl.side_effect = crawl_error
        else:
            firecrawl.crawl.return_value = crawl_result or MagicMock(
                status="completed", data=[]
            )
        output = io.StringIO()
        with (
            patch.dict(os.environ, env),
            patch.object(crawl, "Firecrawl", return_value=firecrawl),
            patch.object(crawl, "notify_workflow", return_value={"status": 200}) as notify,
            patch.object(crawl, "delete_job") as delete_job,
            redirect_stdout(output),
        ):
            result = crawl.crawl_operation.local(arg)
        return result, output.getvalue(), notify, delete_job

    def test_us_crawls_the_request(self):
        _, output, notify, delete_job = self.call_operation(make_crawl_request(), US)

        self.assertTrue(output.startswith("Crawl Operation:"))
        notify.assert_called_once()
        delete_job.assert_not_called()

    def test_eu_loads_the_job_and_logs_ids_only(self):
        page = MagicMock(markdown="# Page", metadata=MagicMock(language=None))
        with (
            patch.object(crawl, "load_job", return_value=make_crawl_request()) as load_job,
            patch.object(crawl, "upload_chunks_to_r2") as upload,
        ):
            _, output, notify, delete_job = self.call_operation(
                "job_1", EU, crawl_result=MagicMock(status="completed", data=[page])
            )

        load_job.assert_called_once_with("job_1", CrawlRequest)
        upload.assert_called_once()
        self.assertEqual(notify.call_args.kwargs["status"], 200)
        self.assertIn("Crawl Operation: job_id=job_1 namespace_id=ns_1", output)
        self.assertNotIn("secret-value", output)
        self.assertNotIn("token-value", output)
        delete_job.assert_called_once_with("job_1")

    def test_eu_failure_returns_a_generic_error(self):
        with patch.object(crawl, "load_job", return_value=make_crawl_request()):
            _, output, notify, delete_job = self.call_operation(
                "job_1", EU, crawl_error=RuntimeError("https://example.com/private?token=secret-value")
            )

        self.assertEqual(
            notify.call_args.kwargs["body"],
            {"message": "Failed to crawl", "code": "crawl_failed"},
        )
        self.assertIn("Failed to crawl: RuntimeError namespace_id=ns_1", output)
        self.assertNotIn("secret-value", output)
        delete_job.assert_called_once_with("job_1")

    def test_eu_missing_job(self):
        with patch.object(crawl, "load_job", return_value=None):
            result, output, notify, delete_job = self.call_operation("job_1", EU)

        self.assertIsNone(result)
        notify.assert_not_called()
        delete_job.assert_not_called()
        self.assertIn("job not found job_id=job_1", output)


if __name__ == "__main__":
    unittest.main()
