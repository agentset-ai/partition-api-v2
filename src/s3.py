import boto3
import base64
from uuid import uuid4
import json
import os

# Initialize S3 client for Cloudflare R2
_s3_client = boto3.client(
    "s3",
    endpoint_url=os.getenv("R2_ENDPOINT_URL"),
    aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
    region_name="auto",  # R2 uses 'auto' as region
)
_r2_bucket = os.getenv("R2_BUCKET_NAME")
_r2_chunks_bucket = os.getenv("R2_CHUNKS_BUCKET_NAME")
_r2_public_url = os.getenv("R2_PUBLIC_URL")


def _get_content_type_from_filename(filename: str) -> str:
    """Determine content type from filename extension."""
    ext = filename.lower().split(".")[-1] if "." in filename else ""
    content_type_map = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "tiff": "image/tiff",
        "tif": "image/tiff",
    }
    return content_type_map.get(ext, "image/jpeg")


def upload_image_to_r2(
    image_filename: str, base64_content: str, namespace_id: str, document_id: str
) -> str:
    """
    Decodes base64 image content and uploads it to Cloudflare R2.
    Returns the public URL of the uploaded image.
    """
    try:
        # Decode the base64 content
        image_data = base64.b64decode(base64_content)

        # Determine content type from filename
        content_type = _get_content_type_from_filename(image_filename)

        # Generate a unique filename preserving the original extension
        file_ext = image_filename.split(".")[-1] if "." in image_filename else "jpg"
        unique_image_name = f"{uuid4()}.{file_ext}"

        # Create hierarchical key structure
        key = f"namespaces/{namespace_id}/documents/{document_id}/{unique_image_name}"

        # Upload to R2
        _s3_client.put_object(
            Bucket=_r2_bucket, Key=key, Body=image_data, ContentType=content_type
        )

        # Return the public URL
        public_url = f"{_r2_public_url.rstrip('/')}/{key}"
        return public_url
    except Exception as e:
        print(f"Error uploading image {image_filename}: {str(e)}")
        # Return a placeholder if upload fails
        return f"#failed-to-upload-{image_filename}"

def upload_chunks_to_r2(data: dict, namespace_id: str, document_id: str):
    _s3_client.put_object(
        Bucket=_r2_chunks_bucket,
        Key=f"namespaces/{namespace_id}/documents/{document_id}/chunks.json",
        Body=json.dumps(data),
        ContentType="application/json",
    )