import re
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import create_app


class CalendarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {'TESTING': True, 'DATA_DIR': self.tmp.name}
        self.app = create_app(self.config)
        self.owner = self.app.test_client()
        self.member = self.app.test_client()
        self.other = self.app.test_client()
        with self.db() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE username IN ('joe','rachel','bryan')",
                         (generate_password_hash('test-band-password'),))
            self.ids = dict(conn.execute('SELECT username,id FROM users').fetchall())
        self.sign_in(self.owner, 'joe')
        self.sign_in(self.member, 'rachel')
        self.sign_in(self.other, 'bryan')

    def tearDown(self):
        self.tmp.cleanup()

    def db(self):
        return sqlite3.connect(Path(self.tmp.name, 'band.sqlite3'))

    def csrf(self, client, path='/admin/calendar'):
        page = client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', page)[1]

    def sign_in(self, client, username):
        response = client.post('/admin/login', data={
            'csrf': self.csrf(client, '/admin/login'), 'username': username, 'password': 'test-band-password'})
        self.assertEqual(response.status_code, 302)

    def post(self, client, path, data=None):
        return client.post(path, data={'csrf': self.csrf(client), **(data or {})})

    def rehearsal(self, **overrides):
        return {'day': '2099-07-14', 'start': '18:30', 'finish': '21:00',
                'location': 'Private rehearsal studio', 'notes': 'Private set list notes', **overrides}

    def test_calendar_private_and_members_cannot_manage_site_or_accounts(self):
        anonymous = self.app.test_client()
        for path in ['/admin/calendar', '/admin/accounts', '/admin/password']:
            self.assertEqual(anonymous.get(path).status_code, 302)
        self.assertEqual(self.member.get('/admin').location, '/admin/calendar')
        self.assertEqual(self.member.get('/admin/accounts').status_code, 403)
        for path, data in [('/admin/save/gig', {}), ('/admin/save/video', {}),
                           ('/admin/save/settings', {}), ('/admin/delete/videos/1', {}),
                           ('/admin/rehearsals/save', self.rehearsal()),
                           ('/admin/rehearsals/1/delete', {}),
                           (f"/admin/accounts/{self.ids['bryan']}/invite", {}),
                           (f"/admin/accounts/{self.ids['bryan']}/toggle", {})]:
            self.assertEqual(self.post(self.member, path, data).status_code, 403, path)
        self.assertEqual(self.owner.post('/admin/rehearsals/save', data=self.rehearsal()).status_code, 400)
        self.assertEqual(self.member.get('/admin/calendar').headers['Cache-Control'], 'no-store')

    def test_rob_admin_migrates_existing_account_and_can_manage_every_section(self):
        password_hash = generate_password_hash('test-band-password')
        with self.db() as conn:
            conn.execute("UPDATE users SET role='member', password_hash=? WHERE username='rob'", (password_hash,))
            conn.execute("DELETE FROM settings WHERE name='rob_admin_v1'")
        upgraded = create_app(self.config)
        rob = upgraded.test_client()
        self.sign_in(rob, 'rob')
        for path in ['/admin', '/admin/accounts', '/admin/calendar', '/admin/songs']:
            self.assertEqual(rob.get(path).status_code, 200, path)
        self.assertEqual(self.post(rob, '/admin/save/gig', {'day': '2099-07-14', 'venue': 'Rob’s venue', 'location': 'SF'}).status_code, 302)
        self.assertEqual(self.post(rob, '/admin/save/video', {'title': 'Rob’s video', 'link': 'https://youtu.be/274AIJQ-sXM'}).status_code, 302)
        self.assertEqual(self.post(rob, '/admin/save/settings', {'intro': 'Updated by Rob'}).status_code, 302)
        self.assertEqual(self.post(rob, '/admin/rehearsals/save', self.rehearsal()).status_code, 302)
        self.assertEqual(self.post(rob, '/admin/songs/save', {'title': 'Rob’s song', 'status': 'working'}).status_code, 302)
        self.assertEqual(self.post(rob, f"/admin/accounts/{self.ids['rachel']}/invite").status_code, 200)
        create_app(self.config)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT role,password_hash FROM users WHERE username='rob'").fetchone(), ('admin', password_hash))

    def test_availability_is_personal_and_unknown_is_not_available(self):
        page = self.member.get('/admin/calendar?day=2099-07-14').get_data(as_text=True)
        self.assertEqual(page.count('class="status unknown"'), 6)
        self.post(self.member, '/admin/availability', {'day': '2099-07-14', 'status': 'unavailable',
                  'notes': 'Away for the day', 'user_id': self.ids['bryan']})
        with self.db() as conn:
            row = conn.execute('SELECT user_id, status FROM availability').fetchone()
        self.assertEqual(row, (self.ids['rachel'], 'unavailable'))
        page = self.other.get('/admin/calendar?day=2099-07-14').get_data(as_text=True)
        self.assertIn('Away for the day', page)
        self.assertIn('1 unavailable', page)
        self.assertNotIn('Away for the day', self.owner.get('/').get_data(as_text=True))
        self.post(self.member, '/admin/availability', {'day': '2099-07-14', 'status': 'unknown'})
        with self.db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM availability').fetchone()[0], 0)
        self.assertEqual(self.post(self.member, '/admin/availability', {'day': 'bad', 'status': 'available'}).status_code, 400)

    def test_vacation_range_includes_both_dates_and_crosses_months(self):
        self.post(self.other, '/admin/availability', {'day': '2099-08-01', 'status': 'available'})
        result = self.post(self.member, '/admin/availability', {
            'day': '2099-07-29', 'end_day': '2099-08-04', 'status': 'unavailable',
            'notes': 'Vacation', 'user_id': self.ids['bryan']})
        self.assertEqual(result.status_code, 302)
        with self.db() as conn:
            rows = conn.execute('SELECT day, status, notes FROM availability WHERE user_id=? ORDER BY day',
                                (self.ids['rachel'],)).fetchall()
            self.assertEqual(len(rows), 7)
            self.assertEqual(rows[0], ('2099-07-29', 'unavailable', 'Vacation'))
            self.assertEqual(rows[-1], ('2099-08-04', 'unavailable', 'Vacation'))
            self.assertEqual(conn.execute('SELECT status FROM availability WHERE user_id=?', (self.ids['bryan'],)).fetchone()[0], 'available')
        for day in ('2099-07-29', '2099-08-04'):
            self.assertIn('Vacation', self.other.get('/admin/calendar?day=' + day).get_data(as_text=True))
        self.post(self.member, '/admin/availability', {'day': '2099-07-31', 'end_day': '2099-08-02', 'status': 'maybe', 'notes': 'Evenings only'})
        self.post(self.member, '/admin/availability', {'day': '2099-08-01', 'end_day': '2099-08-04', 'status': 'unknown'})
        with self.db() as conn:
            rows = conn.execute('SELECT day, status, notes FROM availability WHERE user_id=? ORDER BY day', (self.ids['rachel'],)).fetchall()
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[-1], ('2099-07-31', 'maybe', 'Evenings only'))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM availability WHERE user_id=?', (self.ids['bryan'],)).fetchone()[0], 1)

    def test_invalid_ranges_do_not_partially_change_availability(self):
        self.post(self.member, '/admin/availability', {'day': '2099-07-14', 'status': 'available'})
        for start, end in [('2099-07-15', '2099-07-14'), ('2099-07-14', 'not-a-date'),
                           ('2099-07-14', '2101-07-14')]:
            self.assertEqual(self.post(self.member, '/admin/availability', {
                'day': start, 'end_day': end, 'status': 'unavailable'}).status_code, 400)
        with self.db() as conn:
            self.assertEqual(conn.execute('SELECT day,status FROM availability').fetchall(), [('2099-07-14', 'available')])

    def test_rehearsal_crud_conflicts_gigs_and_private_notes(self):
        self.post(self.owner, '/admin/rehearsals/save', self.rehearsal())
        self.post(self.member, '/admin/availability', {'day': '2099-07-14', 'status': 'unavailable'})
        self.post(self.owner, '/admin/save/gig', {'day': '2099-07-14', 'venue': 'Public Gig', 'location': 'SF'})
        page = self.member.get('/admin/calendar?day=2099-07-14').get_data(as_text=True)
        for text in ['Private rehearsal studio', 'Private set list notes', 'Rachel Tsacle: Unavailable', 'Public Gig', '18:30–21:00']:
            self.assertIn(text, page)
        public = self.owner.get('/').get_data(as_text=True)
        self.assertIn('Public Gig', public)
        self.assertNotIn('Private rehearsal studio', public)
        self.assertNotIn('Private set list notes', public)
        self.post(self.owner, '/admin/rehearsals/save', self.rehearsal(id='1', location='Changed studio'))
        self.assertIn('Changed studio', self.member.get('/admin/calendar?day=2099-07-14').get_data(as_text=True))
        self.post(self.owner, '/admin/rehearsals/save', self.rehearsal(start='22:00', finish='18:00'))
        with self.db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM rehearsals').fetchone()[0], 1)
        self.post(self.owner, '/admin/rehearsals/1/delete')
        self.assertNotIn('Changed studio', self.member.get('/admin/calendar?day=2099-07-14').get_data(as_text=True))

    def test_rehearsal_accepts_am_pm_and_keeps_details_on_error(self):
        for start, finish, expected in [('7:00 Pm', '9:00 pm', ('19:00', '21:00')),
                                        ('7 PM', '9 PM', ('19:00', '21:00')),
                                        ('19:00', '21:00', ('19:00', '21:00')),
                                        ('12:00 AM', '12:00 PM', ('00:00', '12:00')),
                                        ('7:00\u202fPM', '9:00\u00a0PM', ('19:00', '21:00'))]:
            self.assertEqual(self.post(self.owner, '/admin/rehearsals/save', self.rehearsal(start=start, finish=finish)).status_code, 302)
            with self.db() as conn:
                self.assertEqual(conn.execute('SELECT start,finish FROM rehearsals ORDER BY id DESC LIMIT 1').fetchone(), expected)
        for start, finish in [('7:00 PM', '6:00 PM'), ('25:00', '26:00')]:
            response = self.post(self.owner, '/admin/rehearsals/save', self.rehearsal(start=start, finish=finish))
            self.assertEqual(response.status_code, 400)
            page = response.get_data(as_text=True)
            for value in [start, finish, 'Private rehearsal studio', 'Private set list notes', '2099-07-14']:
                self.assertIn(value, page)
        with self.db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM rehearsals').fetchone()[0], 5)

    def test_rehearsal_time_dropdowns_have_defaults_and_preserve_saved_times(self):
        page = self.owner.get('/admin/calendar').get_data(as_text=True)
        self.assertIn('<select name="start" required>', page)
        self.assertIn('<select name="finish" required>', page)
        self.assertRegex(page, r'value="19:00" selected>7:00 PM</option>')
        self.assertRegex(page, r'value="22:30" selected>10:30 PM</option>')
        self.post(self.owner, '/admin/rehearsals/save', self.rehearsal(start='18:15', finish='20:45'))
        page = self.owner.get('/admin/calendar?day=2099-07-14').get_data(as_text=True)
        self.assertRegex(page, r'value="18:15" selected>6:15 PM</option>')
        self.assertRegex(page, r'value="20:45" selected>8:45 PM</option>')
        self.post(self.owner, '/admin/rehearsals/save', self.rehearsal(id='1', start='18:10', finish='20:40'))
        page = self.owner.get('/admin/calendar?day=2099-07-14').get_data(as_text=True)
        self.assertIn('<option value="18:10" selected>', page)
        self.assertIn('<option value="20:40" selected>', page)

    def test_setup_links_are_one_use_and_reset_revokes_existing_sessions(self):
        invite = self.post(self.owner, f"/admin/accounts/{self.ids['rachel']}/invite")
        link = re.search(r'value="(http[^"]+/admin/setup/[^"]+)"', invite.get_data(as_text=True))[1]
        path = '/admin/setup/' + link.rsplit('/', 1)[-1]
        guest = self.app.test_client()
        token = self.csrf(guest, path)
        self.assertEqual(guest.post(path, data={'csrf': token, 'password': 'short', 'confirm': 'short'}).status_code, 400)
        result = guest.post(path, data={'csrf': token, 'password': 'new-member-password', 'confirm': 'new-member-password'})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(guest.get(path).status_code, 400)
        self.assertEqual(self.member.get('/admin/calendar').status_code, 302)
        token = self.csrf(guest, '/admin/login')
        self.assertEqual(guest.post('/admin/login', data={'csrf': token, 'username': 'rachel', 'password': 'new-member-password'}).location, '/admin/calendar')
        self.assertEqual(guest.get('/admin/calendar').status_code, 200)

    def test_new_invites_expiry_and_disabled_accounts(self):
        def link():
            result = self.post(self.owner, f"/admin/accounts/{self.ids['bryan']}/invite")
            return '/admin/setup/' + re.search(r'value="http[^"]+/admin/setup/([^"/]+)"', result.get_data(as_text=True))[1]
        first, second = link(), link()
        guest = self.app.test_client()
        self.assertEqual(guest.get(first).status_code, 400)
        self.assertEqual(guest.get(second).status_code, 200)
        with self.db() as conn:
            conn.execute('UPDATE users SET setup_expires=? WHERE username=?', (int(time.time()) - 1, 'bryan'))
        self.assertEqual(guest.get(second).status_code, 400)
        third = link()
        self.post(self.owner, f"/admin/accounts/{self.ids['bryan']}/toggle")
        self.assertEqual(self.other.get('/admin/calendar').status_code, 302)
        self.assertEqual(guest.get(third).status_code, 400)
        self.assertEqual(self.post(self.owner, f"/admin/accounts/{self.ids['joe']}/toggle").status_code, 400)

    def test_change_password_and_restart_preserve_data(self):
        self.post(self.member, '/admin/availability', {'day': '2099-07-14', 'status': 'maybe', 'notes': 'Free after 7'})
        self.post(self.owner, '/admin/rehearsals/save', self.rehearsal())
        result = self.member.post('/admin/password', data={'csrf': self.csrf(self.member), 'current': 'test-band-password',
                                  'password': 'my-new-band-password', 'confirm': 'my-new-band-password'})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(self.member.get('/admin/calendar').status_code, 200)
        restarted = create_app(self.config)
        with restarted.test_client() as client:
            token = self.csrf(client, '/admin/login')
            self.assertEqual(client.post('/admin/login', data={'csrf': token, 'username': 'rachel', 'password': 'my-new-band-password'}).status_code, 302)
            page = client.get('/admin/calendar?day=2099-07-14').get_data(as_text=True)
            self.assertIn('Free after 7', page)
            self.assertIn('Private rehearsal studio', page)
        with self.db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0], 6)


if __name__ == '__main__':
    unittest.main()
