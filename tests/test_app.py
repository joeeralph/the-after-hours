import re
import tempfile
import sqlite3
import unittest
from pathlib import Path
from werkzeug.security import generate_password_hash
from app import create_app


class BandSiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.tmp.name})
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            conn.execute('DELETE FROM videos')
        Path(self.tmp.name, 'admin.hash').write_text(generate_password_hash('a-long-test-password'))
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def token(self, path='/admin'):
        html = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', html)[1]

    def login(self):
        return self.client.post('/admin/login', data={'csrf': self.token('/admin/login'), 'password': 'a-long-test-password'})

    def test_private_dashboard_and_csrf(self):
        self.assertEqual(self.client.get('/admin').status_code, 302)
        self.assertEqual(self.client.post('/admin/save/gig', data={}).status_code, 400)
        self.assertEqual(self.client.post('/admin/login', data={'csrf': self.token('/admin/login'), 'password': 'wrong'}).status_code, 401)
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.client.get('/admin').status_code, 200)

    def test_gigs_create_edit_hide_past_and_delete(self):
        self.login()
        for day, venue in [('2099-12-01', 'Future Venue'), ('2000-01-01', 'Old Venue')]:
            self.client.post('/admin/save/gig', data={'csrf': self.token(), 'day': day, 'venue': venue, 'location': 'Our Town', 'link': 'https://example.com/tickets'})
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('Future Venue', page)
        self.assertNotIn('Old Venue', page)
        self.client.post('/admin/save/gig', data={'csrf': self.token(), 'id': '1', 'day': '2099-12-02', 'venue': 'Updated Venue', 'location': 'New Town'})
        self.assertIn('Updated Venue', self.client.get('/').get_data(as_text=True))
        self.client.post('/admin/delete/gigs/1', data={'csrf': self.token()})
        self.assertNotIn('Updated Venue', self.client.get('/').get_data(as_text=True))

    def test_videos_settings_and_unsafe_links(self):
        self.login()
        self.client.post('/admin/save/video', data={'csrf': self.token(), 'title': 'Live set', 'link': 'https://youtu.be/dQw4w9WgXcQ'})
        self.assertIn('youtube-nocookie.com/embed/dQw4w9WgXcQ', self.client.get('/').get_data(as_text=True))
        self.client.post('/admin/save/video', data={'csrf': self.token(), 'id': '1', 'title': 'Updated set', 'link': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'})
        self.assertIn('Updated set', self.client.get('/').get_data(as_text=True))
        self.client.post('/admin/save/settings', data={'csrf': self.token(), 'intro': 'Our band story', 'email': 'booking@example.com', 'photo': 'https://example.com/band.jpg'})
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('Our band story', page)
        self.assertIn('mailto:booking@example.com', page)
        self.client.post('/admin/save/settings', data={'csrf': self.token(), 'photo': 'javascript:alert(1)'})
        self.assertNotIn('javascript:', self.client.get('/').get_data(as_text=True))
        self.client.post('/admin/delete/videos/1', data={'csrf': self.token()})
        self.assertNotIn('Updated set', self.client.get('/').get_data(as_text=True))

    def test_login_rate_limit(self):
        for _ in range(10):
            self.client.post('/admin/login', data={'csrf': self.token('/admin/login'), 'password': 'incorrect'})
        self.assertEqual(self.login().status_code, 429)

    def test_supplied_videos_persist_and_deleted_video_stays_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            config = {'TESTING': True, 'DATA_DIR': folder}
            app = create_app(config)
            page = app.test_client().get('/').get_data(as_text=True)
            for video_id in ('7JeuGzJA9nQ', '274AIJQ-sXM'):
                self.assertIn('youtube-nocookie.com/embed/' + video_id, page)
                self.assertIn('href="https://www.youtube.com/', page)
            self.assertIn('video-card short-video', page)
            with sqlite3.connect(Path(folder, 'band.sqlite3')) as conn:
                conn.execute("DELETE FROM videos WHERE video_id='7JeuGzJA9nQ'")
            page = create_app(config).test_client().get('/').get_data(as_text=True)
            self.assertNotIn('7JeuGzJA9nQ', page)
            self.assertEqual(page.count('youtube-nocookie.com/embed/274AIJQ-sXM'), 1)


if __name__ == '__main__':
    unittest.main()
