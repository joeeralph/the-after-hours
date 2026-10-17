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

Sign in at `/admin/login` using your individual username and password. Joe's administrator account manages the public website, rehearsals and accounts. Other members go directly to `/admin/calendar` and can view the schedule and change only their own availability. Public visitors cannot access rehearsals or availability.

Six accounts are prepared, initially without passwords: `joe`, `rachel`, `rob`, `bryan`, `zusje` and `nasser`. The owner's existing password is migrated to Joe's account when present. No shared login is required.

After setting Joe's password, open **Band accounts** in the dashboard. Create a setup link for each member and share it privately yourself. Links allow the recipient to set their password, expire after 24 hours, work once and are stored only as hashes. Creating another link replaces the previous one. Resetting a password or disabling an account revokes existing sessions. Users can change their own password from **Password**. Accounts and setup emails are not sent automatically; this does not require an email provider.

The private calendar shows monthly gigs and rehearsals. Joe can add, edit and delete rehearsals with same-day start/end times, location and notes. Each member marks a date **Available**, **Maybe**, or **Unavailable**; no response is shown as **Not answered**. Availability is for the whole day: use Maybe and a note for partial availability. Notes are shared with the band. Rehearsals show every active member's response and the calendar marks dates with unavailable members; unanswered dates never count as available. Calendar dates and rehearsal times use San Francisco time. Scheduling does not send notifications or synchronize external calendars.

In the dashboard you can add, edit and delete gigs and YouTube videos, set a booking email, and change the introduction, logo and band photograph. Images currently require direct HTTPS image links; file uploading and non-YouTube video hosting are not included. Upcoming gigs use the San Francisco calendar day (America/Los_Angeles), including daylight saving time, regardless of the server timezone.

## Validation

```sh
.venv/bin/python -m unittest discover -s tests -v
curl --fail http://127.0.0.1:3000/
```

## Deployment

For a proposed Render deployment with a temporary HTTPS address, see [DEPLOY.md](DEPLOY.md). `render.yaml` includes a paid service and persistent disk; review the current prices before deploying.

Run `.venv/bin/gunicorn 'app:create_app()' --bind 0.0.0.0:3000 --workers 1` behind an HTTPS reverse proxy. Set `BAND_HTTPS=1` for secure session cookies on the HTTPS deployment (leave unset for local HTTP). Keep the app on a persistent disk: `data/` contains the SQLite database (including individual password hashes and private calendar information) and session signing key. Back up this directory securely, restrict its permissions, and never expose it as static files. Optionally set `BAND_DATA_DIR` to an absolute persistent directory. Do not use an ephemeral/serverless filesystem. The login rate limit uses the direct client address; configure trusted reverse-proxy handling before deployment so visitors have distinct addresses.

This checkout is a development version; domain, hosting, HTTPS, real band assets and the administrator password must be configured before launch. Production deployment has not been tested.
