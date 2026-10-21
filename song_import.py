"""Reviewed, atomic and repeatable repertoire/setlist imports."""
import csv
import hashlib
import io
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_hex

from flask import flash, redirect, render_template, request, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

FIELDS = ('title', 'artist', 'song_key', 'lead', 'bpm', 'notes')
PDF_PATH = Path(__file__).parent / 'imports' / 'pt-reyes.tsv'


def parse_bpm(value):
    value = str(value or '').strip()
    if not value or value == '—':
        return None
    if not re.fullmatch(r'[0-9]{1,3}', value) or not 1 <= int(value) <= 400:
        raise ValueError('Tempo must be a whole number from 1 to 400 BPM, or blank.')
    return int(value)


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def parse_songs(text):
    reader = csv.reader(io.StringIO(text), delimiter='\t' if '\t' in text else ',', strict=True)
    rows = []
    previous_set = 1
    for line, cells in enumerate(reader, 1):
        if not any(cell.strip() for cell in cells):
            continue
        if not rows and normalized(cells[0]) == 'title' and len(cells) > 1 and normalized(cells[1]) == 'artist':
            continue
        if len(cells) > 7:
            raise ValueError(f'Row {line}: use at most seven columns; quote notes containing commas.')
        values = dict(zip(FIELDS, [c.strip() for c in cells[:6]] + [''] * max(0, 6 - len(cells))))
        if not normalized(values['title']) or any(len(values[k]) > n for k, n in [('title', 160), ('artist', 160), ('song_key', 40), ('lead', 160), ('notes', 4000)]):
            raise ValueError(f'Row {line}: a title is required and fields must fit their limits.')
        values['bpm'] = parse_bpm(values['bpm'])
        number = cells[6].strip() if len(cells) == 7 else '1'
        if not re.fullmatch(r'[0-9]{1,2}', number) or not previous_set <= int(number) <= 20:
            raise ValueError(f'Row {line}: set numbers must be 1–20 in increasing order (repeat a number within a set).')
        values['set_number'] = previous_set = int(number)
        rows.append(values)
        if len(rows) > 100:
            raise ValueError('Import up to 100 songs at a time.')
    if not rows:
        raise ValueError('Paste at least one song.')
    return rows


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def plan_import(conn, rows, status, gig_id, preset=False):
    if status not in ('ready', 'working'):
        raise ValueError('Choose a valid song list.')
    gig = None
    entries = []
    if gig_id is not None:
        found = conn.execute('SELECT * FROM gigs WHERE id=?', (gig_id,)).fetchone()
        if not found:
            raise ValueError('Choose an existing gig.')
        gig = dict(found)
        entries = [dict(r) for r in conn.execute('SELECT * FROM setlist_entries WHERE gig_id=? ORDER BY set_number,position,id', (gig_id,))]
    if preset and (not gig or 'oldwesternsaloon' not in normalized(gig['venue'])):
        raise ValueError('Select the existing Old Western Saloon gig. No gig will be created automatically.')
    # One fixed PDF import per gig, even if the prepared data is revised later.
    key = digest({'source': 'pt-reyes-pdf-v1' if preset else rows, 'status': status, 'gig_id': gig_id})
    completed = conn.execute('SELECT * FROM song_import_runs WHERE import_key=?', (key,)).fetchone()
    if completed:
        return {'key': key, 'already': True, 'gig': gig, 'rows': [], 'new_count': 0, 'songs': [], 'entries': entries}
    if preset and entries:
        raise ValueError('This gig already has a setlist. It has been left unchanged. Review it before importing the PDF; this import will not replace or append to it.')
    songs = [dict(r) for r in conn.execute('SELECT * FROM songs ORDER BY id')]
    original = [dict(r) for r in songs]
    details = []
    new_count = 0
    for row in rows:
        candidates = [s for s in songs if normalized(s['title']) == normalized(row['title'])]
        if row['artist']:
            exact = [s for s in candidates if normalized(s['artist']) == normalized(row['artist'])]
            blank = [s for s in candidates if not s['artist']]
            candidates = exact or blank or candidates
        if len(candidates) > 1:
            raise ValueError(f"Multiple repertoire matches for {row['title']}. Resolve the ambiguous match before importing.")
        changes, conflicts = [], []
        if candidates:
            song = candidates[0]
            if row['artist'] and song['artist'] and normalized(row['artist']) != normalized(song['artist']):
                raise ValueError(f"Artist conflict for {row['title']}. Review the existing song before importing another version.")
            action = 'Reuse existing song'
            for field in FIELDS[1:]:
                if not song[field] and row[field]:
                    song[field] = row[field]
                    changes.append(field)
                elif row[field] and song[field] != row[field]:
                    conflicts.append(field)
            if status == 'ready' and song['status'] == 'working':
                song['status'] = 'ready'
                changes.append('move to Song list')
        else:
            new_count += 1
            song = dict(row, id=-new_count, status=status, reference='')
            songs.append(song)
            action = 'Add new song'
        snapshot = {field: row[field] or song[field] for field in FIELDS}
        details.append(dict(snapshot, set_number=row['set_number'], action=action, changes=changes, conflicts=conflicts))
    return {'key': key, 'already': False, 'gig': gig, 'rows': details, 'new_count': new_count,
            'songs': songs, 'original': original, 'entries': entries}


def apply_plan(conn, plan):
    """Caller holds an immediate transaction and has checked the reviewed plan."""
    if plan['already']:
        return
    original = {song['id']: song for song in plan['original']}
    for song in plan['songs']:
        if song['id'] < 0:
            conn.execute('INSERT INTO songs(title,artist,song_key,lead,bpm,notes,status,reference) VALUES (?,?,?,?,?,?,?,?)',
                         (*[song[k] for k in FIELDS], song['status'], song['reference']))
        elif song != original[song['id']]:
            conn.execute('UPDATE songs SET artist=?,song_key=?,lead=?,bpm=?,notes=?,status=? WHERE id=?',
                         (*[song[k] for k in ('artist', 'song_key', 'lead', 'bpm', 'notes', 'status')], song['id']))
    if plan['gig']:
        positions = {}
        for entry in plan['entries']:
            positions[entry['set_number']] = max(positions.get(entry['set_number'], 0), entry['position'])
        for row in plan['rows']:
            set_number = row['set_number']
            positions[set_number] = positions.get(set_number, 0) + 1
            conn.execute('INSERT INTO setlist_entries(gig_id,set_number,position,title,artist,song_key,lead,bpm,notes) VALUES (?,?,?,?,?,?,?,?,?)',
                         (plan['gig']['id'], set_number, positions[set_number], *[row[k] for k in FIELDS]))
    conn.execute('INSERT INTO song_import_runs(import_key,gig_id,row_count) VALUES (?,?,?)',
                 (plan['key'], plan['gig']['id'] if plan['gig'] else None, len(plan['rows'])))


def backup_database(data_dir):
    directory = Path(data_dir) / 'import-backups'
    directory.mkdir(mode=0o700, exist_ok=True)
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + token_hex(6) + '.sqlite3'
    target = directory / name
    target.touch(mode=0o600, exist_ok=False)
    with sqlite3.connect(Path(data_dir) / 'band.sqlite3') as source, sqlite3.connect(target) as dest:
        source.backup(dest)
    return name


def register_song_import(app, db, admin_only):
    signer = URLSafeTimedSerializer(app.secret_key, salt='song-import-preview-v1')

    @app.route('/admin/songs/import', methods=['GET', 'POST'])
    @admin_only
    def import_songs():
        preset = request.values.get('preset') == 'pt-reyes'
        text = PDF_PATH.read_text() if preset else request.form.get('text', '')
        status = 'ready' if preset else request.form.get('status', 'ready')
        gig_id = request.form.get('gig_id', request.args.get('gig_id', ''))
        error, plan, token = None, None, ''
        with db() as conn:
            gigs = conn.execute('SELECT * FROM gigs ORDER BY day DESC,id DESC').fetchall()
        if preset and request.method == 'GET' and not gig_id:
            matches = [g for g in gigs if 'oldwesternsaloon' in normalized(g['venue'])]
            if len(matches) == 1:
                gig_id = str(matches[0]['id'])
        if request.method == 'POST':
            try:
                selected_gig = int(gig_id) if gig_id else None
                rows = parse_songs(text)
                with db() as conn:
                    # Serialize preview/apply with other writes; a changed database requires a fresh preview.
                    conn.execute('BEGIN IMMEDIATE')
                    plan = plan_import(conn, rows, status, selected_gig, preset)
                    fingerprint = digest(plan)
                    if request.form.get('action') == 'save':
                        try:
                            reviewed = signer.loads(request.form.get('preview_token', ''), max_age=1800)
                        except (BadSignature, SignatureExpired) as exc:
                            raise ValueError('Review the rows before saving. Previews expire after 30 minutes.') from exc
                        if not plan['already'] and reviewed != fingerprint:
                            raise ValueError('The rows, gig or saved data changed. Review a fresh preview before saving.')
                        if plan['entries'] and not plan['already'] and request.form.get('append') != 'yes':
                            raise ValueError('Confirm appending to this gig’s existing setlist before saving.')
                        if not plan['already']:
                            try:
                                backup_database(app.config['DATA_DIR'])
                            except (OSError, sqlite3.Error) as exc:
                                raise ValueError('The database backup could not be created. Nothing was imported. Check persistent disk space and permissions before trying again.') from exc
                            apply_plan(conn, plan)
                        flash('This import was already applied. Nothing was added again.' if plan['already'] else
                              f"Imported {len(rows)} ordered rows; added {plan['new_count']} repertoire songs." + (' Gig setlist saved.' if selected_gig else ''))
                        return redirect(url_for('gig_setlist', gig_id=selected_gig) if selected_gig else url_for('songs'))
                    token = signer.dumps(fingerprint)
            except (ValueError, csv.Error) as exc:
                error = str(exc)
        return render_template('song_import.html', text=text, plan=plan, gigs=gigs, gig_id=str(gig_id),
                               status=status, error=error, preset=preset, preview_token=token), 400 if error else 200
