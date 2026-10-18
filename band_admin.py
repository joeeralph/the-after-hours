"""Individual accounts and the private rehearsal / availability calendar."""
import calendar
import hashlib
import secrets
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

from flask import abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


MEMBERS = [
    ('rachel', 'Rachel Tsacle', 'member'),
    ('rob', 'Rob Headlee', 'admin'),
    ('bryan', 'Bryan Weinberg', 'member'),
    ('zusje', 'Zusje Helderman', 'member'),
    ('joe', 'Joe Ralph', 'admin'),
    ('nasser', 'Nasser Addi', 'member'),
]


def initialize_accounts(conn, legacy_password):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            role TEXT NOT NULL, password_hash TEXT, active INTEGER NOT NULL DEFAULT 1,
            version INTEGER NOT NULL DEFAULT 1, setup_hash TEXT, setup_expires INTEGER);
        CREATE TABLE IF NOT EXISTS rehearsals (
            id INTEGER PRIMARY KEY, day TEXT NOT NULL, start TEXT NOT NULL,
            finish TEXT NOT NULL, location TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS availability (
            user_id INTEGER NOT NULL REFERENCES users(id), day TEXT NOT NULL,
            status TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(user_id, day));
        CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, artist TEXT NOT NULL DEFAULT '',
            song_key TEXT NOT NULL DEFAULT '', lead TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK(status IN ('ready', 'working')),
            reference TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '');
    ''')
    for username, name, role in MEMBERS:
        conn.execute('INSERT OR IGNORE INTO users(username, name, role) VALUES (?, ?, ?)',
                     (username, name, role))
    if not conn.execute("SELECT 1 FROM settings WHERE name='rob_admin_v1'").fetchone():
        conn.execute("UPDATE users SET role='admin' WHERE username='rob'")
        conn.execute("INSERT INTO settings VALUES ('rob_admin_v1', '1')")
    if legacy_password.exists():
        conn.execute("UPDATE users SET password_hash=? WHERE username='joe' AND password_hash IS NULL",
                     (legacy_password.read_text().strip(),))


def register_band_admin(app, db, current_user, private, admin_only):
    @app.get('/admin/songs')
    @private
    def songs():
        query = request.args.get('q', '').strip()[:160]
        with db() as conn:
            rows = conn.execute('SELECT * FROM songs ORDER BY title COLLATE NOCASE, artist COLLATE NOCASE, id').fetchall()
        if query:
            rows = [song for song in rows if query.casefold() in ' '.join(
                (song['title'], song['artist'], song['lead'], song['notes'])).casefold()]
        return render_template('songs.html', songs=rows, query=query)

    @app.post('/admin/songs/save')
    @admin_only
    def save_song():
        try:
            item_id = int(request.form['id']) if request.form.get('id') else None
            if item_id is not None and item_id < 1:
                raise ValueError
        except ValueError:
            abort(400)
        fields = ('title', 'artist', 'song_key', 'lead', 'status', 'reference', 'notes')
        values = {name: request.form.get(name, '').strip() for name in fields}
        error = None
        if not values['title'] or len(values['title']) > 160:
            error = 'Enter a song title of up to 160 characters.'
        elif values['status'] not in ('ready', 'working'):
            error = 'Choose Song list or Working list.'
        elif any(len(values[name]) > maximum for name, maximum in
                 [('artist', 160), ('song_key', 40), ('lead', 160), ('reference', 2000), ('notes', 4000)]):
            error = 'One of the fields is too long. Please shorten it and try again.'
        elif values['reference']:
            try:
                parsed = urlparse(values['reference'])
                if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                    error = 'Use a full HTTPS reference link, without a username or password.'
            except ValueError:
                error = 'Use a valid HTTPS reference link.'
        if error:
            return render_template('song_form.html', song=values, item_id=item_id, error=error), 400
        with db() as conn:
            if item_id:
                result = conn.execute('UPDATE songs SET title=?, artist=?, song_key=?, lead=?, status=?, reference=?, notes=? WHERE id=?',
                                      (*[values[name] for name in fields], item_id))
                if result.rowcount != 1:
                    abort(404)
            else:
                conn.execute('INSERT INTO songs(title, artist, song_key, lead, status, reference, notes) VALUES (?, ?, ?, ?, ?, ?, ?)',
                             [values[name] for name in fields])
        flash('Song saved. The band can see it in the private song lists.')
        return redirect(url_for('songs', _anchor=values['status']))

    @app.get('/admin/songs/<int:item_id>/edit')
    @admin_only
    def edit_song(item_id):
        with db() as conn:
            song = conn.execute('SELECT * FROM songs WHERE id=?', (item_id,)).fetchone()
        if not song:
            abort(404)
        return render_template('song_form.html', song=song, item_id=item_id)

    @app.post('/admin/songs/<int:item_id>/delete')
    @admin_only
    def delete_song(item_id):
        with db() as conn:
            result = conn.execute('DELETE FROM songs WHERE id=?', (item_id,))
            if result.rowcount != 1:
                abort(404)
        flash('Song removed from the private lists.')
        return redirect(url_for('songs'))

    def today():
        return datetime.now(ZoneInfo('America/Los_Angeles')).date()

    @app.get('/admin/calendar')
    @private
    def band_calendar():
        try:
            selected = date.fromisoformat(request.args.get('day', today().isoformat()))
            month = datetime.strptime(request.args.get('month', selected.strftime('%Y-%m')), '%Y-%m').date().replace(day=1)
            if not 1901 <= month.year <= 9998:
                raise ValueError
            if (selected.year, selected.month) != (month.year, month.month):
                selected = month
        except ValueError:
            abort(400)
        last = month.replace(day=calendar.monthrange(month.year, month.month)[1])
        with db() as conn:
            members = conn.execute('SELECT id, name, username FROM users WHERE active=1 ORDER BY name').fetchall()
            rehearsals = conn.execute('SELECT * FROM rehearsals WHERE day BETWEEN ? AND ? ORDER BY day, start',
                                      (month.isoformat(), last.isoformat())).fetchall()
            gigs = conn.execute('SELECT * FROM gigs WHERE day BETWEEN ? AND ? ORDER BY day',
                                (month.isoformat(), last.isoformat())).fetchall()
            responses = conn.execute('SELECT * FROM availability WHERE day BETWEEN ? AND ?',
                                     (month.isoformat(), last.isoformat())).fetchall()
        availability = {(r['day'], r['user_id']): dict(r) for r in responses}
        events = {}
        for rehearsal in rehearsals:
            events.setdefault(rehearsal['day'], []).append({'label': rehearsal['start'] + ' Rehearsal', 'kind': 'rehearsal'})
        for gig in gigs:
            events.setdefault(gig['day'], []).append({'label': gig['venue'], 'kind': 'gig'})
        cells = []
        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month):
            cells.append([{'date': day, 'key': day.isoformat(), 'in_month': day.month == month.month,
                           'events': events.get(day.isoformat(), []),
                           'unavailable': sum(availability.get((day.isoformat(), m['id']), {}).get('status') == 'unavailable'
                                              for m in members)} for day in week])
        return render_template('calendar.html', month=month, selected=selected, today=today(), weeks=cells,
                               previous=(month - timedelta(days=1)).strftime('%Y-%m'),
                               following=(last + timedelta(days=1)).strftime('%Y-%m'),
                               members=members, rehearsals=rehearsals, gigs=gigs, availability=availability)

    @app.post('/admin/availability')
    @private
    def save_availability():
        try:
            start = date.fromisoformat(request.form['day'])
            end = date.fromisoformat(request.form.get('end_day') or request.form['day'])
            days = (end - start).days + 1
            if not 1 <= days <= 366 or not 1901 <= start.year <= end.year <= 9998:
                raise ValueError
            status = request.form['status']
            if status not in ('available', 'unavailable', 'maybe', 'unknown'):
                raise ValueError
            user_id = current_user()['id']
            with db() as conn:
                if status == 'unknown':
                    conn.execute('DELETE FROM availability WHERE user_id=? AND day BETWEEN ? AND ?',
                                 (user_id, start.isoformat(), end.isoformat()))
                else:
                    notes = request.form.get('notes', '').strip()[:500]
                    conn.executemany('INSERT OR REPLACE INTO availability VALUES (?, ?, ?, ?)',
                                     [(user_id, (start + timedelta(days=offset)).isoformat(), status, notes)
                                      for offset in range(days)])
            flash('Your availability is saved.' if days == 1 else f'Your availability is saved for all {days} days, including the start and end dates.')
            return redirect(url_for('band_calendar', day=start.isoformat()))
        except (ValueError, KeyError):
            abort(400, description='Choose valid start and end dates, with the end on or after the start, up to 366 days per save, and a valid availability status.')

    @app.post('/admin/rehearsals/save')
    @admin_only
    def save_rehearsal():
        try:
            day = date.fromisoformat(request.form['day']).isoformat()
            start = datetime.strptime(request.form['start'], '%H:%M').strftime('%H:%M')
            finish = datetime.strptime(request.form['finish'], '%H:%M').strftime('%H:%M')
            location = request.form['location'].strip()[:200]
            if finish <= start or not location:
                raise ValueError
            values = (day, start, finish, location, request.form.get('notes', '').strip()[:2000])
            with db() as conn:
                if request.form.get('id'):
                    result = conn.execute('UPDATE rehearsals SET day=?, start=?, finish=?, location=?, notes=? WHERE id=?',
                                          (*values, int(request.form['id'])))
                    if result.rowcount != 1:
                        abort(404)
                else:
                    conn.execute('INSERT INTO rehearsals(day, start, finish, location, notes) VALUES (?, ?, ?, ?, ?)', values)
            flash('Rehearsal saved. Check the band’s availability below; unanswered dates are not confirmed.')
            return redirect(url_for('band_calendar', day=day))
        except (ValueError, KeyError):
            flash('Rehearsal not saved. Enter a valid date, location and end time later than the start time on the same day.')
            return redirect(url_for('band_calendar'))

    @app.post('/admin/rehearsals/<int:item_id>/delete')
    @admin_only
    def delete_rehearsal(item_id):
        with db() as conn:
            conn.execute('DELETE FROM rehearsals WHERE id=?', (item_id,))
        flash('Rehearsal removed.')
        return redirect(url_for('band_calendar'))

    @app.get('/admin/accounts')
    @admin_only
    def accounts():
        with db() as conn:
            members = conn.execute('SELECT id, name, username, role, active, password_hash IS NOT NULL AS ready FROM users ORDER BY name').fetchall()
        return render_template('accounts.html', members=members)

    @app.post('/admin/accounts/<int:user_id>/invite')
    @admin_only
    def invite(user_id):
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with db() as conn:
            member = conn.execute('SELECT * FROM users WHERE id=? AND active=1', (user_id,)).fetchone()
            if not member:
                abort(404)
            conn.execute('UPDATE users SET setup_hash=?, setup_expires=? WHERE id=?', (digest, int(time.time()) + 86400, user_id))
        return render_template('invite.html', member=member, setup_url=url_for('setup_account', token=token, _external=True))

    @app.post('/admin/accounts/<int:user_id>/toggle')
    @admin_only
    def toggle_account(user_id):
        with db() as conn:
            member = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if not member or member['role'] == 'admin':
                abort(400)
            conn.execute('UPDATE users SET active=1-active, version=version+1, setup_hash=NULL, setup_expires=NULL WHERE id=?', (user_id,))
        flash('Account access updated. Previous sessions and setup links have been revoked.')
        return redirect(url_for('accounts'))

    @app.route('/admin/setup/<token>', methods=['GET', 'POST'])
    def setup_account(token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with db() as conn:
            member = conn.execute('SELECT * FROM users WHERE setup_hash=? AND setup_expires>? AND active=1',
                                  (digest, int(time.time()))).fetchone()
            if not member:
                return render_template('setup.html', invalid=True), 400
            if request.method == 'POST':
                password = request.form.get('password', '')
                if len(password) < 12 or password != request.form.get('confirm'):
                    return render_template('setup.html', member=member, error='Use at least 12 characters and enter the same password twice.'), 400
                result = conn.execute('UPDATE users SET password_hash=?, version=version+1, setup_hash=NULL, setup_expires=NULL WHERE id=? AND setup_hash=? AND setup_expires>? AND active=1',
                                      (generate_password_hash(password), member['id'], digest, int(time.time())))
                if result.rowcount != 1:
                    abort(400)
                session.clear()
                flash('Password saved. Sign in with your username and new password.')
                return redirect(url_for('login'))
        return render_template('setup.html', member=member)

    @app.route('/admin/password', methods=['GET', 'POST'])
    @private
    def change_password():
        if request.method == 'POST':
            user = current_user()
            password = request.form.get('password', '')
            if not check_password_hash(user['password_hash'], request.form.get('current', '')) or len(password) < 12 or password != request.form.get('confirm'):
                return render_template('password.html', error='Check your current password. New passwords must match and have at least 12 characters.'), 400
            with db() as conn:
                conn.execute('UPDATE users SET password_hash=?, version=version+1, setup_hash=NULL, setup_expires=NULL WHERE id=?',
                             (generate_password_hash(password), user['id']))
            session['version'] = user['version'] + 1
            flash('Password changed. Other signed-in sessions have been signed out.')
            return redirect(url_for('band_calendar'))
        return render_template('password.html')
