import pytest

from clipper import reframe
from clipper.reframe import Sample


def face(x, size=0.2):
    return (x, 0.4, size, size * 16 / 9)


def test_crop_box_for_each_aspect():
    assert reframe.crop_box(1920, 1080) == (606, 1080)
    assert reframe.crop_box(1080, 1920) == (1080, 1920)
    assert reframe.crop_box(1080, 2400) == (1080, 1920)


def test_static_face_is_followed():
    samples = [Sample(t=i / 4, faces=[face(0.3)]) for i in range(12)]
    path = reframe.track(samples)
    assert all(x == pytest.approx(0.3) for _, x, _ in path)


def test_no_faces_stays_centered():
    path = reframe.track([Sample(t=i / 4, faces=[]) for i in range(8)])
    assert all(x == pytest.approx(0.5) for _, x, _ in path)


def test_movement_is_smooth_without_cut():
    samples = [Sample(t=i / 4, faces=[face(0.25 if i < 6 else 0.75)]) for i in range(30)]
    xs = [x for _, x, _ in reframe.track(samples)]
    steps = [abs(b - a) for a, b in zip(xs, xs[1:])]
    assert max(steps) < 0.15  # la cámara se desliza, no salta
    assert xs[-1] == pytest.approx(0.75, abs=0.06)


def test_scene_cut_jumps_immediately():
    samples = [Sample(t=i / 4, faces=[face(0.2)]) for i in range(8)]
    samples += [Sample(t=(8 + i) / 4, faces=[face(0.8)], cut=(i == 0)) for i in range(8)]
    path = reframe.track(samples)
    assert path[8][2] is True
    assert path[8][1] == pytest.approx(0.8)
    assert path[7][1] == pytest.approx(0.2)


def test_prefers_tracked_face_over_slightly_bigger_one():
    samples = [Sample(t=0, faces=[face(0.3, 0.2)])]
    samples += [Sample(t=i / 4, faces=[face(0.3, 0.2), face(0.8, 0.22)]) for i in range(1, 10)]
    assert all(x == pytest.approx(0.3) for _, x, _ in reframe.track(samples))


def test_crop_commands_clamp_and_do_not_interpolate_across_cuts():
    path = [(0.0, 0.0, True), (1.0, 0.0, False), (2.0, 1.0, True), (3.0, 1.0, False)]
    x0, text = reframe.crop_commands(path, src_w=1920, crop_w=606, duration=3.0, fps=10)
    assert x0 == 0
    lines = text.strip().splitlines()
    assert lines[0] == "0.000 crop x 0;"
    assert lines[-1] == "2.000 crop x 1314;"  # salto directo en el corte, pegado al borde derecho
    assert len(lines) == 2
