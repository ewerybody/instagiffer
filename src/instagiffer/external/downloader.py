"""Downloader - fetch videos from URLs via yt-dlp or direct HTTP."""

from __future__ import annotations

import enum
import logging
import re
import shutil
import subprocess
import urllib.request
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

# Video file extensions considered "direct" download links (no site scraping needed)
DIRECT_VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.m4v'}

# yt-dlp output template: let it pick the extension, we glob for it afterward
YTDLP_OUTPUT_TEMPLATE = 'source.%(ext)s'

# YouTube playlist query param to strip
PLAYLIST_PARAM_PATTERN = re.compile(r'&list=[^&]*', re.I)


class VideoQuality(enum.Enum):
    """Download quality presets mapped to yt-dlp format selectors."""

    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    HIGHEST = 'highest'

    def ytdlp_format(self) -> str:
        if self is VideoQuality.LOW:
            return 'bestvideo[height<=?240][ext=mp4]/bestvideo[height<=?240]'
        if self is VideoQuality.MEDIUM:
            return 'bestvideo[height<=?480][ext=mp4]/bestvideo[height<=?480]'
        if self is VideoQuality.HIGH:
            return 'bestvideo[height<=?720][ext=mp4]/bestvideo[height<=?720]'
        return 'bestvideo[ext=mp4]/bestvideo'


class DownloadError(Exception):
    """Raised when a download fails for any reason."""


class YtDlpNotFoundError(DownloadError):
    """Raised when yt-dlp cannot be located."""


def _strip_playlist_param(url: str) -> str:
    """Remove YouTube playlist query parameters so only the single video is fetched."""
    cleaned = PLAYLIST_PARAM_PATTERN.sub('', url)
    if cleaned != url:
        log.info('Stripped playlist param from URL: %s', cleaned)
    return cleaned


def _is_direct_video_url(url: str) -> bool:
    """Return True if the URL path ends with a known video file extension."""
    path_part = url.split('?')[0].split('#')[0]
    suffix = Path(path_part).suffix.lower()
    return suffix in DIRECT_VIDEO_EXTENSIONS


class Downloader:
    """
    Download videos from URLs.

    Delegates to ``yt-dlp`` for website URLs (YouTube, Vimeo, etc.) and
    falls back to ``urllib`` for plain direct video file URLs when yt-dlp
    is unavailable.

    Usage::

        dl = Downloader()
        video_path = dl.download('https://youtu.be/dQw4w9WgXcQ', dest_dir=Path('/tmp/x'))
    """

    def __init__(self, ytdlp_path: Path | None = None) -> None:
        self._ytdlp_path: Path | None = ytdlp_path
        self._ytdlp: Path | None = None
        self._ytdlp_resolved = False

    @property
    def ytdlp(self) -> Path | None:
        """Lazily locate yt-dlp; returns None if not found (not an error)."""
        if not self._ytdlp_resolved:
            self._ytdlp_resolved = True
            if self._ytdlp_path is not None:
                self._ytdlp = Path(self._ytdlp_path)
            else:
                found = shutil.which('yt-dlp') or shutil.which('yt_dlp')
                self._ytdlp = Path(found) if found else None
            if self._ytdlp:
                log.debug('Using yt-dlp: %s', self._ytdlp)
            else:
                log.debug('yt-dlp not found in PATH')
        return self._ytdlp

    def download(
        self,
        url: str,
        dest_dir: Path,
        quality: VideoQuality = VideoQuality.HIGH,
        progress_callback: Callable[[float], None] | None = None,
    ) -> Path:
        """
        Download the video at *url* into *dest_dir*.

        Tries yt-dlp first.  Falls back to direct urllib download for plain
        video file URLs when yt-dlp is not installed.

        Args:
            url:               Source URL.
            dest_dir:          Directory to write the downloaded file into.
            quality:           Desired quality preset (ignored for direct URLs).
            progress_callback: Optional callable receiving a float 0.0-1.0.

        Returns:
            Path to the downloaded video file.

        Raises:
            DownloadError:       On failure.
            YtDlpNotFoundError:  When yt-dlp is absent and the URL needs scraping.
        """
        url = _strip_playlist_param(url)
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        if self.ytdlp is not None:
            return self._download_ytdlp(url, dest_dir, quality, progress_callback)

        if _is_direct_video_url(url):
            log.info('yt-dlp not available; falling back to direct download for: %s', url)
            return self._download_direct(url, dest_dir, progress_callback)

        raise YtDlpNotFoundError(
            'yt-dlp is required to download from this URL but was not found. '
            'Install it with:  pip install yt-dlp'
        )

    def _download_ytdlp(
        self,
        url: str,
        dest_dir: Path,
        quality: VideoQuality,
        progress_callback: Callable[[float], None] | None,
    ) -> Path:
        assert self.ytdlp is not None

        output_template = str(dest_dir / YTDLP_OUTPUT_TEMPLATE)
        cmd = [
            str(self.ytdlp),
            '--no-check-certificates',
            '--newline',
            '--format', quality.ytdlp_format(),
            '--output', output_template,
            url,
        ]
        log.debug('yt-dlp cmd: %s', ' '.join(cmd))

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert process.stdout is not None

        for line in process.stdout:
            line = line.rstrip()
            log.debug('yt-dlp: %s', line)
            if progress_callback:
                match = re.search(r'\[download\]\s+(\d+(?:\.\d+)?)%', line)
                if match:
                    progress_callback(float(match.group(1)) / 100.0)

        process.wait()

        if process.returncode != 0:
            raise DownloadError(
                f'yt-dlp exited with code {process.returncode} for URL: {url}'
            )

        downloaded = self._find_downloaded_file(dest_dir)
        if downloaded is None:
            raise DownloadError(f'yt-dlp ran but no video file found in: {dest_dir}')

        if progress_callback:
            progress_callback(1.0)

        log.info('Downloaded via yt-dlp: %s', downloaded)
        return downloaded

    def _download_direct(
        self,
        url: str,
        dest_dir: Path,
        progress_callback: Callable[[float], None] | None,
    ) -> Path:
        """Download a direct video URL using urllib."""
        suffix = Path(url.split('?')[0]).suffix or '.mp4'
        dest_file = dest_dir / f'source{suffix}'

        log.info('Direct download: %s -> %s', url, dest_file)

        def _reporthook(block_count: int, block_size: int, total_size: int) -> None:
            if progress_callback and total_size > 0:
                progress_callback(min(block_count * block_size / total_size, 1.0))

        try:
            urllib.request.urlretrieve(url, dest_file, reporthook=_reporthook)
        except Exception as exc:
            raise DownloadError(f'Direct download failed for {url}: {exc}') from exc

        if progress_callback:
            progress_callback(1.0)

        log.info('Downloaded directly: %s', dest_file)
        return dest_file

    def _find_downloaded_file(self, dest_dir: Path) -> Path | None:
        """Return the first video file matching `source.*` in *dest_dir*."""
        candidates = sorted(dest_dir.glob('source.*'))
        for candidate in candidates:
            if candidate.suffix.lower() in DIRECT_VIDEO_EXTENSIONS:
                return candidate
        # yt-dlp may produce a container we didn't list; return first match anyway
        return candidates[0] if candidates else None
