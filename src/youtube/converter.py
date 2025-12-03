import time
from youtube_transcript_api import YouTubeTranscriptApi
from .utils import ExtractedVideo
from datetime import datetime, timedelta
from youtube_transcript_api.proxies import WebshareProxyConfig
import os

PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")

ytt_api = YouTubeTranscriptApi(
    proxy_config=WebshareProxyConfig(
        proxy_username=PROXY_USERNAME,
        proxy_password=PROXY_PASSWORD,
    )
)


class YouTubeConverter:
    """Handle YouTube specially, focusing on the video title, description, and transcript."""

    def _format_timestamp(self, timestamp: int) -> str:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")

    def _format_duration(self, duration: int) -> str:
        return str(timedelta(seconds=duration))

    def convert(
        self,
        video: ExtractedVideo,
        youtube_transcript_languages: list[str] | None = None,
        include_metadata: bool = True,
    ) -> str:
        # Start preparing the page
        webpage_text = "# YouTube Video\n"

        if video.title:
            webpage_text += f"## {video.title}\n"

        if include_metadata:
            stats = ""
            if video.views:
                stats += f"- **Views:** {video.views}\n"

            if video.comments:
                stats += f"- **Comments:** {video.comments}\n"

            if video.category:
                stats += f"- **Category:** {video.category}\n"

            if video.tags:
                stats += f"- **Tags:** {', '.join(video.tags)}\n"

            if video.timestamp:
                stats += f"- **Published at:** {self._format_timestamp(video.timestamp)} UTC\n"

            if video.duration:
                stats += f"- **Duration:** {self._format_duration(video.duration)}\n"

            if video.channel_name:
                stats += f"- **Channel Name:** {video.channel_name}\n"

            if len(stats) > 0:
                webpage_text += f"\n### Video Metadata\n{stats}\n"

            if video.description:
                webpage_text += f"\n### Description\n{video.description}\n"

        transcript_text = ""
        transcript_list = ytt_api.list(video.id)
        languages = ["en"]
        for transcript in transcript_list:
            languages.append(transcript.language_code)
            break

        try:
            yt_transcript_languages = (
                youtube_transcript_languages
                if youtube_transcript_languages
                else languages
            )
            # Retry the transcript fetching operation
            transcript = self._retry_operation(
                lambda: ytt_api.fetch(video.id, languages=yt_transcript_languages),
                retries=3,  # Retry 3 times
                delay=2,  # 2 seconds delay between retries
            )

            if transcript:
                transcript_text = " ".join(
                    [part.text for part in transcript]
                )  # type: ignore
        except Exception as e:
            # No transcript available
            if len(languages) == 1:
                print(f"Error fetching transcript: {e}")
            else:
                # Translate transcript into first kwarg
                transcript = (
                    transcript_list.find_transcript(languages)
                    .translate(yt_transcript_languages[0])
                    .fetch()
                )
                transcript_text = " ".join([part.text for part in transcript])

        if transcript_text:
            webpage_text += f"\n### Transcript\n{transcript_text}\n"

        return webpage_text

    def _retry_operation(self, operation, retries=3, delay=2):
        """Retries the operation if it fails."""
        attempt = 0
        while attempt < retries:
            try:
                return operation()  # Attempt the operation
            except Exception as e:
                print(f"Attempt {attempt + 1} failed: {e}")
                if attempt < retries - 1:
                    time.sleep(delay)  # Wait before retrying
                attempt += 1
        # If all attempts fail, raise the last exception
        raise Exception(f"Operation failed after {retries} attempts.")
