import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from applemusic.client import AppleMusicClient
from applemusic.config import Config
from applemusic.extractors.local_manager import (
    list_local_tracks,
    update_local_track_metadata,
    batch_delete_local_tracks,
    import_local_tracks_to_applemusic,
)


class TestAppleMusicClientLibraryMethods(unittest.TestCase):
    def setUp(self):
        self.config = Config(
            developer_token="dummy_dev_token",
            media_user_token="dummy_user_token",
            storefront="cn",
        )
        self.client = AppleMusicClient(self.config)

    @patch("requests.Session.get")
    def test_get_user_playlists(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {
                    "id": "p.abc12345",
                    "type": "library-playlists",
                    "attributes": {
                        "name": "My Favorite Hits",
                        "description": {"standard": "Best songs of 2026"},
                        "hasCatalog": False,
                        "canEdit": True,
                        "artwork": {"url": "https://example.com/artwork/{w}x{h}bb.jpg"},
                    },
                }
            ]
        }
        mock_get.return_value = mock_resp

        playlists = self.client.get_user_playlists(limit=10, offset=0)
        self.assertEqual(len(playlists), 1)
        self.assertEqual(playlists[0]["id"], "p.abc12345")
        self.assertEqual(playlists[0]["name"], "My Favorite Hits")
        self.assertEqual(playlists[0]["description"], "Best songs of 2026")
        self.assertEqual(playlists[0]["artwork_url"], "https://example.com/artwork/{w}x{h}bb.jpg")

    @patch("requests.Session.get")
    def test_get_playlist_tracks(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {
                    "id": "i.song123",
                    "type": "library-songs",
                    "attributes": {
                        "name": "晴天",
                        "artistName": "周杰伦",
                        "albumName": "叶惠美",
                        "durationInMillis": 269000,
                        "artwork": {"url": "https://example.com/track/{w}x{h}bb.jpg"},
                    },
                }
            ]
        }
        mock_get.return_value = mock_resp

        tracks = self.client.get_playlist_tracks("p.abc12345")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["id"], "i.song123")
        self.assertEqual(tracks[0]["title"], "晴天")
        self.assertEqual(tracks[0]["artist"], "周杰伦")
        self.assertEqual(tracks[0]["album"], "叶惠美")
        self.assertEqual(tracks[0]["duration_ms"], 269000)

    @patch("requests.Session.patch")
    def test_update_playlist(self, mock_patch):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_patch.return_value = mock_resp

        res = self.client.update_playlist("p.abc12345", name="New Name", description="New Desc")
        self.assertTrue(res)

    @patch("requests.Session.delete")
    def test_delete_playlist(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_delete.return_value = mock_resp

        res = self.client.delete_playlist("p.abc12345")
        self.assertTrue(res)

    @patch("requests.Session.delete")
    def test_batch_delete_playlists(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_delete.return_value = mock_resp

        success_count, failed = self.client.batch_delete_playlists(["p.1", "p.2"])
        self.assertEqual(success_count, 2)
        self.assertEqual(len(failed), 0)

    @patch("requests.Session.delete")
    def test_delete_playlist_tracks(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_delete.return_value = mock_resp

        deleted_count, failed = self.client.delete_playlist_tracks("p.abc", ["i.track1", "i.track2"])
        self.assertEqual(deleted_count, 2)
        self.assertEqual(len(failed), 0)

    @patch("requests.Session.post")
    def test_add_playlist_tracks(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_post.return_value = mock_resp

        added_count, failed = self.client.add_playlist_tracks("p.abc", ["i.track1", "i.track2"])
        self.assertEqual(added_count, 2)
        self.assertEqual(len(failed), 0)

    @patch("requests.Session.get")
    def test_get_library_songs(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {
                    "id": "i.song999",
                    "type": "library-songs",
                    "attributes": {
                        "name": "夜曲",
                        "artistName": "周杰伦",
                        "albumName": "十一月的萧邦",
                        "durationInMillis": 226000,
                    },
                }
            ]
        }
        mock_get.return_value = mock_resp

        songs = self.client.get_library_songs(limit=10)
        self.assertEqual(len(songs), 1)
        self.assertEqual(songs[0]["id"], "i.song999")
        self.assertEqual(songs[0]["title"], "夜曲")

    @patch("requests.Session.get")
    def test_get_library_songs_fetch_all(self, mock_get):
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {
            "data": [
                {
                    "id": f"i.song_{i}",
                    "type": "library-songs",
                    "attributes": {
                        "name": f"Song {i}",
                        "artistName": "Artist",
                        "albumName": "Album",
                        "durationInMillis": 200000,
                    },
                }
                for i in range(100)
            ],
            "next": "/v1/me/library/songs?offset=100",
        }
        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {
            "data": [
                {
                    "id": "i.song_101",
                    "type": "library-songs",
                    "attributes": {
                        "name": "Song 101",
                        "artistName": "Artist",
                        "albumName": "Album",
                        "durationInMillis": 200000,
                    },
                }
            ]
        }
        mock_get.side_effect = [resp1, resp2]

        songs = self.client.get_library_songs(limit=100, fetch_all=True)
        self.assertEqual(len(songs), 101)
        self.assertEqual(songs[0]["id"], "i.song_0")
        self.assertEqual(songs[100]["id"], "i.song_101")

    @patch("requests.Session.get")
    def test_search_library_songs(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": {
                "library-songs": {
                    "data": [
                        {
                            "id": "i.song_search_1",
                            "type": "library-songs",
                            "attributes": {
                                "name": "七里香",
                                "artistName": "周杰伦",
                                "albumName": "七里香",
                                "durationInMillis": 299000,
                            },
                        }
                    ]
                }
            }
        }
        mock_get.return_value = mock_resp

        results = self.client.search_library_songs("七里香")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "i.song_search_1")
        self.assertEqual(results[0]["title"], "七里香")

    @patch("requests.Session.delete")
    def test_delete_library_song(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_delete.return_value = mock_resp

        res = self.client.delete_library_song("i.song999")
        self.assertTrue(res)

    @patch("requests.Session.delete")
    def test_batch_delete_library_songs(self, mock_delete):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_delete.return_value = mock_resp

        success_count, failed = self.client.batch_delete_library_songs(["i.1", "i.2"])
        self.assertEqual(success_count, 2)
        self.assertEqual(len(failed), 0)


class TestLocalManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_local_tracks_empty_dir(self):
        tracks = list_local_tracks(self.test_dir)
        self.assertEqual(tracks, [])

    def test_batch_delete_local_tracks(self):
        file1 = os.path.join(self.test_dir, "song1.mp3")
        file2 = os.path.join(self.test_dir, "song2.m4a")
        with open(file1, "w", encoding="utf-8") as f:
            f.write("dummy audio")
        with open(file2, "w", encoding="utf-8") as f:
            f.write("dummy audio 2")

        self.assertTrue(os.path.exists(file1))
        self.assertTrue(os.path.exists(file2))

        success_count, failed = batch_delete_local_tracks([file1, file2])
        self.assertEqual(success_count, 2)
        self.assertEqual(len(failed), 0)
        self.assertFalse(os.path.exists(file1))
        self.assertFalse(os.path.exists(file2))

    @patch("applemusic.extractors.local_manager.get_apple_music_auto_add_dir")
    def test_import_local_tracks_to_applemusic(self, mock_get_auto_dir):
        dest_dir = os.path.join(self.test_dir, "auto_add")
        os.makedirs(dest_dir, exist_ok=True)
        mock_get_auto_dir.return_value = dest_dir

        file1 = os.path.join(self.test_dir, "song1.mp3")
        with open(file1, "w", encoding="utf-8") as f:
            f.write("dummy audio")

        success_count, failed = import_local_tracks_to_applemusic([file1])
        self.assertEqual(success_count, 1)
        self.assertEqual(len(failed), 0)
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "song1.mp3")))


class TestLibraryApi(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from applemusic.web.app import app, SESSION_API_TOKEN
        self.client = TestClient(app)
        self.headers = {"X-App-Token": SESSION_API_TOKEN}

    def test_get_local_songs(self):
        res = self.client.get("/api/local/songs", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertIsInstance(data.get("songs"), list)

    def test_unauthorized_user_playlists(self):
        # Without valid Apple ID token, should return 401
        with patch("applemusic.config.Config.is_authorized", return_value=False):
            res = self.client.get("/api/user/playlists", headers=self.headers)
            self.assertEqual(res.status_code, 401)

    @patch("applemusic.client.AppleMusicClient.search_library_songs")
    @patch("applemusic.config.Config.is_authorized", return_value=True)
    def test_search_library_songs_api(self, mock_auth, mock_search):
        mock_search.return_value = [{"id": "i.1", "title": "Test"}]
        res = self.client.get("/api/user/library/search?term=Test", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(len(data.get("songs")), 1)

    @patch("os.startfile", create=True)
    def test_open_local_folder_api(self, mock_startfile):
        res = self.client.post("/api/local/open-folder", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))

    @patch("applemusic.client.AppleMusicClient.delete_playlist_tracks")
    @patch("applemusic.config.Config.is_authorized", return_value=True)
    def test_delete_playlist_tracks_api(self, mock_auth, mock_del):
        mock_del.return_value = (1, [])
        res = self.client.request(
            "DELETE",
            "/api/user/playlists/p.abc/tracks",
            headers=self.headers,
            json={"track_ids": ["i.1"]}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("deleted_count"), 1)

    @patch("applemusic.client.AppleMusicClient.add_playlist_tracks")
    @patch("applemusic.config.Config.is_authorized", return_value=True)
    def test_add_playlist_tracks_api(self, mock_auth, mock_add):
        mock_add.return_value = (1, [])
        res = self.client.post(
            "/api/user/playlists/p.abc/tracks",
            headers=self.headers,
            json={"track_ids": ["i.1"]}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("added_count"), 1)
    @patch("applemusic.client.AppleMusicClient.add_playlist_tracks")
    @patch("applemusic.client.AppleMusicClient.find_library_song_id")
    @patch("applemusic.config.Config.is_authorized", return_value=True)
    def test_add_local_tracks_to_playlist_api(self, mock_auth, mock_find, mock_add):
        mock_find.return_value = "i.test_local_id"
        mock_add.return_value = (1, [])
        res = self.client.post(
            "/api/user/playlists/p.abc/add-local-tracks",
            headers=self.headers,
            json={"tracks": [{"title": "Song A", "artist": "Artist B", "file_path": "C:/a.m4a"}]}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("added_count"), 1)

    @patch("requests.Session.get")
    def test_get_playlist_tracks_pagination(self, mock_get):
        from applemusic.client import AppleMusicClient
        from applemusic.config import Config
        cfg = Config(developer_token="dev_tok", media_user_token="usr_tok")
        client = AppleMusicClient(cfg)

        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {
            "data": [{"id": f"i.{i}", "attributes": {"name": f"Song {i}", "artistName": "Artist", "albumName": "Album"}} for i in range(100)],
            "next": "/v1/me/library/playlists/p.123/tracks?offset=100"
        }

        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {
            "data": [{"id": f"i.{i}", "attributes": {"name": f"Song {i}", "artistName": "Artist", "albumName": "Album"}} for i in range(100, 150)],
        }

        mock_get.side_effect = [resp1, resp2]
        tracks = client.get_playlist_tracks("p.123", fetch_all=True)
        self.assertEqual(len(tracks), 150)
        self.assertEqual(tracks[0]["id"], "i.0")
        self.assertEqual(tracks[149]["id"], "i.149")


if __name__ == "__main__":
    unittest.main()
