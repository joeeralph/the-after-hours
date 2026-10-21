"""Small, permission-protected updates from song-list dropdowns."""
from flask import abort, flash, redirect, request, url_for

CHOICES = ('', 'Rachel', 'Rob', 'Other')


def register_lead_singers(app, db, admin_only):
    def update_lead(item_id, gig_id=None):
        if 'lead' not in request.form:
            abort(400, description='Choose a lead singer before saving.')
        lead = request.form['lead'].strip()
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if gig_id is None:
                item = conn.execute('SELECT * FROM songs WHERE id=?', (item_id,)).fetchone()
            else:
                item = conn.execute('SELECT * FROM setlist_entries WHERE id=? AND gig_id=?', (item_id, gig_id)).fetchone()
            if not item:
                abort(404)
            # Keep a pre-existing custom singer unless an administrator changes it.
            if lead not in CHOICES and lead != item['lead']:
                abort(400, description='Choose Rachel, Rob, Other, or leave the singer unassigned.')
            if gig_id is None:
                conn.execute('UPDATE songs SET lead=? WHERE id=?', (lead, item_id))
            else:
                conn.execute('UPDATE setlist_entries SET lead=? WHERE id=? AND gig_id=?', (lead, item_id, gig_id))
        flash('Lead singer saved.' + (' This change applies only to this gig.' if gig_id is not None else ' Existing gig setlists are unchanged.'))
        return redirect(url_for('gig_setlist', gig_id=gig_id) if gig_id is not None else url_for('songs', _anchor=item['status']))

    @app.post('/admin/songs/<int:item_id>/lead')
    @admin_only
    def save_song_lead(item_id):
        return update_lead(item_id)

    @app.post('/admin/gigs/<int:gig_id>/setlist/<int:entry_id>/lead')
    @admin_only
    def save_setlist_lead(gig_id, entry_id):
        return update_lead(entry_id, gig_id)
