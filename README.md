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

The password command prompts privately; use a unique password with at least 12 characters. There is no default password. The public page works before administrator setup; sign-in stays unavailable until the password is created. To reset it, rerun the command. Never put a password in Git or chat.

In the dashboard you can add, edit and delete gigs and YouTube videos, set a booking email, and change the introduction, logo and band photograph. Images currently require direct HTTPS image links; file uploading and non-YouTube video hosting are not included. Upcoming gigs use the San Francisco calendar day (America/Los_Angeles), including daylight saving time, regardless of the server timezone.

## Validation

```sh
.venv/bin/python -m unittest discover -s tests -v
curl --fail http://127.0.0.1:3000/
```

## Deployment

For a proposed Render deployment with a temporary HTTPS address, see [DEPLOY.md](DEPLOY.md). `render.yaml` includes a paid service and persistent disk; review the current prices before deploying.

Run `.venv/bin/gunicorn 'app:create_app()' --bind 0.0.0.0:3000 --workers 1` behind an HTTPS reverse proxy. Set `BAND_HTTPS=1` for secure session cookies on the HTTPS deployment (leave unset for local HTTP). Keep the app on a persistent disk: `data/` contains the SQLite database, session signing key and password hash. Back up this directory securely, restrict its permissions, and never expose it as static files. Optionally set `BAND_DATA_DIR` to an absolute persistent directory. Do not use an ephemeral/serverless filesystem. The login rate limit uses the direct client address; configure trusted reverse-proxy handling before deployment so visitors have distinct addresses.

This checkout is a development version; domain, hosting, HTTPS, real band assets and the administrator password must be configured before launch. Production deployment has not been tested.
