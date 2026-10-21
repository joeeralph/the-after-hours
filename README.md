# The After Hours

A mobile-friendly band website with upcoming gigs, YouTube embeds and a password-protected dashboard at `/admin`. The supplied moon logo and live band artwork are included in static/images/ and used by default. No sample gigs are presented as real events.

## Local development

Use the existing checkout; cloud tasks are already isolated and do not need a separate Git worktree.

```sh
cd /workspace/the-after-hours
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/flask --app app:create_app set-admin-password
.venv/bin/flask --app app:create_app run --host 0.0.0.0 --port 3000
```

The password command sets Joe's administrator account, username `joe`, and prompts privately; use a unique password with at least 12 characters. There is no default password. The public page works before administrator setup; sign-in stays unavailable until the password is created. To reset it, rerun the command. Never put a password in Git or chat.

## Private band accounts and calendar

Sign in at `/admin/login` using your individual username and password. Joe and Rob's administrator accounts manage the public website, rehearsals and accounts. The other four members go directly to `/admin/calendar` and can view the schedule and change only their own availability. Public visitors cannot access rehearsals or availability.

Six accounts are prepared, initially without passwords: `joe`, `rachel`, `rob`, `bryan`, `zusje` and `nasser`. The owner's existing password is migrated to Joe's account when present. No shared login is required.

After setting Joe's password, open **Band accounts** in the dashboard. Create a setup link for each member and share it privately yourself. Links allow the recipient to set their password, expire after 24 hours, work once and are stored only as hashes. Creating another link replaces the previous one. Resetting a password or disabling an account revokes existing sessions. Users can change their own password from **Password**. Accounts and setup emails are not sent automatically; this does not require an email provider.

The private calendar shows monthly gigs and rehearsals. Joe and Rob can add, edit and delete rehearsals with same-day start/end times, location and notes. Each member marks a date **Available**, **Maybe**, or **Unavailable**; no response is shown as **Not answered**. Select a start and optional end date to set or clear an inclusive range (up to 366 days per save), including full weeks for vacations or travel. Saving replaces your existing status and note on each selected date; it never changes another member’s entries. Leave the end date blank for one day. Availability is for the whole day: use Maybe and a note for partial availability. Notes are shared with the band. Rehearsals show every active member's response and the calendar marks dates with unavailable members; unanswered dates never count as available. Calendar dates and rehearsal times use San Francisco time. Scheduling does not send notifications or synchronize external calendars.

In the dashboard you can add, edit and delete gigs and YouTube videos, set a booking email, and change the introduction, logo and band photograph. Images currently require direct HTTPS image links; file uploading and non-YouTube video hosting are not included. Upcoming gigs use the San Francisco calendar day (America/Los_Angeles), including daylight saving time, regardless of the server timezone.

## Validation

```sh
.venv/bin/python -m unittest discover -s tests -v
curl --fail http://127.0.0.1:3000/
```

## Deployment

The live site is https://theafterhourssf.com on an existing paid Render Python web service with a persistent disk. See [DEPLOY.md](DEPLOY.md) for updating that service and importing the Pt. Reyes PDF.

Run `.venv/bin/gunicorn 'app:create_app()' --bind 0.0.0.0:3000 --workers 1` behind an HTTPS reverse proxy. Set `BAND_HTTPS=1` for secure session cookies on the HTTPS deployment (leave unset for local HTTP). Keep the app on a persistent disk: `data/` contains the SQLite database (including individual password hashes and private calendar information) and session signing key. Back up this directory securely, restrict its permissions, and never expose it as static files. Optionally set `BAND_DATA_DIR` to an absolute persistent directory. Do not use an ephemeral/serverless filesystem. The login rate limit uses the direct client address; configure trusted reverse-proxy handling before deployment so visitors have distinct addresses.

The development database is separate from production. Never upload local data/ to Render, reset the persistent disk, or recreate existing accounts during an update. Production deployment must be verified independently of local tests.

## Private song lists

Open **Song lists** from the dashboard or calendar navigation. All signed-in members can view and search the ready-to-play **Song list** and **Working list** (songs being learned or rehearsed). Joe and Rob can add, edit, move and delete songs. Entries have title, artist, key, lead vocalist, an optional HTTPS reference link and shared band notes. Change the List field to move a song without losing its details. The lists are stored on the persistent disk and are never published on the homepage.

## Gig setlists

Open **Setlists**, select a gig, and add ready-to-play songs from the Song list. Joe and Rob can add multiple songs to numbered sets, edit each song's position or set, and customize its key, vocalist and notes for that gig. Members can view but cannot edit. Setlists stay private and can be printed or saved as PDF using the browser's Print menu.

Copy any other gig's setlist as a starting point, including past gigs. Replacing a nonempty setlist requires confirmation. Each entry stores a copy of the song details, so changing or deleting a repertoire song does not erase a prior gig's setlist, and editing a copied setlist does not change its source. Deleting a gig also deletes that gig's setlist. The persistent database is upgraded automatically without resetting existing gigs, accounts, availability or songs.

## Tempo and bulk song entry

Songs and gig snapshots have an optional whole-number tempo (1–400 BPM).
Existing databases gain nullable BPM columns without replacing records. Repertoire
edits never rewrite historical setlist snapshots; copying a gig preserves its BPM.

Administrators can select **Add a list of songs** on Song lists or **Paste an
ordered list of songs** on a gig setlist. Paste tab-separated spreadsheet rows,
CSV, or one title per line. Columns: Title, Artist, Key, Lead singer, BPM, Notes,
Set number. Only Title is required. The optional seventh column defaults to 1;
increase it at each set break and repeat it for every song in that set. Use sets
1–20 in ascending order. A Title / Artist header is optional. Up to 100 rows are
validated together. Review the table before confirming; changing input or saved
data requires a fresh preview. Previews expire after 30 minutes.

Title matching ignores case, punctuation and spacing. A unique match reuses the
song and fills missing details; existing metadata and reference links remain.
Conflicting artist credits or multiple matches stop the entire import. Selecting
Song list promotes matched Working list songs to ready to play; the preview
shows this. Supplied gig-specific details become independent snapshots. Generic
bulk imports append only after confirmation if a setlist already exists. Set
numbers preserve breaks, row order and repeats. Identical imports are recorded
and add nothing on retry. To repeat an identical program intentionally, copy its
gig setlist or use the existing song picker.

### Pt. Reyes PDF import

`imports/pt-reyes.tsv` is a literal transcription of the supplied PDF: 35 ordered
rows, 33 distinct titles, two repeated songs, and no marked set breaks. Unknown
artists, singers, keys and tempos stay blank. Durations are retained in Notes.
No artist guesses are included. Existing repertoire details can fill blank fields.
The import does not invent a gig date or create a gig.

After deployment, sign in as Joe or Rob, open **Song lists → Add a list of songs →
Import the Pt. Reyes PDF**, and select the existing **Old Western Saloon** gig.
A unique matching venue is preselected; check its displayed date and location.
Review all 35 rows and click **Confirm import**. This is a separate production
database operation, not a consequence of deploying code. It requires an empty
setlist and refuses to replace or append to existing entries. Once applied, the
same PDF import to that gig is a no-op, even after later song edits. Other gig
setlists are unchanged. The success message and target setlist confirm completion.

Each import saves a private SQLite backup under `BAND_DATA_DIR/import-backups/`
(or local `data/import-backups/`) before committing. The rows and completion record
are committed in one transaction; failures roll back all record changes. Backups
remain on the persistent disk and contain private account data; do not commit,
serve, or share them. Arrange secure off-disk backups separately. Production
accounts/passwords, calendar data and session keys are never reset by this feature.

## Set breaks and printing

On a gig's ordered setlist, Joe or Rob can click **Start next set** beside the
song that should open the next set. That song and the remaining songs in its
current set become a new set. Later sets stay in order; nothing is alphabetized
or copied from the repertoire. **Join previous set**, shown on the first song
of later sets, removes that break. Sets are numbered consecutively and support
up to 20 sets. Song details, repeated songs and other gigs remain unchanged.
If another edit changes the order, reload the page before setting a break.

All band members can open **Printable view / PDF** from a gig setlist. It shows
just the band/gig heading and ordered tables, with set headings, key, lead singer,
BPM and notes. Click **Print / Save PDF** to open the browser print dialog.
The view remains private and includes no editing controls or account navigation.
