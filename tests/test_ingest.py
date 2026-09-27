import io
import json
import os
import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

import requests

from src import ingest, jobs
from src.errors import PartitionError
from src.schema import IngestRequest
from tests.fakes import EU, US, FakeRedis, FakeResponse

TEXT = "# Title\n\nSome text for the document.\n\nSecond paragraph with ümlauts and more words."
TEXT_URL = "https://storage.example.com/documents/doc_1/source.txt"


def make_request(**overrides) -> IngestRequest:
    return IngestRequest(
        **{
            "filename": "doc_1.txt",
            "trigger_token_id": "waitpoint_1",
            "trigger_access_token": "t",
            "namespace_id": "ns_1",
            "document_id": "doc_1",
            **overrides,
        }
    )


class IngestTestCase(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.notify = MagicMock(
            side_effect=lambda status, body, **_: {"status": status}
        )
        self.upload_chunks = MagicMock()
        self.parse_document = MagicMock()
        self.parse_uploaded_document = MagicMock()
        self.delete_job = MagicMock()
        for patcher in (
            patch.object(ingest, "get_redis", return_value=self.redis),
            patch.object(ingest, "notify_workflow", self.notify),
            patch.object(ingest, "delete_job", self.delete_job),
            patch.object(ingest, "upload_chunks_to_r2", self.upload_chunks),
            patch.object(ingest, "parse_document", self.parse_document),
            patch.object(
                ingest, "parse_uploaded_document", self.parse_uploaded_document
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_ingest(self, request, env, download=None):
        self.redis.set_calls.clear()
        self.notify.reset_mock()
        self.upload_chunks.reset_mock()
        output = io.StringIO()
        get_patch = (
            patch("src.file_type.requests.get", return_value=download)
            if download is not None
            else nullcontext()
        )
        with (
            patch.dict(os.environ, env),
            get_patch as get,
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            ingest._ingest(request)

        kwargs = self.notify.call_args.kwargs
        return kwargs["status"], kwargs["body"], output.getvalue(), get

    def stored_chunk_texts(self):
        return [
            chunk["text"]
            for call in self.redis.set_calls
            for chunk in json.loads(call["value"])
        ]


class CompletionDataTest(IngestTestCase):
    def test_us_completion_data_is_unchanged(self):
        status, body, _, _ = self.run_ingest(make_request(text=TEXT), US)

        self.assertEqual(status, 200)
        self.assertEqual(
            body["metadata"],
            {
                "filename": "doc_1.txt",
                "filetype": "text/plain",
                "size_in_bytes": len(TEXT.encode("utf-8")),
            },
        )
        self.assertEqual(
            list(body["metadata"]), ["filename", "filetype", "size_in_bytes"]
        )
        self.assertEqual(
            list(body),
            [
                "metadata",
                "total_characters",
                "total_chunks",
                "total_batches",
                "results_id",
                "batch_template",
            ],
        )

    def test_eu_completion_data_omits_the_filename(self):
        status, body, _, _ = self.run_ingest(make_request(text=TEXT), EU)

        self.assertEqual(status, 200)
        self.assertEqual(
            body["metadata"],
            {"filetype": "text/plain", "size_in_bytes": len(TEXT.encode("utf-8"))},
        )
        self.assertEqual(
            body["batch_template"], f"results_{body['results_id']}_[BATCH_INDEX]"
        )
        self.assertGreater(body["total_chunks"], 0)


class BatchTtlTest(IngestTestCase):
    def test_us_batches_have_no_ttl(self):
        self.run_ingest(make_request(text=TEXT), US)
        self.assertTrue(self.redis.set_calls)
        for call in self.redis.set_calls:
            self.assertNotIn("ex", call)

    def test_eu_batches_expire_after_3_days(self):
        _, body, _, _ = self.run_ingest(make_request(text=TEXT), EU)
        self.assertEqual(len(self.redis.set_calls), body["total_batches"])
        for index, call in enumerate(self.redis.set_calls):
            self.assertEqual(call["key"], f"results_{body['results_id']}_{index}")
            self.assertEqual(call["ex"], 3 * 24 * 60 * 60)


class TextUrlTest(IngestTestCase):
    def download(self, content_type="text/plain; charset=utf-8"):
        return FakeResponse(
            content=TEXT.encode("utf-8"), headers={"Content-Type": content_type}
        )

    def comparable(self, body):
        return {
            k: v for k, v in body.items() if k not in ("results_id", "batch_template")
        }

    def test_eu_text_url_is_handled_like_inline_text(self):
        _, inline_body, _, _ = self.run_ingest(make_request(text=TEXT), EU)
        inline_chunks = self.stored_chunk_texts()
        inline_upload = self.upload_chunks.call_args.kwargs["data"]

        _, url_body, output, get = self.run_ingest(
            make_request(url=TEXT_URL), EU, download=self.download()
        )
        url_chunks = self.stored_chunk_texts()
        url_upload = self.upload_chunks.call_args.kwargs["data"]

        get.assert_called_once_with(TEXT_URL)
        self.assertEqual(self.comparable(url_body), self.comparable(inline_body))
        self.assertEqual(url_chunks, inline_chunks)
        self.assertEqual(
            [chunk["text"] for chunk in url_upload["chunks"]],
            [chunk["text"] for chunk in inline_upload["chunks"]],
        )
        self.assertEqual(url_body["metadata"]["filetype"], "text/plain")
        self.parse_document.assert_not_called()
        self.parse_uploaded_document.assert_not_called()
        self.assertNotIn("storage.example.com", output)

    def test_eu_text_url_without_charset(self):
        _, body, _, _ = self.run_ingest(
            make_request(url=TEXT_URL), EU, download=self.download("text/plain")
        )
        self.assertEqual(body["metadata"]["filetype"], "text/plain")
        self.parse_uploaded_document.assert_not_called()

    def test_us_url_keeps_the_content_type_header(self):
        _, body, _, _ = self.run_ingest(
            make_request(url=TEXT_URL), US, download=self.download()
        )
        self.assertEqual(body["metadata"]["filetype"], "text/plain; charset=utf-8")
        self.parse_document.assert_not_called()


class DatalabRoutingTest(IngestTestCase):
    def pdf_download(self):
        return FakeResponse(
            content=b"%PDF-1.7", headers={"Content-Type": "application/pdf"}
        )

    def test_us_passes_the_url_to_datalab(self):
        self.parse_document.return_value = MagicMock(
            pages=[{"text": "Hello", "page": 1}], page_count=1
        )
        request = make_request(url=TEXT_URL, filename="doc.pdf")
        _, body, _, _ = self.run_ingest(request, US, download=self.pdf_download())

        self.parse_document.assert_called_once()
        self.assertEqual(self.parse_document.call_args.kwargs["file_url"], TEXT_URL)
        self.parse_uploaded_document.assert_not_called()
        self.assertEqual(body["total_pages"], 1)

    def test_eu_uploads_the_file_to_datalab(self):
        self.parse_uploaded_document.return_value = MagicMock(
            pages=[{"text": "Hello", "page": 1}], page_count=1
        )
        request = make_request(url=TEXT_URL, filename="doc.pdf")
        _, body, _, _ = self.run_ingest(request, EU, download=self.pdf_download())

        self.parse_uploaded_document.assert_called_once()
        self.assertEqual(
            self.parse_uploaded_document.call_args.kwargs["file"].mime_type,
            "application/pdf",
        )
        self.parse_document.assert_not_called()
        self.assertEqual(body["total_pages"], 1)


class ErrorTest(IngestTestCase):
    def failed_download(self):
        return patch(
            "src.file_type.requests.get",
            side_effect=requests.HTTPError(f"403 Error for url: {TEXT_URL}"),
        )

    def test_us_download_error_is_unchanged(self):
        with self.failed_download():
            status, body, _, _ = self.run_ingest(make_request(url=TEXT_URL), US)
        self.assertEqual(status, 400)
        self.assertEqual(list(body), ["message"])
        self.assertTrue(body["message"].startswith("Failed to download file: "))

    def test_eu_download_error_is_generic(self):
        with self.failed_download():
            status, body, output, _ = self.run_ingest(make_request(url=TEXT_URL), EU)
        self.assertEqual(status, 400)
        self.assertEqual(
            body, {"message": "Failed to download file", "code": "download_failed"}
        )
        self.assertIn(
            "Failed to download file: HTTPError namespace_id=ns_1 document_id=doc_1",
            output,
        )
        self.assertNotIn("storage.example.com", output)

    def test_us_processing_error_is_unchanged(self):
        with patch.object(ingest, "chunk_documents", side_effect=ValueError("x")):
            status, body, _, _ = self.run_ingest(make_request(text=TEXT), US)
        self.assertEqual(status, 500)
        self.assertEqual(list(body), ["message"])

    def test_eu_processing_error_is_generic(self):
        with patch.object(
            ingest, "chunk_documents", side_effect=ValueError("document text")
        ):
            status, body, output, _ = self.run_ingest(make_request(text=TEXT), EU)
        self.assertEqual(status, 500)
        self.assertEqual(
            body, {"message": "Failed to process document", "code": "processing_failed"}
        )
        self.assertIn(
            "Failed to process document: ValueError namespace_id=ns_1 document_id=doc_1",
            output,
        )
        self.assertNotIn("document text", output)
        self.assertNotIn("Traceback", output)

    def test_eu_partition_error_code(self):
        error = PartitionError(
            "file_too_large", "File exceeds the 200 MB limit for document parsing"
        )
        with patch.object(ingest, "chunk_documents", side_effect=error):
            _, body, _, _ = self.run_ingest(make_request(text=TEXT), EU)
        self.assertEqual(
            body,
            {
                "message": "File exceeds the 200 MB limit for document parsing",
                "code": "file_too_large",
            },
        )


class OperationTest(IngestTestCase):
    def call_operation(self, arg, env):
        output = io.StringIO()
        with (
            patch.dict(os.environ, env),
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            result = ingest.ingest_operation.local(arg)
        return result, output.getvalue()

    def test_us_ingests_the_request(self):
        _, output = self.call_operation(make_request(text=TEXT), US)

        self.assertTrue(output.startswith("Ingest Operation:"))
        self.assertEqual(self.notify.call_args.kwargs["status"], 200)
        self.delete_job.assert_not_called()

    def test_eu_loads_the_job_and_logs_ids_only(self):
        request = make_request(text=TEXT, trigger_access_token="token-value")
        with patch.object(ingest, "load_job", return_value=request) as load_job:
            result, output = self.call_operation("job_1", EU)

        load_job.assert_called_once_with("job_1", IngestRequest)
        self.assertEqual(result, {"status": 200})
        self.assertIn(
            "Ingest Operation: job_id=job_1 namespace_id=ns_1 document_id=doc_1", output
        )
        self.assertNotIn("Some text", output)
        self.assertNotIn("token-value", output)
        self.delete_job.assert_called_once_with("job_1")

    def test_eu_missing_job(self):
        with patch.object(ingest, "load_job", return_value=None):
            result, output = self.call_operation("job_1", EU)

        self.assertIsNone(result)
        self.notify.assert_not_called()
        self.delete_job.assert_not_called()
        self.assertIn("job not found job_id=job_1", output)

    def test_eu_refuses_jobs_when_misconfigured(self):
        with patch.object(ingest, "load_job") as load_job:
            result, output = self.call_operation(
                "job_1", {**EU, "DATALAB_PROCESSING_LOCATION": "us"}
            )

        self.assertEqual(result, {"status": 500})
        load_job.assert_not_called()
        self.notify.assert_not_called()
        self.assertIn("DATALAB_PROCESSING_LOCATION must be", output)

    def test_eu_retried_job_still_runs(self):
        request = make_request(text=TEXT)
        with (
            patch.object(jobs, "get_redis", return_value=self.redis),
            patch.object(ingest, "delete_job", jobs.delete_job),
        ):
            job_id = jobs.store_job(request)
            # a first attempt loaded the job, then its container failed
            jobs.load_job(job_id, IngestRequest)

            result, _ = self.call_operation(job_id, EU)

        self.assertEqual(result, {"status": 200})
        self.assertEqual(self.notify.call_args.kwargs["status"], 200)
        self.assertNotIn(jobs.job_key(job_id), self.redis.store)

    def test_eu_unexpected_error_notifies_the_workflow(self):
        with (
            patch.object(ingest, "load_job", return_value=make_request(text=TEXT)),
            patch.object(ingest, "_ingest", side_effect=RuntimeError("chunk text")),
        ):
            result, output = self.call_operation("job_1", EU)

        self.assertEqual(result, {"status": 500})
        self.notify.assert_called_once_with(
            status=500,
            body={"message": "Failed to process document", "code": "processing_failed"},
            trigger_token_id="waitpoint_1",
            trigger_access_token="t",
        )
        self.delete_job.assert_called_once_with("job_1")
        self.assertNotIn("chunk text", output)

    def test_eu_unexpected_error(self):
        self.notify.side_effect = requests.HTTPError(
            "500 Error for url: https://api.trigger.dev/x"
        )
        with patch.object(ingest, "load_job", return_value=make_request(text=TEXT)):
            result, output = self.call_operation("job_1", EU)

        self.assertEqual(result, {"status": 500})
        self.assertIn(
            "Ingest Operation failed: HTTPError job_id=job_1 namespace_id=ns_1 document_id=doc_1",
            output,
        )
        self.assertIn(
            "Failed to notify the workflow: HTTPError job_id=job_1 namespace_id=ns_1 document_id=doc_1",
            output,
        )
        self.delete_job.assert_called_once_with("job_1")
        self.assertNotIn("Traceback", output)


if __name__ == "__main__":
    unittest.main()
