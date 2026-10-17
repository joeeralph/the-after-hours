"""Individual accounts and the private rehearsal / availability calendar."""
import calendar
import hashlib
import secrets
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


MEMBERS = [
    ('rachel', 'Rachel Tsacle', 'member'),
    ('rob', 'Rob Headlee', 'member'),
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
    ''')
    for username, name, role in MEMBERS:
        conn.execute('INSERT OR IGNORE INTO users(username, name, role) VALUES (?, ?, ?)',
                     (username, name, role))
    if legacy_password.exists():
        conn.execute("UPDATE users SET password_hash=? WHERE username='joe' AND password_hash IS NULL",
                     (legacy_password.read_text().strip(),))


def register_band_admin(app, db, current_user, private, admin_only):
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
            day = date.fromisoformat(request.form['day']).isoformat()
            status = request.form['status']
            if status not in ('available', 'unavailable', 'maybe', 'unknown'):
                raise ValueError
            with db() as conn:
                if status == 'unknown':
                    conn.execute('DELETE FROM availability WHERE user_id=? AND day=?', (current_user()['id'], day))
                else:
                    conn.execute('INSERT OR REPLACE INTO availability VALUES (?, ?, ?, ?)',
                                 (current_user()['id'], day, status, request.form.get('notes', '').strip()[:500]))
            flash('Your availability is saved.')
            return redirect(url_for('band_calendar', day=day))
        except (ValueError, KeyError):
            abort(400)

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
