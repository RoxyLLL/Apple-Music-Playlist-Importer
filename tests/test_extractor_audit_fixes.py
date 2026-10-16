"""Offline regressions for extractor completeness and validated audio cache."""

import json
import wave
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from applemusic.extractors import bilibili_downloader as bili
from applemusic.extractors.local_file import LocalFileExtractor
from applemusic.extractors.netease import NetEaseExtractor


def response(data, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode()
    return result


def song(number):
    return {"id": number, "name": f"Song {number}", "ar": [{"name": "Artist"}]}


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
@pytest.mark.parametrize("artist_first", [True, False])
def test_csv_bom_named_columns(tmp_path, encoding, artist_first):
    file = tmp_path / "songs.csv"
    contents = "artist,title\n歌手,歌曲\n" if artist_first else "title,artist\n歌曲,歌手\n"
    file.write_text(contents, encoding=encoding)
    playlist = LocalFileExtractor().extract(str(file))
    assert [(t.title, t.artists) for t in playlist.tracks] == [("歌曲", ["歌手"])]


def mock_playlist(monkeypatch, count):
    monkeypatch.setattr("applemusic.extractors.netease.requests.get", Mock(return_value=response({
        "code": 200, "playlist": {"name": "Test", "trackIds": [{"id": n} for n in range(count)]}
    })))


@pytest.mark.parametrize("failure", [requests.Timeout("offline"), response({}, 429), response({"code": 403})])
def test_netease_all_failed_raises(monkeypatch, failure):
    mock_playlist(monkeypatch, 2)
    post = Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure)
    monkeypatch.setattr("applemusic.extractors.netease.requests.post", post)
    with pytest.raises(ValueError, match="全部获取失败"):
        NetEaseExtractor().extract("123456")


@pytest.mark.parametrize("failure", [requests.Timeout("offline"), response({}, 429), response({"code": 403})])
def test_netease_partial_failure_reports_completeness(monkeypatch, failure):
    mock_playlist(monkeypatch, 101)
    monkeypatch.setattr("applemusic.extractors.netease.requests.post", Mock(side_effect=[
        response({"code": 200, "songs": [song(n) for n in range(100)]}), failure
    ]))
    playlist = NetEaseExtractor().extract("123456")
    assert len(playlist.tracks) == 100
    assert playlist.total_expected == 101
    assert any("第 2 批" in w and "重试" in w for w in playlist.warnings)
    assert any("101" in w and "100" in w for w in playlist.warnings)


def test_netease_success_and_missing_items(monkeypatch):
    mock_playlist(monkeypatch, 2)
    post = Mock(return_value=response({"code": 200, "songs": [song(0), song(1)]}))
    monkeypatch.setattr("applemusic.extractors.netease.requests.post", post)
    playlist = NetEaseExtractor().extract("123456")
    assert len(playlist.tracks) == playlist.total_expected == 2
    assert playlist.warnings == []
    post.return_value = response({"code": 200, "songs": [song(0)]})
    assert NetEaseExtractor().extract("123456").warnings


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "source.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes(b"\x00\x00" * 4410)
    return path


@pytest.fixture
def bili_download(monkeypatch, tmp_path):
    backup = tmp_path / "backup"
    auto = tmp_path / "auto"
    backup.mkdir()
    auto.mkdir()
    monkeypatch.setattr(bili, "get_backup_download_dir", lambda: str(backup))
    monkeypatch.setattr(bili, "get_apple_music_auto_add_dir", lambda: str(auto))
    monkeypatch.setattr(bili, "MP4", None)
    stream = Mock()
    stream.iter_content.return_value = [b"downloaded source"]

    def get(url, **kwargs):
        if url.endswith("/view"):
            return response({"code": 0, "data": {"cid": 1}})
        if url.endswith("/playurl"):
            if kwargs["params"]["fnval"]:
                return response({"data": {"dash": {"audio": [{"baseUrl": "https://mock/audio"}]}}})
            return response({"data": {"durl": []}})
        assert url == "https://mock/audio"
        return stream

    getter = Mock(side_effect=get)
    monkeypatch.setattr(bili._client.session, "get", getter)
    return backup, auto, getter


def test_bili_failed_remux_never_publishes_or_reuses(monkeypatch, bili_download):
    backup, auto, getter = bili_download

    def corrupt(source, dest):
        Path(dest).write_bytes(b"broken" * 20000)
        raise ValueError("remux failed")

    monkeypatch.setattr(bili, "extract_audio_from_video", corrupt)
    for _ in range(2):
        before = getter.call_count
        result = bili.download_bilibili_audio("BVtest", "Title", "Artist")
        assert not result["success"]
        assert getter.call_count > before
        assert list(backup.iterdir()) == []
        assert list(auto.iterdir()) == []


def test_bili_old_corrupt_cache_not_reused_or_destroyed(monkeypatch, bili_download):
    backup, auto, getter = bili_download
    old = backup / "Artist - Title [BVtest].m4a"
    old.write_bytes(b"broken" * 20000)
    monkeypatch.setattr(bili, "extract_audio_from_video", Mock(side_effect=ValueError("failed")))
    result = bili.download_bilibili_audio("BVtest", "Title", "Artist")
    assert not result["success"]
    assert getter.called
    assert old.read_bytes() == b"broken" * 20000
    assert list(backup.iterdir()) == [old]
    assert not list(auto.iterdir())


def test_bili_success_then_validated_reuse(monkeypatch, bili_download, audio_file):
    backup, auto, getter = bili_download
    remux = bili.extract_audio_from_video
    monkeypatch.setattr(bili, "extract_audio_from_video", lambda source, dest: remux(str(audio_file), dest))
    result = bili.download_bilibili_audio("BVtest", "Title", "Artist")
    assert result["success"] and result["auto_imported"]
    assert bili._is_readable_audio(result["local_path"])
    assert len(list(backup.iterdir())) == 1
    before = getter.call_count
    result = bili.download_bilibili_audio("BVtest", "Title", "Artist")
    assert result["success"] and result["reused"]
    assert getter.call_count == before
    assert len(list(auto.iterdir())) == 1


def test_bili_successful_but_unreadable_remux_is_failure(monkeypatch, bili_download):
    backup, auto, _ = bili_download
    monkeypatch.setattr(bili, "extract_audio_from_video", lambda source, dest: Path(dest).write_bytes(b"invalid"))
    assert not bili.download_bilibili_audio("BVtest", "Title", "Artist")["success"]
    assert not list(backup.iterdir())
    assert not list(auto.iterdir())


def test_bili_actual_remux_is_readable(audio_file, tmp_path):
    output = tmp_path / "output.m4a"
    bili.extract_audio_from_video(str(audio_file), str(output))
    assert bili._is_readable_audio(str(output))
    second = tmp_path / "aac-remux.m4a"
    bili.extract_audio_from_video(str(output), str(second))
    assert bili._is_readable_audio(str(second))


def test_bili_video_fallback_replaces_failed_staging(monkeypatch, bili_download, audio_file):
    backup, auto, getter = bili_download
    original_get = getter.side_effect
    remux = bili.extract_audio_from_video

    def fallback_get(url, **kwargs):
        if url.endswith("/playurl") and not kwargs["params"]["fnval"]:
            return response({"data": {"durl": [{"url": "https://mock/audio"}]}})
        return original_get(url, **kwargs)

    def remux_with_failure(source, dest):
        if source.endswith("raw.m4a"):
            Path(dest).write_bytes(b"broken" * 20000)
            raise ValueError("DASH failed")
        remux(str(audio_file), dest)

    getter.side_effect = fallback_get
    monkeypatch.setattr(bili, "extract_audio_from_video", remux_with_failure)
    result = bili.download_bilibili_audio("BVtest", "Title", "Artist")
    assert result["success"] and result["auto_imported"]
    assert bili._is_readable_audio(result["local_path"])
    assert len(list(backup.iterdir())) == len(list(auto.iterdir())) == 1


def test_bili_auto_import_rejects_corrupt_file(bili_download, tmp_path):
    _, auto, _ = bili_download
    bad = tmp_path / "bad.m4a"
    bad.write_bytes(b"invalid" * 20000)
    assert not bili.ensure_auto_imported(str(bad), "Title", "Artist")
    assert not list(auto.iterdir())
