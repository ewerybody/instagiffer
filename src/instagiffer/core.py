"""InstaGif - central orchestrator for frame ingestion and GIF creation."""

from __future__ import annotations

import configparser
import enum
import hashlib
import logging
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from instagiffer.external.downloader import Downloader, VideoQuality
from instagiffer.external.ffmpeg import FFmpegWrapper, VideoInfo

log = logging.getLogger(__name__)

DEFAULT_TEMP_ROOT = Path(tempfile.gettempdir()) / 'instagiffer'
HASH_PREFIX_LENGTH = 16

URL_PATTERN = re.compile(r'^(https?://|www\.)', re.I)


class InputKind(enum.Enum):
    LOCAL_FILE = 'local_file'
    URL_SHORTCUT = 'url_shortcut'
    URL = 'url'


def _classify_input(source: str | Path) -> tuple[InputKind, str]:
    """
    Inspect *source* and return ``(kind, normalized_string)``.

    Resolution order:
    1. Existing ``.url`` shortcut file  →  ``URL_SHORTCUT``
    2. String matching ``http(s)://`` or ``www.``  →  ``URL``
    3. Everything else  →  ``LOCAL_FILE``
    """
    source_str = str(source)
    path = Path(source_str)

    if path.exists() and path.suffix.lower() == '.url':
        return InputKind.URL_SHORTCUT, source_str

    if URL_PATTERN.match(source_str):
        return InputKind.URL, source_str

    return InputKind.LOCAL_FILE, source_str


def _read_url_shortcut(shortcut_path: Path) -> str:
    """
    Extract the URL from a Windows ``.url`` shortcut file.

    The file is a standard INI document with an ``[InternetShortcut]`` section
    containing a ``URL=`` key.

    Raises:
        ValueError: If no URL can be found in the file.
    """
    config = configparser.ConfigParser()
    config.read(shortcut_path)

    if config.has_option('InternetShortcut', 'url'):
        return config.get('InternetShortcut', 'url').strip('"')

    defaults = config.defaults()
    if 'baseurl' in defaults:
        return defaults['baseurl'].strip('"')

    raise ValueError(f'Could not extract a URL from shortcut: {shortcut_path}')


def _make_hash(text: str) -> str:
    """Return a short, stable hex digest for *text* (path string or URL)."""
    return hashlib.sha256(text.encode()).hexdigest()[:HASH_PREFIX_LENGTH]


def _hash_for_path(video_path: Path) -> str:
    return _make_hash(str(video_path.resolve()))


def _hash_for_url(url: str) -> str:
    # Normalise: lowercase scheme+host, strip trailing slash
    normalised = url.strip().rstrip('/')
    return _make_hash(normalised)


class IngestResult:
    """Holds the outcome of a single :meth:`InstaGif.ingest` call."""

    def __init__(
        self,
        source: str,
        frames_dir: Path,
        frames: list[Path],
        video_info: VideoInfo,
        cache_hit: bool,
        input_kind: InputKind,
    ) -> None:
        self.source = source
        self.frames_dir = frames_dir
        self.frames = frames
        self.video_info = video_info
        self.cache_hit = cache_hit
        self.input_kind = input_kind

    def __repr__(self) -> str:
        origin = 'cache' if self.cache_hit else self.input_kind.value
        return f'<IngestResult {self.source!r:.60} frames={len(self.frames)} origin={origin}>'


class InstaGif:
    """
    High-level facade for the instagiffer pipeline.

    Accepts any of the following as input to :meth:`ingest`:

    * A local video file path (``Path`` or ``str``)
    * A direct video URL (``https://example.com/clip.mp4``)
    * A website URL containing a video (YouTube, Vimeo, …) - requires ``yt-dlp``
    * A Windows ``.url`` shortcut file pointing to either of the above

    Frame caching:
        A SHA-256 hash of the resolved path (for local files) or the URL (for
        remote sources) is used to create a dedicated subdirectory under
        ``temp_root``.  If ``image*.png`` files are already present there, the
        extraction step is skipped entirely.

    Usage::

        gif = InstaGif()
        result = gif.ingest(Path('clip.mp4'))
        result = gif.ingest('https://youtu.be/dQw4w9WgXcQ')
        result = gif.ingest(Path('shortcut.url'))
        print(result.frames, result.cache_hit)
    """

    def __init__(
        self,
        temp_root: Path | None = None,
        ffmpeg_path: Path | None = None,
        ytdlp_path: Path | None = None,
    ) -> None:
        self.temp_root: Path = Path(temp_root) if temp_root is not None else DEFAULT_TEMP_ROOT
        self._ffmpeg_path = ffmpeg_path
        self._ytdlp_path = ytdlp_path
        self._ffmpeg: FFmpegWrapper | None = None
        self._downloader: Downloader | None = None

    @property
    def ffmpeg(self) -> FFmpegWrapper:
        if self._ffmpeg is None:
            self._ffmpeg = FFmpegWrapper(self._ffmpeg_path)
        return self._ffmpeg

    @property
    def downloader(self) -> Downloader:
        if self._downloader is None:
            self._downloader = Downloader(self._ytdlp_path)
        return self._downloader

    def frames_dir_for(self, source_hash: str) -> Path:
        """Return the dedicated temp directory for a given hash (not yet created)."""
        return self.temp_root / source_hash

    def ingest(
        self,
        source: str | Path,
        fps: float | None = None,
        start_time: float = 0.0,
        duration: float | None = None,
        quality: VideoQuality = VideoQuality.HIGH,
        progress_callback: Callable[[float], None] | None = None,
    ) -> IngestResult:
        """
        Ingest *source*, downloading and extracting frames on demand.

        Args:
            source:            Local path, ``.url`` shortcut, or video URL.
            fps:               Frames per second to extract (``None`` = native fps).
            start_time:        Seek offset in seconds.
            duration:          Seconds to extract (``None`` = full clip).
            quality:           Download quality preset (applies to URL sources).
            progress_callback: Optional callable receiving a float 0.0-1.0.

        Returns:
            :class:`IngestResult` with frame paths, video metadata, cache flag,
            and the resolved input kind.
        """
        kind, source_str = _classify_input(source)

        if kind is InputKind.URL_SHORTCUT:
            url = _read_url_shortcut(Path(source_str))
            log.info('Resolved .url shortcut to: %s', url)
            kind = InputKind.URL
            source_str = url

        if kind is InputKind.URL:
            return self._ingest_url(
                source_str, fps, start_time, duration, quality, progress_callback
            )

        return self._ingest_file(Path(source_str), fps, start_time, duration, progress_callback)

    def _ingest_file(
        self,
        video_path: Path,
        fps: float | None,
        start_time: float,
        duration: float | None,
        progress_callback: Callable[[float], None] | None,
    ) -> IngestResult:
        if not video_path.is_file():
            raise FileNotFoundError(f'Video file not found: {video_path}')

        source_hash = _hash_for_path(video_path)
        frames_dir = self.frames_dir_for(source_hash)
        existing_frames = _find_frames(frames_dir)

        if existing_frames:
            log.info('Cache hit for "%s" (%d frames)', video_path.name, len(existing_frames))
            video_info = self.ffmpeg.get_video_info(video_path)
            return IngestResult(
                source=str(video_path),
                frames_dir=frames_dir,
                frames=existing_frames,
                video_info=video_info,
                cache_hit=True,
                input_kind=InputKind.LOCAL_FILE,
            )

        video_info = self.ffmpeg.get_video_info(video_path)
        extract_fps = fps if fps is not None else video_info.fps

        log.info('Extracting frames from "%s" at %.2f fps', video_path.name, extract_fps)
        frames = self.ffmpeg.extract_frames(
            video_path,
            output_dir=frames_dir,
            fps=extract_fps,
            start_time=start_time,
            duration=duration,
            progress_callback=progress_callback,
        )
        return IngestResult(
            source=str(video_path),
            frames_dir=frames_dir,
            frames=frames,
            video_info=video_info,
            cache_hit=False,
            input_kind=InputKind.LOCAL_FILE,
        )

    def _ingest_url(
        self,
        url: str,
        fps: float | None,
        start_time: float,
        duration: float | None,
        quality: VideoQuality,
        progress_callback: Callable[[float], None] | None,
    ) -> IngestResult:
        source_hash = _hash_for_url(url)
        frames_dir = self.frames_dir_for(source_hash)
        existing_frames = _find_frames(frames_dir)

        if existing_frames:
            log.info('Cache hit for URL (%d frames): %s', len(existing_frames), url)
            # Find the previously downloaded source file to get video info
            video_file = self._find_source_video(frames_dir)
            if video_file is None:
                # Edge case: frames exist but source file was removed; re-download
                log.warning('Cached frames found but no source video; will re-extract info only')
                video_file = _download_to_dir(self.downloader, url, frames_dir, quality, None)
            video_info = self.ffmpeg.get_video_info(video_file)
            return IngestResult(
                source=url,
                frames_dir=frames_dir,
                frames=existing_frames,
                video_info=video_info,
                cache_hit=True,
                input_kind=InputKind.URL,
            )

        # Split progress: 40% download, 60% extraction
        def download_progress(fraction: float) -> None:
            if progress_callback:
                progress_callback(fraction * 0.4)

        def extract_progress(fraction: float) -> None:
            if progress_callback:
                progress_callback(0.4 + fraction * 0.6)

        log.info('Downloading from URL: %s', url)
        video_file = _download_to_dir(self.downloader, url, frames_dir, quality, download_progress)

        video_info = self.ffmpeg.get_video_info(video_file)
        extract_fps = fps if fps is not None else video_info.fps

        log.info('Extracting frames from downloaded video at %.2f fps', extract_fps)
        frames = self.ffmpeg.extract_frames(
            video_file,
            output_dir=frames_dir,
            fps=extract_fps,
            start_time=start_time,
            duration=duration,
            progress_callback=extract_progress,
        )
        return IngestResult(
            source=url,
            frames_dir=frames_dir,
            frames=frames,
            video_info=video_info,
            cache_hit=False,
            input_kind=InputKind.URL,
        )

    def _find_source_video(self, frames_dir: Path) -> Path | None:
        """Return the downloaded source video file inside *frames_dir*, if present."""
        for candidate in frames_dir.glob('source.*'):
            if candidate.is_file():
                return candidate
        return None

    def clear_cache(self, source: str | Path) -> bool:
        """
        Delete the cached frames directory for *source*.

        Returns ``True`` if a directory was found and removed.
        """
        import shutil

        kind, source_str = _classify_input(source)
        if kind is InputKind.URL_SHORTCUT:
            source_str = _read_url_shortcut(Path(source_str))
            kind = InputKind.URL

        if kind is InputKind.URL:
            source_hash = _hash_for_url(source_str)
        else:
            source_hash = _hash_for_path(Path(source_str))

        frames_dir = self.frames_dir_for(source_hash)
        if frames_dir.is_dir():
            shutil.rmtree(frames_dir)
            log.info('Cleared cache for: %s (%s)', source_str, frames_dir)
            return True
        return False


def _find_frames(frames_dir: Path) -> list[Path]:
    """Return sorted list of extracted frame PNGs in *frames_dir*, or empty list."""
    if not frames_dir.is_dir():
        return []
    return sorted(frames_dir.glob('image*.png'))


def _download_to_dir(
    downloader: Downloader,
    url: str,
    dest_dir: Path,
    quality: VideoQuality,
    progress_callback: Callable[[float], None] | None,
) -> Path:
    return downloader.download(
        url, dest_dir=dest_dir, quality=quality, progress_callback=progress_callback
    )
