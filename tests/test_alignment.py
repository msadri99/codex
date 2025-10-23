import math

from dubbing.aligner import SubtitleInterval, align_segments_to_subtitles


def build_interval(index, start, end):
    return SubtitleInterval(index=index, start=start, end=end, text=f"line {index}")


def test_align_segments_matches_subtitle_timing():
    sample_rate = 100
    subtitles = [
        build_interval(1, 1.0, 3.0),
        build_interval(2, 4.0, 5.0),
    ]
    seg1 = [1.0] * int(sample_rate * 0.5)
    seg2 = [0.5] * int(sample_rate * 1.5)

    result = align_segments_to_subtitles(subtitles, [seg1, seg2], sample_rate)

    assert len(result) == sample_rate * 5
    assert all(abs(x) < 1e-9 for x in result[:sample_rate])
    assert all(abs(x - 1.0) < 1e-6 for x in result[sample_rate : sample_rate * 3])
    assert all(abs(x) < 1e-9 for x in result[sample_rate * 3 : sample_rate * 4])
    assert all(abs(x - 0.5) < 1e-6 for x in result[sample_rate * 4 : sample_rate * 5])


def test_align_segments_handles_stereo_segments():
    sample_rate = 20
    subtitles = [build_interval(1, 0.5, 1.5)]
    frame_count = int(sample_rate * 0.5)
    mono = [(-1.0 + (2.0 * idx) / (frame_count - 1)) for idx in range(frame_count)]
    stereo = [[value, -value] for value in mono]

    result = align_segments_to_subtitles(subtitles, [stereo], sample_rate)

    expected_length = max(1, int(round(subtitles[0].duration * sample_rate)))
    assert len(result) == int(math.ceil(subtitles[0].end * sample_rate))
    leading_silence = int(round(subtitles[0].start * sample_rate))
    assert all(sample == [0.0, 0.0] for sample in result[:leading_silence])
    aligned = result[leading_silence : leading_silence + expected_length]
    assert all(len(sample) == 2 for sample in aligned)
    assert any(abs(sample[0]) > 0 for sample in aligned)
    assert all(abs(sample[0] + sample[1]) < 1e-6 for sample in aligned)
    assert all(sample == [0.0, 0.0] for sample in result[leading_silence + expected_length :])


def test_align_segments_validates_length_mismatch():
    sample_rate = 10
    subtitles = [build_interval(1, 0.0, 1.0)]
    segs = [[0.0] * 5, [0.0] * 5]

    try:
        align_segments_to_subtitles(subtitles, segs, sample_rate)
    except ValueError as exc:
        assert "counts must match" in str(exc)
    else:
        raise AssertionError("Expected a ValueError for mismatched inputs")

