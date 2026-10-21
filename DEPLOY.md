# Update the existing Render service

The live site is https://theafterhourssf.com. It already runs on a paid Render
Python web service with a persistent disk. Update that service; do not create a
new Blueprint or replace its disk.

## Preserve the existing configuration

- Repository: `joeeralph/the-after-hours`, branch `main`.
- Build: `pip install -r requirements.lock`.
- Start: `gunicorn 'app:create_app()' --bind 0.0.0.0:$PORT --workers 1`.
- `BAND_HTTPS=1`.
- `BAND_DATA_DIR=/var/data/the-after-hours`.
- Persistent disk mounted at `/var/data`.

Keep the production database, session key and existing accounts in place. Do not
copy the local database to Render, rerun initial password setup, reset accounts,
or delete/recreate the disk. Never put credentials or setup links in chat or Git.
The application adds missing BPM columns and an import-history table on startup;
it does not clear existing records or automatically import any songs.

## Deploy and check the update

1. Run the existing tests locally: `.venv/bin/python -m unittest discover -s tests -v`.
2. Commit and push the reviewed code to `main`. Render may deploy automatically;
   this can briefly interrupt the service. If automatic deploys are off, open the
   **existing service** in Render and deploy the latest commit.
3. In Render, verify the deployment succeeded and check its logs for startup
   errors. Open https://theafterhourssf.com and confirm the public homepage loads.
4. Sign in using your existing Joe or Rob account. In **Song lists**, confirm that
   **Add a list of songs** and the optional **Tempo (BPM)** field appear. Check the
   calendar and existing data are still present. Other members can view song
   details and setlists but cannot import or edit them.

A successful Git push is not proof Render deployed the commit. A successful
Render deploy is not proof the PDF records have been imported.

## Apply the reviewed Pt. Reyes PDF to production

1. On the live website, sign in as Joe or Rob. Open **Song lists → Add a list of
   songs → Import the Pt. Reyes PDF**.
2. Select the existing **Old Western Saloon** gig. A unique venue match is
   preselected. Check its date and location; do not create a guessed gig.
3. Click **Review rows**. Confirm 35 entries in PDF order, including “Let’s Go
   Back” twice and “Fire on the Bayou” twice. There are no marked set breaks in
   the PDF, so it uses set 1. Unknown details stay blank or come from existing
   repertoire records. Durations appear in Notes. No artist guesses are supplied.
4. Review matched songs, missing fields to fill, differences that preserve existing
   repertoire values, and any moves from Working list to Song list. If the app
   reports an ambiguous match, resolve it in Song lists and review again. If the
   gig already has a setlist, stop and review it: this importer will not replace
   or append to it. Existing data remains untouched on these errors.
5. Click **Confirm import**. The app saves a private database backup and commits
   the repertoire updates, ordered snapshots and completion record together.
6. The app opens the target setlist. Verify **35 songs**, their order, supplied
   keys/BPM, and both repeated entries. Check Song list for the repertoire songs.
   That success message and the actual records establish a completed production
   import. Reopening and confirming the same PDF import adds nothing again.

Backups are stored on the disk at `/var/data/the-after-hours/import-backups/` with
private permissions. They contain account information and must not be shared,
committed or served publicly. Keep secure off-disk backups too. Do not restore a
backup over a running database without a separate reviewed recovery procedure.

This update does not add email notifications or calendar subscriptions. The
existing direct-address login rate limit still needs trusted proxy configuration
before changing how client addresses are interpreted.
