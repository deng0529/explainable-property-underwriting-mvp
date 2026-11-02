# Share the saved-results app with Streamlit Community Cloud

This project uses **Streamlit Community Cloud**, not StreamNative Cloud (a different
service). The cloud app views saved results only. Continue creating and updating
analyses in the local `app.py` owner app. Both use the same Turso database.

## 1. Create the cloud app

1. Sign in at <https://share.streamlit.io> with your GitHub account.
2. Choose **Create app**, then **Yup, I have an app** if asked.
3. Use these values:

   | Setting | Value |
   | --- | --- |
   | Repository | `deng0529/explainable-property-underwriting-mvp` |
   | Branch | `main` |
   | Main file path | `cloud_app.py` |
   | Python version (Advanced settings) | `3.12` |

4. Choose an available app subdomain if you want a memorable link.

The repository already contains `requirements.txt`, the configuration, approved
prompts and immutable synthetic Northbridge PDF. No separate Graphviz system
package is required. Do not upload your `.env` or local pending-response folders.

## 2. Add only database credentials

Open **Advanced settings → Secrets**. Enter these two settings in TOML format,
replacing the placeholders locally with the corresponding values from your `.env`:

```toml
TURSO_DATABASE_URL = "your-existing-database-url"
TURSO_AUTH_TOKEN = "your-database-auth-token"
```

Use a Turso read-only token for this deployment where possible. Keep actual values
in Streamlit's Secrets field only; do not paste them into GitHub, YAML, chat or logs.
Aliases already supported by the local credential loader are also supported in
cloud secrets, but the canonical names above are simplest.

**Do not add `OPENAI_API_KEY`.** The public app does not need it. Neither
`OPENAI_MODEL` nor an admin password is required. Model/prompt settings remain in
YAML; visitors cannot use them to generate anything.

## 3. Deploy and share

Click **Deploy** and wait for dependency installation to complete. In the deployed
app's **Settings → Sharing → Who can view this app**, choose **This app is public
and searchable**. Copy the resulting `https://….streamlit.app` URL and share it.
Anyone with the URL can open the app without installing Python or Streamlit.
The public URL can be forwarded; do not treat it as a private access control.

## 4. Check the public experience

- Submission: click **Load saved submission**; inspect information, screening and
  the original PDF. Generation, confirmations and save actions remain unavailable.
- Assessment: load saved evidence, then saved proposed/confirmed beliefs, then
  **Load saved Fire/Security results** and **Load saved overall assessment**.
  These retrieve existing compatible results; a missing result cannot trigger
  calculation or a paid request.
- Decision: **Load saved decision review** reads the current saved upstream records
  directly; opening Submission/Assessment first is unnecessary. Download the saved
  review report. A missing PDF must first be created/saved in the local owner app.
- System overview: inspect both diagrams directly. They are read-only.

The current document hash and compatible saved inputs still have to match. If a
stage has no current successful saved result, the cloud app explains what is
missing instead of generating or writing a replacement. Refresh/reset the relevant
workspace and load again after updating records locally.

## Configuration and future updates

`cloud_app.py` sets the server-side `public_readonly` profile. The central loader
applies `config/public_readonly.yaml` on top of the single `config/settings.yaml`;
it does not duplicate field definitions or screening policy. Profile-specific
caches prevent local owner configuration from leaking into the cloud entry point.
All paid calls, calculations on cache misses, confirmations and database writes
remain protected by the existing server-side owner checks.

Local operation is unchanged: `scripts/run.ps1` launches `app.py` on `127.0.0.1` with
the configured local owner mode. Do not set `UNDERWRITING_PROFILE` in the local
`.env` unless you deliberately want to restrict the local app to saved viewing too.

Pushing later code updates to `main` updates the deployed app through Community
Cloud. Do not change secrets in Git. Use the cloud app's settings to update them.

## Troubleshooting

- **Missing database settings:** add the two names above in the cloud Secrets field.
- **Database unavailable:** check the URL/token and database connectivity; the app
  will not treat an outage as permission to call GPT.
- **Saved stage/report unavailable:** load and complete that stage in the local
  owner app first, then reload the public workspace.
- **Dependency error:** check developer logs and select Python 3.12. Keep
  `requirements.txt` at the repository root.

Official instructions, checked 2 November 2026:

- [Deploy your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
- [Cloud secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [Public sharing](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app)
