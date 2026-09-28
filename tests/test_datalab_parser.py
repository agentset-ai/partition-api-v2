import io
import os
import unittest
from contextlib import redirect_stdout
from io import BytesIO
from unittest.mock import patch

import requests

from src import datalab_parser
from src.errors import PartitionError
from src.file_type import ExtractedFile
from src.schema import ParseOptions
from tests.fakes import EU, US, FakeResponse, FakeSession

API = "https://www.datalab.to/api/v1"
UPLOAD_URL = "https://uploads.datalab.example/file?X-Goog-Signature=upload"
RESULT_URL = "https://results.datalab.example/result.json?X-Goog-Signature=result"


def delimiter(page: int) -> str:
    return f"\n\n{{{page}}}" + "-" * 48 + "\n\n"


def pdf_file(
    size=None, mime_type="application/pdf", extension="pdf", name="Q3 report.pdf"
):
    content = b"%PDF-1.7 test"
    return ExtractedFile(
        file=BytesIO(content),
        mime_type=mime_type,
        size_in_bytes=len(content) if size is None else size,
        file_name=name,
        extension=extension,
        url="https://storage.example.com/doc.pdf?X-Amz-Signature=abc",
    )


def eu_responses(convert_success=True, poll=None):
    return [
        FakeResponse(
            json_data={
                "file_id": 123,
                "upload_url": UPLOAD_URL,
                "expires_in": 3600,
                "reference": "datalab://file-abc",
            }
        ),
        FakeResponse(),  # PUT upload
        FakeResponse(json_data={"success": True}),  # confirm
        FakeResponse(
            json_data={
                "success": convert_success,
                "request_id": "req_1",
                "request_check_url": f"{API}/convert/req_1",
            }
        ),
        *(
            poll
            if poll is not None
            else [
                FakeResponse(json_data={"status": "processing"}),
                FakeResponse(
                    json_data={
                        "status": "complete",
                        "success": True,
                        "markdown": "",
                        "images": {},
                        "page_count": 2,
                        "result_url": RESULT_URL,
                    }
                ),
                FakeResponse(
                    json_data={
                        "status": "complete",
                        "success": True,
                        "markdown": delimiter(0)
                        + "Page one"
                        + delimiter(1)
                        + "Page two ![](_page_1_Picture_0.jpeg)",
                        "images": {"_page_1_Picture_0.jpeg": "aW1hZ2U="},
                        "page_count": None,
                    }
                ),
            ]
        ),
        FakeResponse(),  # DELETE file
    ]


class UsesDatalabFileUploadTest(unittest.TestCase):
    def test_us_keeps_the_url_flow(self):
        with patch.dict(os.environ, US):
            self.assertFalse(datalab_parser.uses_datalab_file_upload())

    def test_eu_location_uses_file_upload(self):
        with patch.dict(os.environ, EU):
            self.assertTrue(datalab_parser.uses_datalab_file_upload())

    def test_eu_region_without_eu_location_fails(self):
        with patch.dict(
            os.environ, {"AGENTSET_REGION": "eu", "DATALAB_PROCESSING_LOCATION": ""}
        ):
            with self.assertRaises(PartitionError) as context:
                datalab_parser.uses_datalab_file_upload()
        self.assertEqual(context.exception.code, "parser_not_configured")


class ParseUploadedDocumentTest(unittest.TestCase):
    def setUp(self):
        for patcher in (
            patch.dict(os.environ, EU),
            patch.object(datalab_parser.time, "sleep"),
            patch.object(datalab_parser, "_headers", {"X-API-Key": "dl_key"}),
            patch.object(
                datalab_parser,
                "upload_image_to_r2",
                return_value="https://files.example.com/namespaces/ns_1/documents/doc_1/image.jpeg",
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def parse(self, session, file=None):
        with patch.object(datalab_parser, "_session", session):
            return datalab_parser.parse_uploaded_document(
                file=file or pdf_file(),
                options=ParseOptions(mode="accurate"),
                namespace_id="ns_1",
                document_id="doc_1",
            )

    def test_uploads_converts_downloads_and_deletes(self):
        session = FakeSession(eu_responses())
        output = io.StringIO()
        with redirect_stdout(output):
            result = self.parse(session)

        self.assertEqual(
            result.pages,
            [
                {"text": "Page one", "page": 1},
                {
                    "text": "Page two ![](https://files.example.com/namespaces/ns_1/documents/doc_1/image.jpeg)",
                    "page": 2,
                },
            ],
        )
        self.assertEqual(result.page_count, 2)

        upload, put, confirm, convert, poll_1, poll_2, download, delete = session.calls

        self.assertEqual(
            (upload["method"], upload["url"]), ("POST", f"{API}/files/upload")
        )
        self.assertEqual(
            upload["json"],
            {
                "filename": "doc_1.pdf",
                "content_type": "application/pdf",
                "processing_location": "eu",
            },
        )
        self.assertEqual(upload["headers"], {"X-API-Key": "dl_key"})

        self.assertEqual((put["method"], put["url"]), ("PUT", UPLOAD_URL))
        self.assertEqual(put["data"], b"%PDF-1.7 test")
        self.assertEqual(put["headers"], {"Content-Type": "application/pdf"})

        self.assertEqual(
            (confirm["method"], confirm["url"]), ("GET", f"{API}/files/123/confirm")
        )
        self.assertEqual(confirm["headers"], {"X-API-Key": "dl_key"})

        self.assertEqual(
            (convert["method"], convert["url"]), ("POST", f"{API}/convert")
        )
        # url-encoded: Datalab rejects multipart with processing_location
        self.assertNotIn("files", convert)
        self.assertEqual(convert["data"]["file_url"], "datalab://file-abc")
        self.assertEqual(convert["data"]["processing_location"], "eu")
        self.assertEqual(convert["data"]["output_format"], "markdown")
        self.assertEqual(convert["data"]["mode"], "accurate")
        self.assertEqual(convert["data"]["paginate"], True)

        for poll in (poll_1, poll_2):
            self.assertEqual(
                (poll["method"], poll["url"]), ("GET", f"{API}/convert/req_1")
            )

        self.assertEqual((download["method"], download["url"]), ("GET", RESULT_URL))
        self.assertNotIn("headers", download)

        self.assertEqual(
            (delete["method"], delete["url"]), ("DELETE", f"{API}/files/123")
        )

        # the document's storage URL never reaches Datalab, signed links are never logged
        for call in session.calls:
            self.assertNotIn("storage.example.com", str(call))
        self.assertNotIn("Signature", output.getvalue())
        self.assertNotIn("files.example.com", output.getvalue())

    def test_deletes_the_file_when_conversion_fails(self):
        session = FakeSession(eu_responses(convert_success=False, poll=[]))
        with self.assertRaises(PartitionError) as context:
            self.parse(session)

        self.assertEqual(context.exception.code, "parse_failed")
        self.assertEqual(session.calls[-1]["method"], "DELETE")
        self.assertEqual(session.calls[-1]["url"], f"{API}/files/123")

    def test_failed_job(self):
        session = FakeSession(
            eu_responses(
                poll=[
                    FakeResponse(
                        json_data={"status": "complete", "success": False, "error": "x"}
                    )
                ]
            )
        )
        with self.assertRaises(PartitionError) as context:
            self.parse(session)

        self.assertEqual(context.exception.code, "parse_failed")
        self.assertEqual(session.calls[-1]["method"], "DELETE")

    def test_upload_error_is_not_logged_with_the_url(self):
        session = FakeSession(
            [
                FakeResponse(
                    json_data={
                        "file_id": 123,
                        "upload_url": UPLOAD_URL,
                        "reference": "datalab://file-abc",
                    }
                ),
                FakeResponse(status_code=403, url=UPLOAD_URL),
                FakeResponse(
                    status_code=500, url=f"{API}/files/123"
                ),  # DELETE fails too
            ]
        )
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(requests.HTTPError):
            self.parse(session)

        self.assertEqual(session.calls[-1]["method"], "DELETE")
        self.assertIn(
            "Failed to delete Datalab file: HTTPError status=500 file_id=123",
            output.getvalue(),
        )
        self.assertNotIn("Signature", output.getvalue())

    def test_rejects_files_over_200_mb(self):
        session = FakeSession([])
        with self.assertRaises(PartitionError) as context:
            self.parse(session, file=pdf_file(size=200 * 1024 * 1024 + 1))

        self.assertEqual(context.exception.code, "file_too_large")
        self.assertEqual(session.calls, [])

    def test_accepts_files_up_to_200_mb(self):
        session = FakeSession(eu_responses())
        with redirect_stdout(io.StringIO()):
            self.parse(session, file=pdf_file(size=200 * 1024 * 1024))
        self.assertEqual(session.calls[0]["url"], f"{API}/files/upload")


class UploadFileInfoTest(unittest.TestCase):
    def test_uses_the_document_id_as_filename(self):
        self.assertEqual(
            datalab_parser._upload_file_info(pdf_file(), "doc_1"),
            ("doc_1.pdf", "application/pdf"),
        )

    def test_content_type_from_extension(self):
        file = pdf_file(
            mime_type="application/octet-stream", extension="DOCX", name="a.DOCX"
        )
        self.assertEqual(
            datalab_parser._upload_file_info(file, "doc_1"),
            (
                "doc_1.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        )

    def test_extension_from_content_type(self):
        file = pdf_file(
            mime_type="image/jpeg; charset=binary", extension="scan", name="scan"
        )
        self.assertEqual(
            datalab_parser._upload_file_info(file, "doc_1"), ("doc_1.jpg", "image/jpeg")
        )


class MergeResultTest(unittest.TestCase):
    def test_downloaded_content_wins_over_empty_poll_fields(self):
        merged = datalab_parser._merge_result(
            {"markdown": "text", "images": {"a.png": "x"}, "page_count": None},
            {
                "status": "complete",
                "markdown": "",
                "images": {},
                "page_count": 3,
                "error": None,
            },
        )
        self.assertEqual(
            merged,
            {
                "markdown": "text",
                "images": {"a.png": "x"},
                "page_count": 3,
                "status": "complete",
            },
        )


class UsParseDocumentTest(unittest.TestCase):
    def test_posts_the_file_url_to_marker(self):
        session = FakeSession(
            [
                FakeResponse(json_data={"success": True, "request_id": "req_1"}),
                FakeResponse(
                    json_data={
                        "status": "complete",
                        "success": True,
                        "markdown": "Hello",
                        "page_count": 1,
                    }
                ),
            ]
        )
        with (
            patch.dict(os.environ, US),
            patch.object(datalab_parser, "_session", session),
            patch.object(datalab_parser, "_headers", {"X-API-Key": "dl_key"}),
        ):
            result = datalab_parser.parse_document(
                file_url="https://storage.example.com/doc.pdf",
                options=ParseOptions(),
                namespace_id="ns_1",
                document_id="doc_1",
            )

        self.assertEqual(result.pages, [{"text": "Hello", "page": 1}])
        marker, poll = session.calls
        self.assertEqual(marker["url"], f"{API}/marker")
        self.assertEqual(
            marker["files"],
            {
                "disable_image_extraction": (None, False),
                "disable_image_captions": (None, False),
                "extras": (None, None),
                "mode": (None, "balanced"),
                "additional_config": (None, None),
                "file_url": (None, "https://storage.example.com/doc.pdf"),
                "output_format": (None, "markdown"),
                "paginate": (None, True),
            },
        )
        self.assertEqual(
            list(marker["files"]),
            [
                "disable_image_extraction",
                "disable_image_captions",
                "extras",
                "mode",
                "additional_config",
                "file_url",
                "output_format",
                "paginate",
            ],
        )
        self.assertEqual(poll["url"], f"{API}/marker/req_1")


if __name__ == "__main__":
    unittest.main()
