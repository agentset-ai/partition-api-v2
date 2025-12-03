from fastapi import status
from .app import app
from .notify_trigger import notify_workflow
from .schema import YouTubeRequest
from .chunker import chunk_documents
from .youtube.utils import (
    extract_url,
    ExtractedVideo,
    ExtractedPlaylist,
)
from .youtube.converter import YouTubeConverter
from typing import Callable
from cuid2 import cuid_wrapper
from .s3 import upload_chunks_to_r2

yt_converter = YouTubeConverter()


@app.function(timeout=7200)  # 2 hours
def youtube_operation(request: YouTubeRequest):
    print("YT Operation:")
    print(request.model_dump_json(indent=2))
    cuid_generator: Callable[[], str] = cuid_wrapper()
    video_ids: dict[str, ExtractedVideo] = {}

    try:
        for url in request.urls:
            result = extract_url(url)
            if isinstance(result, ExtractedVideo):
                video_ids[result.id] = result
            elif isinstance(result, ExtractedPlaylist):
                for video in result.videos:
                    video_ids[video.id] = video
    except Exception as e:
        return notify_workflow(
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            body={"message": str(e)},
            trigger_token_id=request.trigger_token_id,
            trigger_access_token=request.trigger_access_token,
        )

    videos_to_process = list(video_ids.values())

    try:
        if len(videos_to_process) == 0:
            return notify_workflow(
                status=status.HTTP_400_BAD_REQUEST,
                body={"message": "Invalid YouTube URL or could not extract info"},
                trigger_token_id=request.trigger_token_id,
                trigger_access_token=request.trigger_access_token,
            )

        documents = []
        failed_videos = []

        videos_data = [
            (
                video,
                yt_converter.convert(
                    video, request.transcript_languages, request.include_metadata
                ),
            )
            for video in videos_to_process
        ]

        for video, video_markdown in videos_data:
            if video_markdown is None:
                failed_videos.append(video)
                continue

            chunks, total_characters, total_chunks, _ = chunk_documents(
                documents=[{"text": video_markdown}],
                batch_size=None,
                chunk_options=request.chunk_options,
            )

            documents.append(
                {
                    "id": cuid_generator(),
                    "chunks": chunks,
                    "video_id": video.id,
                    "title": video.title,
                    "video_metadata": {
                        "video_id": video.id,
                        "url": video.url,
                        "title": video.title,
                        "description": video.description,
                        "tags": video.tags,
                        "category": video.category,
                        "timestamp": video.timestamp,
                        "channel_id": video.channel_id,
                        "channel_name": video.channel_name,
                        "views": video.views,
                        "comments": video.comments,
                        "duration": video.duration,
                    },
                    "total_bytes": len(video_markdown),
                    "total_characters": total_characters,
                    "total_chunks": total_chunks,
                }
            )

        if len(documents) == 0:
            return notify_workflow(
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                body={
                    "message": "No transcripts found for any video",
                    "failed_videos": failed_videos,
                },
                trigger_token_id=request.trigger_token_id,
                trigger_access_token=request.trigger_access_token,
            )

        for doc in documents:
            metadata = {
                **(request.extra_metadata or {}),
                "namespaceId": request.namespace_id,
                "documentId": doc["id"],
            }

            video_metadata = doc["video_metadata"]
            if video_metadata["video_id"]:
                metadata["youtube_id"] = video_metadata["video_id"]
            if video_metadata["title"]:
                metadata["youtube_title"] = video_metadata["title"]
            if video_metadata["description"]:
                metadata["youtube_description"] = video_metadata["description"]
            if video_metadata["duration"]:
                metadata["youtube_duration"] = video_metadata["duration"]
            if video_metadata["timestamp"]:
                metadata["youtube_timestamp"] = video_metadata["timestamp"]

            upload_chunks_to_r2(
                namespace_id=request.namespace_id,
                document_id=doc["id"],
                data={
                    "metadata": metadata,
                    "total_chunks": doc["total_chunks"],
                    "total_characters": doc["total_characters"],
                    "chunks": doc["chunks"],
                },
            )

            del doc["chunks"]
            del doc["video_metadata"]

        response_body = {"documents": documents}
        if len(failed_videos) > 0:
            response_body["failed_videos"] = failed_videos

        return notify_workflow(
            status=status.HTTP_200_OK,
            body=response_body,
            trigger_token_id=request.trigger_token_id,
            trigger_access_token=request.trigger_access_token,
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return notify_workflow(
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            body={"message": str(e)},
            trigger_token_id=request.trigger_token_id,
            trigger_access_token=request.trigger_access_token,
        )
