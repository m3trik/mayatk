# mayatk — API Changes

_Diff vs the last release (origin/main @ b5a23f0)._

## Removed (9)

- `anim_utils/playblast_exporter.py::CaptureResult` — was `(class)`
- `anim_utils/playblast_exporter.py::CaptureResult.pattern` — was `(self) -> str`
- `anim_utils/playblast_exporter.py::ExportResult` — was `(class)`
- `anim_utils/playblast_exporter.py::ExportResult.ok` — was `(self) -> bool`
- `anim_utils/playblast_exporter.py::ExportTarget` — was `(class)`
- `anim_utils/playblast_exporter.py::PlayblastExporter.available_targets` — was `(cls) -> List[Tuple[str, str]]`
- `anim_utils/playblast_exporter.py::PlayblastExporter.encode_sequence` — was `(self, capture: Union[CaptureResult, str], output_filepath: str, fps: Optional[float] = None, audio: Optional[Union[bool, str]] = None, quality: Optional[int] = None, **ffmpeg_options: Any) -> str`
- `anim_utils/playblast_exporter.py::PlayblastExporter.export` — was `(self, output_dir: str, name: Optional[str] = None, targets: Union[str, Sequence[str]] = ('mp4',), range_mode: str = 'playback', start: Optional[int] = None, end: Optional[int] = None, camera: Optional[str] = None, keep_frames: bool = False, progress_callback: Optional[Callable[[int, int, str], None]] = None, **overrides: Any) -> List[ExportResult]`
- `anim_utils/playblast_exporter.py::PlayblastExporter.resolve_frame_range` — was `(cls, mode: str = 'playback', start: Optional[int] = None, end: Optional[int] = None) -> Tuple[int, int]`

## Added (2)

- `anim_utils/playblast_exporter.py::PlayblastExporter.sequence_fps(self) -> float`
- `anim_utils/playblast_exporter.py::PlayblastExporter.sequence_name(self) -> str`
