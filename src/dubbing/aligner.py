"""Audio alignment utilities for building dubbed soundtracks."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence


@dataclass(frozen=True)
class SubtitleInterval:
    """Represents a single subtitle interval."""

    index: int
    start: float
    end: float
    text: str = ""

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError("Subtitle start time must be non-negative")
        if self.end <= self.start:
            raise ValueError("Subtitle end time must be greater than start time")

    @property
    def duration(self) -> float:
        """Return the interval duration in seconds."""

        return self.end - self.start


def _as_seconds(timecode: str) -> float:
    hours, minutes, rest = timecode.split(":", 2)
    seconds, millis = rest.split(",", 1)
    total = (int(hours) * 3600) + (int(minutes) * 60) + int(seconds)
    return total + int(millis) / 1000.0


def parse_srt(content: str) -> List[SubtitleInterval]:
    """Parse SRT subtitle content into :class:`SubtitleInterval` entries."""

    entries: List[SubtitleInterval] = []
    blocks = [block.strip() for block in content.strip().split("\n\n") if block.strip()]
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 2:
            continue
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue
        timing = lines[1]
        try:
            start_str, end_str = [part.strip() for part in timing.split("-->")]
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Invalid timing line: {timing!r}") from exc
        start = _as_seconds(start_str)
        end = _as_seconds(end_str)
        text = "\n".join(lines[2:])
        entries.append(SubtitleInterval(index=index, start=start, end=end, text=text))
    entries.sort(key=lambda item: (item.start, item.end, item.index))
    return entries


def _resample_channel(data: List[float], target_length: int) -> List[float]:
    if target_length <= 0:
        raise ValueError("Target length must be positive")
    current_length = len(data)
    if current_length == target_length:
        return list(data)
    if current_length == 0:
        return [0.0] * target_length
    if target_length == 1:
        return [data[0]]
    if current_length == 1:
        return [data[0]] * target_length

    step = (current_length - 1) / (target_length - 1)
    resampled: List[float] = []
    for idx in range(target_length):
        position = idx * step
        left_index = int(math.floor(position))
        right_index = min(left_index + 1, current_length - 1)
        weight = position - left_index
        left_value = data[left_index]
        right_value = data[right_index]
        resampled.append((1.0 - weight) * left_value + weight * right_value)
    return resampled


def _segment_to_channels(segment) -> List[List[float]]:
    """Convert an arbitrary audio segment into a channel-first representation."""

    if hasattr(segment, "shape") and hasattr(segment, "tolist"):
        array = segment.tolist()
        return _segment_to_channels(array)

    if not isinstance(segment, Sequence):
        raise ValueError("Audio segment must be a sequence")

    if not segment:
        return [[]]

    first = segment[0]
    if isinstance(first, Sequence) and not isinstance(first, (str, bytes, bytearray)):
        channels = [[] for _ in range(len(first))]
        for frame in segment:
            if len(frame) != len(channels):
                raise ValueError("Inconsistent channel count in segment")
            for idx, value in enumerate(frame):
                channels[idx].append(float(value))
        return channels

    return [[float(value) for value in segment]]


def _channels_to_samples(channels: List[List[float]]) -> List[List[float]]:
    if not channels:
        return []
    if len(channels) == 1:
        return [[value] for value in channels[0]]
    return [list(frame) for frame in zip(*channels)]


def align_segments_to_subtitles(
    subtitles: Sequence[SubtitleInterval],
    segments: Sequence[Sequence[float]],
    sample_rate: int,
) -> List[float] | List[List[float]]:
    """Stretch and position dubbed audio segments to match subtitle timings.

    Each input segment is resized to match its corresponding subtitle duration
    and then inserted into the final stream at the subtitle's start timestamp.
    Segments can be mono (a sequence of samples) or multi-channel (a sequence
    of sample tuples/lists).  The returned value mirrors this behaviour: a list
    of floats for mono content or a list of ``[channel_0, channel_1, ...]``
    samples for multi-channel audio.
    """

    if sample_rate <= 0:
        raise ValueError("Sample rate must be positive")
    if len(subtitles) != len(segments):
        raise ValueError("Subtitle and segment counts must match")

    subtitle_list = list(subtitles)
    if not subtitle_list:
        return []

    prepared_segments: List[List[List[float]]] = []
    num_channels: int | None = None
    for seg in segments:
        channels_representation = _segment_to_channels(seg)
        if num_channels is None:
            num_channels = len(channels_representation)
        elif len(channels_representation) != num_channels:
            raise ValueError("All segments must have the same channel count")
        prepared_segments.append([list(channel) for channel in channels_representation])
    assert num_channels is not None

    max_end = max(item.end for item in subtitle_list)
    final_length = max(1, int(math.ceil(max_end * sample_rate)))
    final_samples = [[0.0] * num_channels for _ in range(final_length)]

    for subtitle, segment in zip(subtitle_list, prepared_segments):
        target_length = max(1, int(round(subtitle.duration * sample_rate)))
        stretched_channels = [_resample_channel(channel, target_length) for channel in segment]
        samples = _channels_to_samples(stretched_channels)
        start_index = int(round(subtitle.start * sample_rate))
        end_index = start_index + target_length

        if end_index > len(final_samples):
            pad_amount = end_index - len(final_samples)
            final_samples.extend([[0.0] * num_channels for _ in range(pad_amount)])

        for idx in range(target_length):
            final_samples[start_index + idx] = list(samples[idx])

    if num_channels == 1:
        return [frame[0] for frame in final_samples]
    return final_samples

