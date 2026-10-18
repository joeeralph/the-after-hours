import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash
from app import create_app


class SongListsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {'TESTING': True, 'DATA_DIR': self.tmp.name}
        self.app = create_app(self.config)
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE username IN ('joe','rachel')",
                         (generate_password_hash('test-song-password'),))
        self.owner = self.app.test_client()
        self.member = self.app.test_client()
        for client, username in [(self.owner, 'joe'), (self.member, 'rachel')]:
            client.post('/admin/login', data={'csrf': self.csrf(client, '/admin/login'),
                                             'username': username, 'password': 'test-song-password'})

    def tearDown(self):
        self.tmp.cleanup()

    def csrf(self, client, path='/admin/songs'):
        page = client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', page)[1]

    def post(self, client, path, **fields):
        return client.post(path, data={'csrf': self.csrf(client), **fields})

    def test_add_move_search_restart_and_delete(self):
        response = self.post(self.owner, '/admin/songs/save', title='Private practice tune', artist='The After Hours',
                             song_key='E minor', lead='Rachel', status='working', notes='Work on the ending',
                             reference='https://youtu.be/274AIJQ-sXM')
        self.assertEqual(response.location, '/admin/songs#working')
        page = self.member.get('/admin/songs').get_data(as_text=True)
        for text in ['Private practice tune', 'E minor', 'Rachel', 'Work on the ending']:
            self.assertIn(text, page)
        self.assertNotIn('Private practice tune', self.owner.get('/').get_data(as_text=True))
        self.assertIn('Private practice tune', self.member.get('/admin/songs?q=ending').get_data(as_text=True))
        self.assertNotIn('Private practice tune', self.member.get('/admin/songs?q=no-match').get_data(as_text=True))
        self.assertEqual(self.owner.get('/admin/songs/1/edit').status_code, 200)
        response = self.post(self.owner, '/admin/songs/save', id='1', title='Private practice tune',
                             artist='The After Hours', song_key='G', lead='Rachel', status='ready', notes='New ending')
        self.assertEqual(response.location, '/admin/songs#ready')
        create_app(self.config)
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT status,song_key,notes FROM songs').fetchall(), [('ready', 'G', 'New ending')])
        self.assertEqual(self.post(self.owner, '/admin/songs/1/delete').status_code, 302)
        self.assertNotIn('Private practice tune', self.member.get('/admin/songs').get_data(as_text=True))

    def test_lists_private_and_only_owner_can_edit(self):
        self.assertEqual(self.app.test_client().get('/admin/songs').status_code, 302)
        self.assertEqual(self.member.get('/admin/songs').status_code, 200)
        self.assertEqual(self.member.get('/admin/songs/1/edit').status_code, 403)
        self.assertEqual(self.post(self.member, '/admin/songs/save', title='Not allowed', status='working').status_code, 403)
        self.assertEqual(self.post(self.member, '/admin/songs/1/delete').status_code, 403)
        self.assertEqual(self.owner.post('/admin/songs/save', data={'title': 'No token', 'status': 'ready'}).status_code, 400)
        self.assertEqual(self.member.get('/admin/songs').headers['Cache-Control'], 'no-store')

    def test_invalid_input_keeps_form_values_and_does_not_save(self):
        for fields in [dict(title='', status='ready'), dict(title='Draft tune', status='invalid'),
                       dict(title='Draft tune', status='working', reference='javascript:alert(1)'),
                       dict(title='Draft tune', status='working', reference='https://[invalid'),
                       dict(title='Draft tune', status='working', id='invalid')]:
            self.assertEqual(self.post(self.owner, '/admin/songs/save', **fields).status_code, 400)
        result = self.post(self.owner, '/admin/songs/save', title='Keep my title', artist='Keep my artist',
                           status='working', reference='http://example.com')
        self.assertIn('Keep my title', result.get_data(as_text=True))
        self.assertIn('Keep my artist', result.get_data(as_text=True))
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0], 0)
