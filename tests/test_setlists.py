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

    def change_break(self, entry_id, start_set='yes', gig_id=1, client=None, version=None):
        from setlists import order_version
        if version is None:
            version = order_version(self.entries(gig_id))
        return self.post(f'/admin/gigs/{gig_id}/setlist/{entry_id}/set-break', client=client,
                         start_set=start_set, order_version=version)

    def test_split_and_merge_sets_preserves_playing_order_and_all_snapshot_details(self):
        with self.db() as conn:
            for i, (title, set_number, position) in enumerate([
                ('Zulu',1,1),('Repeated song',1,2),('Alpha',1,3),
                ('Original second set',2,1),('Repeated song',2,2),('Finale',3,1)]):
                conn.execute('INSERT INTO setlist_entries(gig_id,set_number,position,title,artist,song_key,lead,notes,bpm) VALUES (1,?,?,?,?,?,?,?,?)',
                             (set_number,position,title,f'Artist {i}','Am','Rachel',f'Notes {i}',80+i))
            conn.execute("INSERT INTO setlist_entries(gig_id,set_number,position,title,artist,song_key,lead,notes,bpm) VALUES (2,1,1,'Other gig','Keep','C','Rob','Unchanged',100)")
            songs_before=conn.execute('SELECT * FROM songs').fetchall()
        before=self.entries()
        other_before=self.entries(2)
        self.assertEqual(self.change_break(before[1]['id'],client=self.rob).status_code,302)
        self.assertEqual([r['set_number'] for r in self.entries()],[1,2,2,3,3,4])
        self.assertEqual(self.change_break(before[4]['id']).status_code,302)
        self.assertEqual([r['set_number'] for r in self.entries()],[1,2,2,3,4,5])
        self.assertEqual(self.change_break(before[3]['id'],'no').status_code,302)
        after=self.entries()
        self.assertEqual([r['set_number'] for r in after],[1,2,2,2,3,4])
        self.assertEqual([r['position'] for r in after],[1,1,2,3,1,1])
        for old,new in zip(before,after):
            self.assertEqual({k:v for k,v in old.items() if k not in ('set_number','position')},
                             {k:v for k,v in new.items() if k not in ('set_number','position')})
        self.assertEqual(self.entries(2),other_before)
        with self.db() as conn:
            self.assertEqual(conn.execute('SELECT * FROM songs').fetchall(),songs_before)
        page=self.owner.get('/admin/gigs/1/setlist').get_data(as_text=True)
        self.assertIn('Start next set',page)
        self.assertIn('Join previous set',page)
        self.assertNotIn('Start next set',self.member.get('/admin/gigs/1/setlist').get_data(as_text=True))

    def test_set_break_permissions_validation_and_stale_order(self):
        from setlists import order_version
        self.post('/admin/gigs/1/setlist/add',set_number='1',song_ids=['1','2'])
        before=self.entries()
        entry=before[1]['id']
        self.assertEqual(self.change_break(entry,client=self.member).status_code,403)
        self.assertEqual(self.owner.post(f'/admin/gigs/1/setlist/{entry}/set-break',data={'start_set':'yes'}).status_code,400)
        self.assertEqual(self.change_break(entry,'bad').status_code,400)
        self.assertEqual(self.change_break(before[0]['id']).status_code,400)
        self.assertEqual(self.change_break(entry,gig_id=2).status_code,404)
        self.assertEqual(self.change_break(entry,version='outdated').status_code,409)
        self.assertEqual(self.entries(),before)
        version=order_version(before)
        self.assertEqual(self.change_break(entry,version=version).status_code,302)
        self.assertEqual(self.change_break(entry,version=version).status_code,409)
        self.assertEqual(self.change_break(entry).status_code,302)
        self.assertEqual([r['set_number'] for r in self.entries()],[1,2])
        self.assertEqual(self.change_break(entry,'no').status_code,302)
        self.assertEqual([r['set_number'] for r in self.entries()],[1,1])

    def test_set_break_limit_does_not_partially_change_order(self):
        with self.db() as conn:
            for i in range(21):
                conn.execute("INSERT INTO setlist_entries(gig_id,set_number,position,title,artist,song_key,lead,notes) VALUES (1,?,?,'Tune','','','','')",(min(i+1,20),1 if i<20 else 2))
        before=self.entries()
        self.assertEqual(self.change_break(before[-1]['id']).status_code,400)
        self.assertEqual(self.entries(),before)

    def test_printable_view_is_private_ordered_and_has_no_editing_forms(self):
        self.post('/admin/gigs/1/setlist/add',set_number='1',song_ids=['2','1'])
        entries=self.entries()
        self.change_break(entries[1]['id'])
        path='/admin/gigs/1/setlist/print'
        self.assertEqual(self.app.test_client().get(path).status_code,302)
        for client in [self.owner,self.rob,self.member]:
            response=client.get(path)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            page=response.get_data(as_text=True)
            self.assertIn('Print / Save PDF',page)
            self.assertIn('Set 1',page)
            self.assertIn('Set 2',page)
            self.assertLess(page.index('Private Song B'),page.index('Private Song A'))
            self.assertIn('Quiet intro',page)
            self.assertIn('Lead singer',page)
            self.assertIn('BPM',page)
            self.assertNotIn('<form',page)
            self.assertNotIn('Private navigation',page)
            self.assertNotIn('Edit / move',page)
        self.assertEqual(self.member.get('/admin/gigs/99999/setlist/print').status_code,404)
