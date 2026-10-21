"""Private gig setlists with independent copies of song details."""
import hashlib
import json
from collections import defaultdict

from flask import abort, flash, redirect, render_template, request, url_for


def order_version(entries):
    order = [(row['id'], row['set_number'], row['position']) for row in entries]
    return hashlib.sha256(json.dumps(order).encode()).hexdigest()


def initialize_setlists(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS setlist_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gig_id INTEGER NOT NULL REFERENCES gigs(id) ON DELETE CASCADE,
        set_number INTEGER NOT NULL, position INTEGER NOT NULL,
        title TEXT NOT NULL, artist TEXT NOT NULL, song_key TEXT NOT NULL,
        lead TEXT NOT NULL, notes TEXT NOT NULL)''')

    conn.execute('''CREATE TABLE IF NOT EXISTS song_import_runs (
        import_key TEXT PRIMARY KEY, gig_id INTEGER REFERENCES gigs(id) ON DELETE CASCADE,
        row_count INTEGER NOT NULL, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    if 'bpm' not in {row['name'] for row in conn.execute('PRAGMA table_info(setlist_entries)')}:
        conn.execute('ALTER TABLE setlist_entries ADD COLUMN bpm INTEGER CHECK(bpm BETWEEN 1 AND 400)')


def register_setlists(app, db, current_user, private, admin_only):
    def gig_or_404(conn, gig_id):
        gig = conn.execute('SELECT * FROM gigs WHERE id=?', (gig_id,)).fetchone()
        if not gig:
            abort(404)
        return gig

    def number(value, maximum):
        try:
            result = int(value)
        except (ValueError, TypeError):
            abort(400, description='Enter valid set and song position numbers.')
        if not 1 <= result <= maximum:
            abort(400, description=f'Choose a number from 1 to {maximum}.')
        return result

    @app.get('/admin/setlists')
    @private
    def setlists():
        with db() as conn:
            gigs = conn.execute('''SELECT gigs.*, COUNT(setlist_entries.id) AS song_count
                FROM gigs LEFT JOIN setlist_entries ON gigs.id=setlist_entries.gig_id
                GROUP BY gigs.id ORDER BY gigs.day DESC, gigs.id DESC''').fetchall()
        return render_template('setlists.html', gigs=gigs)

    @app.get('/admin/gigs/<int:gig_id>/setlist')
    @private
    def gig_setlist(gig_id):
        with db() as conn:
            gig = gig_or_404(conn, gig_id)
            entries = conn.execute('SELECT * FROM setlist_entries WHERE gig_id=? ORDER BY set_number, position, id', (gig_id,)).fetchall()
            songs = conn.execute("SELECT * FROM songs WHERE status='ready' ORDER BY title COLLATE NOCASE, id").fetchall() if current_user()['role'] == 'admin' else []
            sources = conn.execute('''SELECT DISTINCT gigs.* FROM gigs JOIN setlist_entries ON gigs.id=setlist_entries.gig_id
                WHERE gigs.id != ? ORDER BY gigs.day DESC, gigs.id DESC''', (gig_id,)).fetchall()
        groups = defaultdict(list)
        for entry in entries:
            groups[entry['set_number']].append(entry)
        return render_template('setlist.html', gig=gig, groups=groups, songs=songs, sources=sources, song_count=len(entries), order_version=order_version(entries))

    @app.get('/admin/gigs/<int:gig_id>/setlist/print')
    @private
    def print_setlist(gig_id):
        with db() as conn:
            gig = gig_or_404(conn, gig_id)
            entries = conn.execute('SELECT * FROM setlist_entries WHERE gig_id=? ORDER BY set_number,position,id', (gig_id,)).fetchall()
        groups = defaultdict(list)
        for entry in entries:
            groups[entry['set_number']].append(entry)
        return render_template('setlist_print.html', gig=gig, groups=groups, song_count=len(entries))

    @app.post('/admin/gigs/<int:gig_id>/setlist/<int:entry_id>/set-break')
    @admin_only
    def set_setlist_break(gig_id, entry_id):
        start_set = request.form.get('start_set')
        if start_set not in ('yes', 'no'):
            abort(400, description='Choose whether this song starts a set.')
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            gig_or_404(conn, gig_id)
            entries = conn.execute('SELECT * FROM setlist_entries WHERE gig_id=? ORDER BY set_number,position,id', (gig_id,)).fetchall()
            index = next((i for i, row in enumerate(entries) if row['id'] == entry_id), None)
            if index is None:
                abort(404)
            if request.form.get('order_version') != order_version(entries):
                abort(409, description='The setlist order changed. Reload the setlist before changing a set break.')
            if index == 0:
                abort(400, description='The first song already starts the first set.')
            breaks = {i for i in range(1, len(entries)) if entries[i]['set_number'] != entries[i-1]['set_number']}
            if start_set == 'yes':
                breaks.add(index)
            else:
                breaks.discard(index)
            if len(breaks) >= 20:
                abort(400, description='A setlist can have up to 20 sets.')
            set_number, position = 1, 0
            for i, entry in enumerate(entries):
                if i in breaks:
                    set_number += 1
                    position = 0
                position += 1
                conn.execute('UPDATE setlist_entries SET set_number=?,position=? WHERE id=? AND gig_id=?',
                             (set_number, position, entry['id'], gig_id))
        flash('Set break updated. Song order and song details are unchanged.')
        return redirect(url_for('gig_setlist', gig_id=gig_id))

    @app.post('/admin/gigs/<int:gig_id>/setlist/add')
    @admin_only
    def add_setlist_songs(gig_id):
        set_number = number(request.form.get('set_number'), 20)
        song_ids = list(dict.fromkeys(request.form.getlist('song_ids')))
        if not 1 <= len(song_ids) <= 100:
            abort(400, description='Select between 1 and 100 songs from the song list.')
        song_ids = [number(value, 2147483647) for value in song_ids]
        with db() as conn:
            gig_or_404(conn, gig_id)
            selected = []
            for song_id in song_ids:
                song = conn.execute("SELECT * FROM songs WHERE id=? AND status='ready'", (song_id,)).fetchone()
                if not song:
                    abort(400, description='A selected song is no longer in the ready-to-play song list. Refresh and try again.')
                selected.append(song)
            position = conn.execute('SELECT COALESCE(MAX(position),0) FROM setlist_entries WHERE gig_id=? AND set_number=?', (gig_id, set_number)).fetchone()[0]
            for offset, song in enumerate(selected, start=1):
                conn.execute('''INSERT INTO setlist_entries(gig_id,set_number,position,title,artist,song_key,lead,notes,bpm)
                    VALUES (?,?,?,?,?,?,?,?,?)''', (gig_id, set_number, position + offset, song['title'], song['artist'], song['song_key'], song['lead'], song['notes'], song['bpm']))
        flash('Songs added. Use Edit / move to change the order or set.')
        return redirect(url_for('gig_setlist', gig_id=gig_id))

    @app.post('/admin/gigs/<int:gig_id>/setlist/copy')
    @admin_only
    def copy_setlist(gig_id):
        source_id = number(request.form.get('source_id'), 2147483647)
        if source_id == gig_id:
            abort(400, description='Choose a different gig to copy from.')
        with db() as conn:
            gig_or_404(conn, gig_id)
            gig_or_404(conn, source_id)
            entries = conn.execute('SELECT * FROM setlist_entries WHERE gig_id=? ORDER BY set_number, position, id', (source_id,)).fetchall()
            if not entries:
                abort(400, description='That gig has no setlist to copy.')
            if conn.execute('SELECT 1 FROM setlist_entries WHERE gig_id=?', (gig_id,)).fetchone() and request.form.get('replace') != 'yes':
                abort(400, description='Confirm replacement before copying over this gig’s current setlist.')
            conn.execute('DELETE FROM setlist_entries WHERE gig_id=?', (gig_id,))
            conn.executemany('''INSERT INTO setlist_entries(gig_id,set_number,position,title,artist,song_key,lead,notes,bpm)
                VALUES (?,?,?,?,?,?,?,?,?)''', [(gig_id, row['set_number'], row['position'], row['title'], row['artist'], row['song_key'], row['lead'], row['notes'], row['bpm']) for row in entries])
        flash('Setlist copied. Changes here will not change the original gig’s setlist.')
        return redirect(url_for('gig_setlist', gig_id=gig_id))

    @app.post('/admin/gigs/<int:gig_id>/setlist/<int:entry_id>/save')
    @admin_only
    def save_setlist_entry(gig_id, entry_id):
        set_number = number(request.form.get('set_number'), 20)
        position = number(request.form.get('position'), 10000)
        song_key, lead, notes = (request.form.get(name, '').strip() for name in ('song_key', 'lead', 'notes'))
        if len(song_key) > 40 or len(lead) > 160 or len(notes) > 4000:
            abort(400, description='The key, vocalist or notes are too long.')
        from song_import import parse_bpm
        try:
            bpm = parse_bpm(request.form.get('bpm', ''))
        except ValueError as exc:
            abort(400, description=str(exc))
        with db() as conn:
            gig_or_404(conn, gig_id)
            entry = conn.execute('SELECT * FROM setlist_entries WHERE id=? AND gig_id=?', (entry_id, gig_id)).fetchone()
            if not entry:
                abort(404)
            others = conn.execute('SELECT id,set_number FROM setlist_entries WHERE gig_id=? AND id!=? ORDER BY set_number,position,id', (gig_id, entry_id)).fetchall()
            groups = defaultdict(list)
            for row in others:
                groups[row['set_number']].append(row['id'])
            groups[set_number].insert(min(position - 1, len(groups[set_number])), entry_id)
            for set_id, ids in groups.items():
                for index, item_id in enumerate(ids, start=1):
                    conn.execute('UPDATE setlist_entries SET set_number=?, position=? WHERE id=? AND gig_id=?', (set_id, index, item_id, gig_id))
            conn.execute('UPDATE setlist_entries SET song_key=?, lead=?, notes=?, bpm=? WHERE id=? AND gig_id=?', (song_key, lead, notes, bpm, entry_id, gig_id))
        flash('Setlist updated. These changes apply only to this gig.')
        return redirect(url_for('gig_setlist', gig_id=gig_id))

    @app.post('/admin/gigs/<int:gig_id>/setlist/<int:entry_id>/delete')
    @admin_only
    def delete_setlist_entry(gig_id, entry_id):
        with db() as conn:
            gig_or_404(conn, gig_id)
            if conn.execute('DELETE FROM setlist_entries WHERE id=? AND gig_id=?', (entry_id, gig_id)).rowcount != 1:
                abort(404)
        flash('Song removed from this setlist. Your song list is unchanged.')
        return redirect(url_for('gig_setlist', gig_id=gig_id))
