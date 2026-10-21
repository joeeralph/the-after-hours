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
        data = {'csrf': self.csrf(client), **fields}
        if path == '/admin/songs/import' and fields.get('action') == 'save':
            preview = client.post(path, data={**data, 'action': 'preview'})
            if preview.status_code != 200:
                return preview
            token = re.search(r'name="preview_token" value="([^"]+)"', preview.get_data(as_text=True))
            if token:
                data['preview_token'] = token[1]
        return client.post(path, data=data)

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

    def test_bpm_validation_persistence_and_private_display(self):
        for bpm in ['0', '401', 'fast', '80.5']:
            self.assertEqual(self.post(self.owner, '/admin/songs/save', title='Tempo tune', status='ready', bpm=bpm).status_code, 400)
        self.assertEqual(self.post(self.owner, '/admin/songs/save', title='Tempo tune', status='ready', bpm='95').status_code, 302)
        self.assertIn('95 BPM', self.member.get('/admin/songs').get_data(as_text=True))
        create_app(self.config)
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT bpm FROM songs').fetchall(), [(95,)])

    def test_bulk_preview_validation_permissions_and_ordered_repeats(self):
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            gig = conn.execute("INSERT INTO gigs(day,venue,location) VALUES ('2026-12-01','Test venue','Test town')").lastrowid
        text = 'Title\tArtist\tKey\tLead\tBPM\tNotes\nFirst\tArtist\tAm\tRachel\t80\tIntro\nSecond\t\tF\t\t100\t\nFirst\tArtist\tG\t\t85\tReprise'
        path = '/admin/songs/import'
        self.assertEqual(self.member.get(path).status_code, 403)
        self.assertEqual(self.post(self.member, path, text=text, action='save').status_code, 403)
        self.assertEqual(self.owner.post(path, data={'text': text}).status_code, 400)
        preview = self.post(self.owner, path, text=text, gig_id=str(gig), action='preview')
        self.assertEqual(preview.status_code, 200)
        self.assertIn('Confirm import', preview.get_data(as_text=True))
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0], 0)
        self.assertEqual(self.post(self.owner, path, text=text+'\nInvalid\t\t\t\t999', gig_id=str(gig), action='save').status_code, 400)
        self.assertEqual(self.post(self.owner, path, text=text, gig_id=str(gig), action='save').status_code, 302)
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT title,bpm FROM songs ORDER BY id').fetchall(), [('First',80),('Second',100)])
            self.assertEqual(conn.execute('SELECT title,song_key,bpm FROM setlist_entries ORDER BY position').fetchall(), [('First','Am',80),('Second','F',100),('First','G',85)])
        page = self.member.get(f'/admin/gigs/{gig}/setlist').get_data(as_text=True)
        self.assertIn('85 BPM', page)
        self.assertEqual(self.post(self.owner, path, text='First\tArtist', action='save').status_code, 302)
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0], 2)
        copy_gig = None
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            copy_gig = conn.execute("INSERT INTO gigs(day,venue,location) VALUES ('2026-12-02','Copy','Town')").lastrowid
        self.assertEqual(self.post(self.owner, f'/admin/gigs/{copy_gig}/setlist/copy', source_id=str(gig)).status_code, 302)
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT bpm FROM setlist_entries WHERE gig_id=? ORDER BY position',(copy_gig,)).fetchall(), [(80,),(100,),(85,)])

    def test_bulk_errors_rollback_and_preserve_input(self):
        path = '/admin/songs/import'
        for text in ['','Good\nBad,,,,401','Good\n,missing title','A,B,C,D,80,F,extra']:
            self.assertEqual(self.post(self.owner, path, text=text, action='save').status_code, 400)
        self.assertEqual(self.post(self.owner, path, text='Good', gig_id='999', action='save').status_code, 400)
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0], 0)
        self.assertEqual(self.post(self.owner, path, text='"Title with, comma",Artist,,,80', action='save').status_code, 302)

    def test_existing_database_migrates_without_losing_songs(self):
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            conn.execute('ALTER TABLE songs DROP COLUMN bpm')
            conn.execute('ALTER TABLE setlist_entries DROP COLUMN bpm')
            conn.execute("INSERT INTO songs(title,status) VALUES ('Existing tune','ready')")
        create_app(self.config)
        create_app(self.config)
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT title,bpm FROM songs').fetchall(), [('Existing tune',None)])
        self.assertEqual(self.post(self.owner, '/admin/songs/save', id='1', title='Existing tune', status='ready', bpm='106').status_code, 302)
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            gig = conn.execute("INSERT INTO gigs(day,venue,location) VALUES ('2026-12-01','Test','Town')").lastrowid
        self.assertEqual(self.post(self.owner, f'/admin/gigs/{gig}/setlist/add', set_number='1', song_ids='1').status_code, 302)
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            entry = conn.execute('SELECT id,bpm FROM setlist_entries').fetchone()
            self.assertEqual(entry[1], 106)
        self.assertEqual(self.post(self.owner, f'/admin/gigs/{gig}/setlist/{entry[0]}/save', set_number='1', position='1', bpm='108').status_code, 302)
        self.assertIn('108 BPM', self.member.get(f'/admin/gigs/{gig}/setlist').get_data(as_text=True))
        preset = self.owner.get('/admin/songs/import?preset=pt-reyes').get_data(as_text=True)
        self.assertIn('Ain’t No Sunshine', preset)
        self.assertIn('Fire On The Bayou', preset)

    def test_pdf_exact_order_retries_backups_and_preserves_existing_data(self):
        from song_import import PDF_PATH, parse_songs
        rows = parse_songs(PDF_PATH.read_text())
        self.assertEqual(len(rows), 35)
        self.assertTrue(all(not row['artist'] and not row['lead'] for row in rows))
        self.assertTrue(all(row['set_number'] == 1 for row in rows))
        self.assertEqual([rows[i]['title'] for i in (16,17,32,34)], ['Let’s Go Back','Let’s Go Back','Fire on the Bayou','Fire On The Bayou'])
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            gig = conn.execute("INSERT INTO gigs(day,venue,location) VALUES ('2099-12-01','Old Western Saloon','Pt. Reyes')").lastrowid
            conn.execute("INSERT INTO songs(title,artist,song_key,lead,status,reference,notes) VALUES (?,?,?,?,?,?,?)",
                         ("Ain't No Sunshine", 'Existing artist', 'Gm', 'Rachel', 'working', 'https://example.com/song', 'Keep this arrangement'))
            preserved = {table:conn.execute(f'SELECT * FROM {table}').fetchall() for table in ('users','gigs','rehearsals','availability','settings')}
        path = '/admin/songs/import'
        form = self.owner.get(path+'?preset=pt-reyes').get_data(as_text=True)
        self.assertIn(f'value="{gig}" selected',form)
        self.assertEqual(self.post(self.owner, path, preset='pt-reyes', gig_id=str(gig), action='save').status_code, 302)
        with sqlite3.connect(Path(self.tmp.name, 'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0],33)
            entries = conn.execute('SELECT title,song_key,bpm,set_number,position,artist FROM setlist_entries WHERE gig_id=? ORDER BY set_number,position,id',(gig,)).fetchall()
            self.assertEqual([r[0] for r in entries],[r['title'] for r in rows])
            self.assertEqual([r[2] for r in entries],[r['bpm'] for r in rows])
            self.assertEqual([r[4] for r in entries],list(range(1,36)))
            self.assertEqual(entries[0][1], 'Am')
            self.assertEqual(entries[0][5], 'Existing artist')
            self.assertEqual(conn.execute('SELECT artist,song_key,lead,status,reference,notes,bpm FROM songs WHERE id=1').fetchone(),
                             ('Existing artist','Gm','Rachel','ready','https://example.com/song','Keep this arrangement',80))
            for table, before in preserved.items():
                self.assertEqual(conn.execute(f'SELECT * FROM {table}').fetchall(),before)
        backups = list(Path(self.tmp.name,'import-backups').glob('*.sqlite3'))
        self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].stat().st_mode & 0o777,0o600)
        with sqlite3.connect(backups[0]) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM setlist_entries').fetchone()[0],0)
        self.assertEqual(self.post(self.owner,path,preset='pt-reyes',gig_id=str(gig),action='save').status_code,302)
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM setlist_entries').fetchone()[0],35)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM song_import_runs').fetchone()[0],1)
        self.assertEqual(len(list(Path(self.tmp.name,'import-backups').glob('*.sqlite3'))),1)

    def test_set_breaks_and_snapshot_bpm_independence(self):
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            gig=conn.execute("INSERT INTO gigs(day,venue,location) VALUES ('2099-12-01','Test','Town')").lastrowid
        text='Title\tArtist\tKey\tLead\tBPM\tNotes\tSet\nFirst\t\tAm\t\t80\t\t1\nSecond\t\tF\t\t90\t\t1\nFirst\t\tG\t\t100\tReprise\t2'
        self.assertEqual(self.post(self.owner,'/admin/songs/import',text=text,gig_id=str(gig),action='save').status_code,302)
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT title,set_number,position,bpm FROM setlist_entries ORDER BY set_number,position').fetchall(),[('First',1,1,80),('Second',1,2,90),('First',2,1,100)])
            conn.execute('UPDATE songs SET bpm=120')
        create_app(self.config)
        page=self.member.get(f'/admin/gigs/{gig}/setlist').get_data(as_text=True)
        self.assertIn('Set 2',page)
        self.assertIn('100 BPM',page)
        self.assertNotIn('120 BPM',page)
        for text in ['A,,,,80,,2\nB,,,,90,,1','A,,,,80,,0','A,,,,80,,21','A,,,,80,,oops']:
            self.assertEqual(self.post(self.owner,'/admin/songs/import',text=text,action='save').status_code,400)

    def test_preview_cannot_be_bypassed_or_reused_after_changes(self):
        path='/admin/songs/import'
        csrf=self.csrf(self.owner)
        fields={'csrf':csrf,'text':'First,,,,80','action':'save'}
        self.assertEqual(self.owner.post(path,data=fields).status_code,400)
        preview=self.owner.post(path,data={**fields,'action':'preview'})
        token=re.search(r'name="preview_token" value="([^"]+)"',preview.get_data(as_text=True))[1]
        self.assertEqual(self.owner.post(path,data={**fields,'preview_token':token,'text':'Changed,,,,80'}).status_code,400)
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            conn.execute("INSERT INTO songs(title,status) VALUES ('Other change','ready')")
        self.assertEqual(self.owner.post(path,data={**fields,'preview_token':token}).status_code,400)
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT title FROM songs').fetchall(),[('Other change',)])

    def test_pdf_refuses_wrong_gig_and_nonempty_setlist(self):
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            wrong=conn.execute("INSERT INTO gigs(day,venue,location) VALUES ('2099-01-01','Other venue','Town')").lastrowid
            gig=conn.execute("INSERT INTO gigs(day,venue,location) VALUES ('2099-02-01','Old Western Saloon','Pt Reyes')").lastrowid
            conn.execute("INSERT INTO setlist_entries(gig_id,set_number,position,title,artist,song_key,lead,notes) VALUES (?,1,1,'Existing','','','','Keep')",(gig,))
        for ident in ('',str(wrong),str(gig)):
            self.assertEqual(self.post(self.owner,'/admin/songs/import',preset='pt-reyes',gig_id=ident,action='save').status_code,400)
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT title,notes FROM setlist_entries').fetchall(),[('Existing','Keep')])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0],0)

    def test_bulk_ambiguous_duplicates_artist_conflicts_and_atomicity(self):
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            conn.executemany('INSERT INTO songs(title,artist,status) VALUES (?,?,?)',[('Crazy','Artist A','ready'),('Crazy','Artist B','ready')])
        for text in ['New song\nCrazy', 'New song\nCrazy,Artist C']:
            self.assertEqual(self.post(self.owner,'/admin/songs/import',text=text,action='save').status_code,400)
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0],2)
        self.assertEqual(self.post(self.owner,'/admin/songs/import',text='Crazy,Artist A,,,80',action='save').status_code,302)
        self.assertEqual(self.post(self.owner,'/admin/songs/import',text='Other title,Artist C\nOther title,Artist D',action='save').status_code,400)
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0],2)

    def test_existing_setlist_append_requires_confirmation_and_preserves_history(self):
        path='/admin/songs/import'
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            gig=conn.execute("INSERT INTO gigs(day,venue,location) VALUES ('2099-01-01','Test','Town')").lastrowid
            conn.execute("INSERT INTO setlist_entries(gig_id,set_number,position,title,artist,song_key,lead,notes,bpm) VALUES (?,1,1,'Existing','Artist','F','Rob','History',90)",(gig,))
        fields={'text':'New,,,,100','gig_id':str(gig),'action':'save'}
        self.assertEqual(self.post(self.owner,path,**fields).status_code,400)
        self.assertEqual(self.post(self.owner,path,append='yes',**fields).status_code,302)
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT title,position,bpm FROM setlist_entries ORDER BY position').fetchall(),[('Existing',1,90),('New',2,100)])
        self.assertEqual(self.post(self.owner,path,append='yes',**fields).status_code,302)
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM setlist_entries').fetchone()[0],2)

    def test_backup_failure_aborts_without_saving_records(self):
        from unittest.mock import patch
        path='/admin/songs/import'
        with patch('song_import.backup_database',side_effect=OSError('Disk unavailable')):
            response = self.post(self.owner,path,text='Never saved,,,,80',action='save')
            self.assertEqual(response.status_code,400)
            self.assertIn('Nothing was imported',response.get_data(as_text=True))
        with sqlite3.connect(Path(self.tmp.name,'band.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM songs').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM song_import_runs').fetchone()[0],0)
