# Preview on Render

This is a proposed paid deployment. Review Render's current service and persistent disk prices before confirming deployment. Nothing is deployed merely by committing these files.

1. Sign in at https://dashboard.render.com/ using GitHub.
2. Create a **Blueprint**, connect GitHub, and grant access to **joeeralph/the-after-hours**. Select that repository and the `main` branch.
3. Render reads `render.yaml`. Review the Starter web service and 1 GB persistent disk, including their current charges. Approve deployment only if the cost is acceptable. Keep the disk: it stores your gigs, videos and administrator password hash across deployments.
4. Wait for the service to become live. Open the HTTPS `onrender.com` URL displayed on the service page. The exact address is assigned by Render.
5. In the service's **Shell**, run `flask --app app:create_app set-admin-password`. Enter and confirm your chosen password when prompted. Use at least 12 characters. It will not be displayed. Never share the password in chat or put it in GitHub.
6. Open `/admin` on your new site and sign in. Add a gig and a YouTube video, then check that each appears on the homepage. Edit and remove them if they were only test entries.

The site is publicly accessible at the preview address. The dashboard requires your password. Administrator setup is disabled until you set that password in the shell. Keep regular backups of the persistent data directory. A domain name can be connected later.

Images and the public homepage work without additional accounts or API keys. YouTube playback depends on the uploaded video's embedding permissions. Production hosting and these steps still need verification on an actual Render account. The current rate limit treats requests from the same proxy as one address, which is conservative but can temporarily block all admin sign-ins after repeated failed attempts; configure trusted proxy headers for the selected hosting topology before wider admin use.
