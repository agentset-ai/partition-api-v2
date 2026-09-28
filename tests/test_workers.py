import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from fastapi.responses import JSONResponse

from src import crawl, notify_trigger, s3, yt
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


class CrawlAndYouTubeWorkerTest(unittest.TestCase):
    def test_eu_workers_refuse_to_run(self):
        for operation, module in (
            (crawl.crawl_operation, crawl),
            (yt.youtube_operation, yt),
        ):
            with (
                patch.dict(os.environ, EU),
                patch.object(module, "notify_workflow") as notify,
            ):
                result = operation.local(MagicMock())
            self.assertEqual(result, {"status": 403})
            notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
