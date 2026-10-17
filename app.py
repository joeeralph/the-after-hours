import getpass
import os
import secrets
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import click
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(DATA_DIR=os.environ.get('BAND_DATA_DIR', str(Path(__file__).parent / 'data')),
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=os.environ.get('BAND_HTTPS') == '1', MAX_CONTENT_LENGTH=64 * 1024)
    if test_config:
        app.config.update(test_config)
    data = Path(app.config['DATA_DIR'])
    data.mkdir(parents=True, exist_ok=True)
    key = data / 'session.key'
    if not key.exists():
        with key.open('x') as f:
            os.chmod(key, 0o600)
            f.write(secrets.token_hex(32))
    app.secret_key = key.read_text().strip()

    def db():
        connection = sqlite3.connect(data / 'band.sqlite3')
        connection.row_factory = sqlite3.Row
        return connection

    with db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS gigs (id INTEGER PRIMARY KEY, day TEXT NOT NULL,
          venue TEXT NOT NULL, location TEXT NOT NULL, link TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS videos (id INTEGER PRIMARY KEY, title TEXT NOT NULL, video_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts (address TEXT PRIMARY KEY, count INTEGER NOT NULL, started INTEGER NOT NULL);
        ''')
        if 'is_short' not in {row['name'] for row in conn.execute('PRAGMA table_info(videos)')}:
            conn.execute('ALTER TABLE videos ADD COLUMN is_short INTEGER NOT NULL DEFAULT 0')
        if not conn.execute("SELECT 1 FROM settings WHERE name='initial_videos_added'").fetchone():
            for title, video_id, is_short in [
                ('The After Hours · Band video', '274AIJQ-sXM', 0),
                ('The After Hours · YouTube Short', '7JeuGzJA9nQ', 1),
            ]:
                if not conn.execute('SELECT 1 FROM videos WHERE video_id=?', (video_id,)).fetchone():
                    conn.execute('INSERT INTO videos(title, video_id, is_short) VALUES (?, ?, ?)',
                                 (title, video_id, is_short))
            conn.execute("INSERT INTO settings VALUES ('initial_videos_added', '1')")

    def settings():
        with db() as conn:
            return {'email': 'theafterhourssf@gmail.com',
                    **dict(conn.execute('SELECT name, value FROM settings').fetchall())}

    def csrf():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
        return session['csrf']

    app.jinja_env.globals['csrf_token'] = csrf

    @app.before_request
    def protect_posts():
        if request.method == 'POST' and not secrets.compare_digest(session.get('csrf', ''), request.form.get('csrf', '')):
            abort(400)
        if request.method == 'POST' and not session.get('csrf'):
            abort(400)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' https:; style-src 'self'; frame-src https://www.youtube-nocookie.com; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        if request.path.startswith('/admin'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    def private(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get('admin'):
                return redirect(url_for('login'))
            return view(*args, **kwargs)
        return wrapped

    def safe_url(value):
        value = value.strip()
        if value and (urlparse(value).scheme != 'https' or not urlparse(value).hostname):
            raise ValueError('Please use a full HTTPS link.')
        return value

    @app.get('/')
    def home():
        with db() as conn:
            gigs = conn.execute('SELECT * FROM gigs WHERE day >= ? ORDER BY day, id', (datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat(),)).fetchall()
            videos = conn.execute('SELECT * FROM videos ORDER BY id DESC').fetchall()
        return render_template('home.html', gigs=gigs, videos=videos, settings=settings())

    @app.route('/admin/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            import time
            address = request.remote_addr or 'unknown'
            now = int(time.time())
            with db() as conn:
                attempt = conn.execute('SELECT * FROM login_attempts WHERE address=?', (address,)).fetchone()
                if attempt and now - attempt['started'] < 900 and attempt['count'] >= 10:
                    return render_template('login.html', error='Too many attempts. Please try again in 15 minutes.'), 429
                password_file = data / 'admin.hash'
                if password_file.exists() and check_password_hash(password_file.read_text().strip(), request.form.get('password', '')):
                    conn.execute('DELETE FROM login_attempts WHERE address=?', (address,))
                    session.clear()
                    session['admin'] = True
                    return redirect(url_for('admin'))
                if not attempt or now - attempt['started'] >= 900:
                    conn.execute('INSERT OR REPLACE INTO login_attempts VALUES (?, 1, ?)', (address, now))
                else:
                    conn.execute('UPDATE login_attempts SET count=count+1 WHERE address=?', (address,))
            return render_template('login.html', error='Unable to sign in. Check your password or complete administrator setup.'), 401
        return render_template('login.html')

    @app.post('/admin/logout')
    @private
    def logout():
        session.clear()
        return redirect(url_for('home'))

    @app.get('/admin')
    @private
    def admin():
        with db() as conn:
            gigs = conn.execute('SELECT * FROM gigs ORDER BY day').fetchall()
            videos = conn.execute('SELECT * FROM videos ORDER BY id DESC').fetchall()
        return render_template('admin.html', gigs=gigs, videos=videos, settings=settings())

    @app.post('/admin/save/<kind>')
    @private
    def save(kind):
        try:
            with db() as conn:
                if kind == 'gig':
                    day = date.fromisoformat(request.form['day']).isoformat()
                    venue = request.form['venue'].strip()[:160]
                    location = request.form['location'].strip()[:160]
                    if not venue or not location:
                        raise ValueError('Enter a venue and location.')
                    values = (day, venue, location, safe_url(request.form.get('link', '')))
                    if request.form.get('id'):
                        conn.execute('UPDATE gigs SET day=?, venue=?, location=?, link=? WHERE id=?', (*values, int(request.form['id'])))
                    else:
                        conn.execute('INSERT INTO gigs(day, venue, location, link) VALUES (?, ?, ?, ?)', values)
                elif kind == 'video':
                    parsed = urlparse(safe_url(request.form['link']))
                    if parsed.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
                        video_id = parse_qs(parsed.query).get('v', [''])[0] if parsed.path == '/watch' else parsed.path.split('/')[-1] if parsed.path.startswith(('/shorts/', '/embed/', '/live/')) else ''
                    elif parsed.hostname == 'youtu.be':
                        video_id = parsed.path.lstrip('/')
                    else:
                        video_id = ''
                    import re
                    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                        raise ValueError('Paste a valid YouTube video link.')
                    title = request.form['title'].strip()[:160]
                    if not title:
                        raise ValueError('Enter a video title.')
                    is_short = int(parsed.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com') and parsed.path.startswith('/shorts/'))
                    if request.form.get('id'):
                        conn.execute('UPDATE videos SET title=?, video_id=?, is_short=? WHERE id=?', (title, video_id, is_short, int(request.form['id'])))
                    else:
                        conn.execute('INSERT INTO videos(title, video_id, is_short) VALUES (?, ?, ?)', (title, video_id, is_short))
                elif kind == 'settings':
                    for name in ('intro', 'email', 'photo', 'logo', 'youtube'):
                        value = request.form.get(name, '').strip()
                        if name in ('photo', 'logo', 'youtube'):
                            value = safe_url(value)
                        if name == 'email' and value and ('@' not in value or any(c in value for c in '\r\n?')):
                            raise ValueError('Enter a valid booking email.')
                        conn.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (name, value[:2000]))
                else:
                    abort(404)
            flash('Saved. Your website is updated.')
        except (ValueError, KeyError):
            flash('Please check the details. Dates must be valid; links must use HTTPS and videos must be valid YouTube links.')
        return redirect(url_for('admin'))

    @app.post('/admin/delete/<kind>/<int:item_id>')
    @private
    def delete(kind, item_id):
        if kind not in ('gigs', 'videos'):
            abort(404)
        with db() as conn:
            conn.execute(f'DELETE FROM {kind} WHERE id=?', (item_id,))
        flash('Removed from your website.')
        return redirect(url_for('admin'))

    @app.cli.command('set-admin-password')
    def set_admin_password():
        """Create or replace the private dashboard password interactively."""
        password = getpass.getpass('New admin password (at least 12 characters): ')
        confirmation = getpass.getpass('Confirm password: ')
        if len(password) < 12 or password != confirmation:
            raise click.ClickException('Passwords must match and contain at least 12 characters.')
        path = data / 'admin.hash'
        path.write_text(generate_password_hash(password))
        path.chmod(0o600)
        click.echo('Administrator password saved.')

    return app
