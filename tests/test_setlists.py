import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash
from app import create_app


class SetlistsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {'TESTING': True, 'DATA_DIR': self.tmp.name}
        self.app = create_app(self.config)
        with self.db() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE username IN ('joe','rob','rachel')", (generate_password_hash('setlist-test-password'),))
            conn.executemany('INSERT INTO gigs(day,venue,location) VALUES (?,?,?)',
                             [('2099-07-14', 'First Gig', 'SF'), ('2099-08-14', 'Second Gig', 'Oakland')])
            conn.executemany('INSERT INTO songs(title,artist,song_key,lead,status,notes) VALUES (?,?,?,?,?,?)',
                             [('Private Song A', 'Artist A', 'E', 'Rachel', 'ready', 'Quiet intro'),
                              ('Private Song B', 'Artist B', 'G', 'Rob', 'ready', 'Big ending'),
                              ('Working Song', 'Artist C', '', '', 'working', '')])
        self.owner = self.sign_in('joe')
        self.rob = self.sign_in('rob')
        self.member = self.sign_in('rachel')

    def tearDown(self):
        self.tmp.cleanup()

    def db(self):
        return sqlite3.connect(Path(self.tmp.name, 'band.sqlite3'))

    def csrf(self, client, path='/admin/setlists'):
        return re.search(r'name="csrf" value="([^"]+)"', client.get(path).get_data(as_text=True))[1]

    def sign_in(self, username):
        client = self.app.test_client()
        client.post('/admin/login', data={'csrf': self.csrf(client, '/admin/login'), 'username': username, 'password': 'setlist-test-password'})
        return client

    def post(self, path, client=None, **data):
        client = client or self.owner
        return client.post(path, data={'csrf': self.csrf(client), **data})

    def entries(self, gig_id=1):
        with self.db() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute('SELECT * FROM setlist_entries WHERE gig_id=? ORDER BY set_number,position,id', (gig_id,))]

    def test_build_order_sets_and_gig_specific_details(self):
        response = self.post('/admin/gigs/1/setlist/add', client=self.rob, set_number='1', song_ids=['1', '2'])
        self.assertEqual(response.status_code, 302)
        entries = self.entries()
        self.assertEqual([row['title'] for row in entries], ['Private Song A', 'Private Song B'])
        self.post(f"/admin/gigs/1/setlist/{entries[1]['id']}/save", set_number='1', position='1', song_key='A minor', lead='Rachel & Rob', notes='Gig-only ending')
        self.assertEqual([row['title'] for row in self.entries()], ['Private Song B', 'Private Song A'])
        self.post(f"/admin/gigs/1/setlist/{entries[0]['id']}/save", set_number='2', position='1', song_key='E', lead='Rachel', notes='Quiet intro')
        self.assertEqual([row['set_number'] for row in self.entries()], [1, 2])
        page = self.member.get('/admin/gigs/1/setlist').get_data(as_text=True)
        for text in ['Set 1', 'Set 2', 'Gig-only ending', 'A minor']:
            self.assertIn(text, page)
        self.assertNotIn('Edit / move song', page)
        with self.db() as conn:
            self.assertEqual(conn.execute('SELECT song_key,notes FROM songs WHERE id=2').fetchone(), ('G', 'Big ending'))

    def test_copy_is_independent_and_replacement_needs_confirmation(self):
        self.post('/admin/gigs/1/setlist/add', set_number='2', song_ids=['1', '2'])
        self.assertEqual(self.post('/admin/gigs/2/setlist/copy', source_id='1').status_code, 302)
        self.assertEqual([r['title'] for r in self.entries(2)], ['Private Song A', 'Private Song B'])
        self.assertEqual(self.post('/admin/gigs/2/setlist/copy', source_id='1').status_code, 400)
        second = self.entries(2)[0]
        self.post(f"/admin/gigs/2/setlist/{second['id']}/save", set_number='1', position='1', song_key='C', lead='Rob', notes='Different arrangement')
        self.assertEqual(self.entries()[0]['song_key'], 'E')
        self.assertEqual(self.entries(2)[0]['song_key'], 'C')
        self.assertEqual(self.post('/admin/gigs/2/setlist/copy', source_id='1', replace='yes').status_code, 302)
        self.assertEqual(self.entries(2)[0]['song_key'], 'E')
        self.assertEqual(self.post('/admin/gigs/1/setlist/copy', source_id='1', replace='yes').status_code, 400)

    def test_private_read_and_admin_only_writes(self):
        anonymous = self.app.test_client()
        for path in ['/admin/setlists', '/admin/gigs/1/setlist']:
            self.assertEqual(anonymous.get(path).status_code, 302)
            self.assertEqual(self.member.get(path).status_code, 200)
        for suffix in ['add', 'copy', '1/save', '1/delete']:
            self.assertEqual(self.post('/admin/gigs/1/setlist/' + suffix, client=self.member).status_code, 403)
        self.assertEqual(self.owner.post('/admin/gigs/1/setlist/add', data={'song_ids': '1', 'set_number': '1'}).status_code, 400)
        self.post('/admin/gigs/1/setlist/add', set_number='1', song_ids=['1'])
        self.assertNotIn('Private Song A', self.member.get('/').get_data(as_text=True))
        self.assertEqual(self.member.get('/admin/gigs/1/setlist').headers['Cache-Control'], 'no-store')

    def test_song_deletion_preserves_snapshots_and_gig_deletion_cleans_up(self):
        self.post('/admin/gigs/1/setlist/add', set_number='1', song_ids=['1'])
        self.post('/admin/songs/1/delete')
        create_app(self.config)
        self.assertEqual(self.entries()[0]['title'], 'Private Song A')
        self.assertEqual(self.post('/admin/gigs/2/setlist/copy', source_id='1').status_code, 302)
        self.post('/admin/delete/gigs/1')
        self.assertEqual(self.entries(1), [])
        self.assertEqual(self.entries(2)[0]['title'], 'Private Song A')
        self.assertEqual(self.member.get('/admin/gigs/1/setlist').status_code, 404)

    def test_invalid_selection_and_wrong_gig_do_not_change_other_setlists(self):
        for ids in [[], ['1', '9999'], ['3'], ['bad']]:
            self.assertEqual(self.post('/admin/gigs/1/setlist/add', set_number='1', song_ids=ids).status_code, 400)
            self.assertEqual(self.entries(), [])
        self.post('/admin/gigs/1/setlist/add', set_number='1', song_ids=['1'])
        entry_id = self.entries()[0]['id']
        self.assertEqual(self.post(f'/admin/gigs/2/setlist/{entry_id}/delete').status_code, 404)
        self.assertEqual(self.post(f'/admin/gigs/2/setlist/{entry_id}/save', set_number='1', position='1').status_code, 404)
        self.assertEqual(self.post('/admin/gigs/9999/setlist/add', set_number='1', song_ids=['1']).status_code, 404)
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(self.post(f'/admin/gigs/1/setlist/{entry_id}/delete').status_code, 302)
        with self.db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0], 3)
