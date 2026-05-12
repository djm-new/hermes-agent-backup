# Restore guide: Hermes Agent code/deployment backup

This private repo backs up the local Hermes Agent source checkout and the Railway deployment files for Chief.

Use this repo when the question is:

"Can we rebuild or redeploy Chief's app/code setup?"

It is separate from `djm-new/chief-cloud-state-backup`, which backs up selected Railway runtime state from `/opt/data`.

## What this repo is for

This repo contains the app/deployment side, such as:

- Hermes Agent source code checkout
- `Dockerfile.railway`
- `railway.json`
- `docker/railway-chief-start.sh`
- helper scripts used to deploy Chief on Railway

## What this repo is not for

Do not use this repo as a dump of Chief's live cloud brain/state. Runtime memory, skills, and cron state from Railway belong in:

`djm-new/chief-cloud-state-backup`

## Basic restore / redeploy flow

Plain-English version:

1. Clone this repo onto the machine that will redeploy Chief.
2. Confirm the Railway deployment files are present.
3. Link or deploy the repo to Railway project `chief-cloud`, service `chief-gateway`.
4. Make sure Railway still has a persistent volume mounted at `/opt/data`.
5. Make sure Railway variables/secrets are present. Do not commit them to GitHub.
6. Deploy the service.
7. Confirm Railway service `chief-gateway` is `SUCCESS` and not stopped.
8. Confirm Telegram and Slack connect in the gateway log.

## Useful commands

Check the repo:

```bash
git clone https://github.com/djm-new/hermes-agent-backup.git
cd hermes-agent-backup
ls Dockerfile.railway railway.json docker/railway-chief-start.sh
```

Check Railway after deployment:

```bash
railway service status --service chief-gateway --json
railway ssh --service chief-gateway 'tail -120 /opt/data/logs/gateway.log'
```

## Safety reminder

Secrets should live in Railway variables or `/opt/data/.env`, not in this repo.

Never commit:

- API keys
- Telegram/Slack bot tokens
- OAuth tokens
- `.env`
- `auth.json`
- Google token/client-secret JSON files
