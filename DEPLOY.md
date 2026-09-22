# Deploying on Oracle Cloud Always Free

This guide puts the whole product (API, web app and vector store) on one free
Oracle Cloud machine, behind a real HTTPS certificate.

One container serves both the web app and the API from the **same origin**.
That keeps the session cookie simple (`SameSite=Lax`, no cross-site setup) and
means TLS only has to be set up once, in Caddy.

```
internet ──443──▶ Caddy (TLS) ──▶ nexora container ──▶ MongoDB Atlas (all data)
                                        │
                                        └──▶ LLM chain: Ollama → Groq → Gemini (optional)
```

## Contents

1. [Before you start](#1-before-you-start)
2. [Create the instance](#2-create-the-instance)
3. [Open ports 80 and 443](#3-open-ports-80-and-443)
4. [Install Docker and add swap](#4-install-docker-and-add-swap)
5. [Point a domain at it](#5-point-a-domain-at-it)
6. [Create the database](#6-create-the-database)
7. [Get the code and configure](#7-get-the-code-and-configure)
8. [Start it](#8-start-it)
8b. [Check it end to end](#8b-check-it-end-to-end)
9. [Optional: Firebase sign-in](#9-optional-firebase-sign-in)
10. [Optional: LLM features](#10-optional-llm-features)
11. [Updating](#11-updating)
12. [Backups](#12-backups)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Before you start

| You need | Notes |
|---|---|
| The code | This project, as a zip or a git clone (step 7). |
| Oracle Cloud account | Always Free tier. A card is verified but not charged. |
| A domain name | A free [DuckDNS](https://www.duckdns.org) subdomain works. Firebase won't authorise a bare IP address. |
| MongoDB | A free Atlas M0 cluster (step 6). |
| Optional API keys | Groq and/or Gemini, for the LLM features (step 10). |

**If someone else owns the project**, ask them for these before you start —
none of them are in the code, and the site won't fully work without them:

| Value | Needed for | Without it |
|---|---|---|
| `MONGO_URI` | The database | The app won't start |
| Firebase service account (JSON or the 3 fields) | Verifying Google sign-in on the server | Google sign-in refused |
| `NEXT_PUBLIC_FIREBASE_*` web config | The sign-in button in the browser | No "Continue with Google" button |
| `GROQ_API_KEY`, `GEMINI_API_KEYS` | The assistant and rewording | Those features stay off; the FAQ bot still works |

Everything else (`ADMIN_PASSWORD`, `ADMIN_API_KEY`) you generate yourself in
step 7.

Throughout this guide, replace `yourname.duckdns.org` with your own domain.

## 2. Create the instance

In the Oracle console, create a compute instance:

| Setting | Value |
|---|---|
| Shape | **VM.Standard.A1.Flex** (Ampere, ARM): 2 OCPU, 12 GB RAM |
| Image | Ubuntu 24.04 (aarch64) |
| Boot volume | 100 GB or more |
| SSH key | Download the private key it offers. You can't get it again later. |

> **"Out of host capacity"** is normal for A1 shapes. Try another availability
> domain, or try again later.

Then connect:

```bash
ssh -i path/to/private.key ubuntu@<public-ip>
```

## 3. Open ports 80 and 443

Oracle has **two** firewalls, and both must allow the ports. Port 80 is
required even though the site redirects to HTTPS: Let's Encrypt checks your
domain over port 80 before it issues a certificate.

**In the console:** Networking → Virtual Cloud Networks → your VCN → Security
Lists → Add Ingress Rules. Source `0.0.0.0/0`, protocol TCP, destination ports
`80` and `443`.

**On the instance:**

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## 4. Install Docker and add swap

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git git-lfs
sudo usermod -aG docker $USER && newgrp docker
```

The first image build (installing torch on two ARM cores) briefly needs more
memory than the app does. Swap stops that from being killed:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 5. Point a domain at it

1. Copy the instance's **public IP** from the console.
2. At [duckdns.org](https://www.duckdns.org), sign in, claim a subdomain, and
   set its IP to the instance's public IP.
3. Check that it resolves before continuing:

```bash
dig +short yourname.duckdns.org   # must print the instance's public IP
```

If this is wrong, the certificate request in step 8 fails and Caddy keeps
retrying slowly.

## 6. Create the database

At [mongodb.com/atlas](https://www.mongodb.com/atlas), create a free **M0**
cluster, then:

1. **Database Access:** create a database user with a long password.
2. **Network Access:** add the instance's public IP. (`0.0.0.0/0` also works,
   but then the password is the only protection.)
3. **Connect → Drivers:** copy the `mongodb+srv://…` connection string.

## 7. Get the code and configure

**From a zip** (what you have if the project was handed to you):

```bash
sudo apt install -y unzip
# copy the zip up from your own machine first:
#   scp -i path/to/private.key nexora-ai-*.zip ubuntu@<public-ip>:~
unzip nexora-ai-*.zip        # unpacks into nexora-ai/
cd nexora-ai
cp .env.example .env
nano .env
```

**Or from git**, if you have access to the repository. Install Git LFS first,
or images arrive as small text files:

```bash
git lfs install
git clone -b feature/billing-platform https://github.com/Harsh29004/chatbot.git nexora
cd nexora
cp .env.example .env
nano .env
```

Set at least these values in `.env`:

```bash
# --- Domain ---
SITE_DOMAIN=yourname.duckdns.org
PUBLIC_BASE_URL=https://yourname.duckdns.org
WEB_ORIGINS=https://yourname.duckdns.org
# Written into widget packages customers download. Must be https://,
# or HTTPS sites block the widget's requests.
PUBLIC_API_ORIGIN=https://yourname.duckdns.org

# --- Sessions ---
# Required with HTTPS, or nobody stays signed in.
BILLING_COOKIE_SECURE=true
BILLING_ALLOW_MANUAL=false

# --- Database ---
MONGO_URI=mongodb+srv://user:password@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
MONGO_DB_NAME=nexora

# --- Admin ---
# Generate both: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# ADMIN_API_KEY is for scripts. The username and password are what you sign in
# with at /admin and /admin/support; leaving the password empty disables that
# sign-in entirely.
ADMIN_API_KEY=<paste a generated value>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<paste another generated value>
```

Then, if you have them, add the Firebase values (step 9) and the LLM keys
(step 10) to the same file. Two more are worth knowing about, both already in
`.env.example` with sensible defaults:

```bash
# Browser error reports and page timings, stored in MongoDB and shown in the
# admin panel. Set to false to collect nothing.
TELEMETRY_ENABLED=true
TELEMETRY_RETENTION_DAYS=30
```

Keep a few rules in mind:

- **Never use `localhost` or `http://`** in `.env` on the server.
- **Leave `BILLING_ALLOW_MANUAL=false`.** It activates paid plans without payment.
- **Compose only passes listed variables.** Docker Compose uses `.env` to fill
  in `${...}` values in `docker-compose.yml`. A setting that isn't listed under
  the `nexora` service's `environment:` never reaches the app. If you add a new
  setting, add it there too.
- **Never commit `.env`.** It's already in `.gitignore`.

## 8. Start it

```bash
docker compose --profile tls up -d --build
```

This builds four services:

| Service | What it is | Reachable from |
|---|---|---|
| `bot-service` | Python: embeddings, retrieval, sheet readers | the compose network only |
| `api` | Node + Express: accounts, billing, credits, admin | Caddy, and `127.0.0.1:8000` |
| `web` | Next.js: the dashboard and landing page | Caddy, and `127.0.0.1:3000` |
| `caddy` | TLS | the internet, on 80 and 443 |

The first build takes **15–30 minutes** on two ARM cores. Almost all of that
is `bot-service` — installing torch and baking the embedding model into the
image — so the first request after a restart isn't slow. `api` and `web` build
in a couple of minutes between them, which matters later: a change to billing
or the dashboard rebuilds those two and leaves the heavy image alone.

Startup order is handled for you. `api` waits for `bot-service` to report
healthy, and `web` waits for `api`. `bot-service` takes a minute or two to get
there because it loads the embedding model before answering — that is expected,
not a hang.

Check it's working:

```bash
docker compose ps                             # four services, all "Up"
curl https://yourname.duckdns.org/health      # {"status":"ok"}
```

`docker compose ps` should show `bot-service` as `Up (healthy)`. If it sits at
`Up (health: starting)` for more than five minutes, look at its log — it is
almost always memory (see [Troubleshooting](#13-troubleshooting)):

```bash
docker compose logs -f bot-service
```

Then open `https://yourname.duckdns.org` in a browser and create an account.

To create an unlimited owner key (there is deliberately no web endpoint for
this):

```bash
docker compose exec api npm run create-owner-key -- you@example.com "Your Name"
```

## 8b. Check it end to end

Five minutes now saves a confused bug report later. In a browser:

| # | Do this | Expect |
|---|---|---|
| 1 | Open `https://yourname.duckdns.org` | The landing page, with a padlock in the address bar |
| 2 | Create an account with an email and password | You land on the dashboard |
| 3 | Dashboard → pick a template → upload a small FAQ sheet (CSV with Question and Answer columns) | "Indexed N questions" |
| 4 | Dashboard → "Try it" → ask one of those questions | The stored answer comes back |
| 5 | Dashboard → create an API key → run the `curl` shown on the dashboard | A JSON answer, with `X-Credits-Remaining` in the headers |
| 6 | Dashboard → "Put it on your website" → download a widget package | A zip downloads; its README shows your `https://` domain, not `localhost` |
| 7 | Open `https://yourname.duckdns.org/admin` and sign in | The admin panel, with your account listed |
| 8 | Click the Support button, send a message, then open `/admin/support` | The message is in the inbox |

If you set up Firebase (step 9), also check that "Continue with Google" appears
on the sign-in page and completes.

Failures here almost always map to a row in
[Troubleshooting](#13-troubleshooting).

## 9. Optional: Firebase sign-in

Without these settings the "Continue with Google" button stays hidden and
sign-in falls back to the local password path.

Firebase runs the sign-in; this server verifies the ID token it issues and
turns it into the same session cookie a password login produces.

**In the [Firebase console](https://console.firebase.google.com)** for your
project:

| Where | What |
|---|---|
| Authentication -> Sign-in method | Enable **Google** and **Email/Password**. Neither is on in a new project. |
| Authentication -> Settings -> Authorized domains | Add `yourname.duckdns.org`. Sign-in fails with `auth/unauthorized-domain` without it. |
| Project Settings -> General -> Your apps -> Web | Register a web app and copy its config. |
| Project Settings -> Service accounts | Generate a new private key. This is the server credential. |

**In `.env`** — the server half. This is a private key that can mint
credentials for any user in the project, so it stays here and never reaches
the browser:

```bash
FIREBASE_PROJECT_ID=<project id>
FIREBASE_CLIENT_EMAIL=<service account email>
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----
MII...
-----END PRIVATE KEY-----
"
```

Keep the newlines escaped and the value quoted, or the shell eats the
escapes and the PEM fails to parse.

**Also in `.env`** — the browser half. These are public: Vite inlines them
into the bundle every visitor downloads. A Firebase web apiKey is a project
identifier, not a secret.

```bash
NEXT_PUBLIC_FIREBASE_API_KEY=<apiKey>
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=<project id>.firebaseapp.com
NEXT_PUBLIC_FIREBASE_PROJECT_ID=<project id>
NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=<project id>.firebasestorage.app
NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=<sender id>
NEXT_PUBLIC_FIREBASE_APP_ID=<app id>
NEXT_PUBLIC_FIREBASE_MEASUREMENT_ID=G-XXXXXXXXXX
```

The `NEXT_PUBLIC_*` values are read at **build** time, not run time — they are
passed to the image as build args by `docker-compose.yml`. Changing one means
rebuilding, not just restarting:

```bash
docker compose --profile tls up -d --build
```

## 10. Optional: LLM features

The FAQ bot never needs an LLM. It answers straight from the customer's sheet
in about 18 ms. An LLM is only used by two optional features:

| Flag | Feature |
|---|---|
| `ASSISTANT_ENABLED=true` | The chat assistant in the signed-in dashboard |
| `LLM_ENABLED=true` | Lets bot owners turn on grounded rewording of near matches |

### The fallback chain

Models are tried in the order set by `LLM_PROVIDER_ORDER`
(default `ollama,groq,gemini`):

```
Ollama models (local) ──all fail──▶ Groq models ──all fail──▶ Gemini models × every key
```

- **Rate limited, busy, missing or slow:** the model is skipped for
  `LLM_COOLDOWN_SECONDS` (default 60) and the next one answers.
- **Key rejected (invalid or revoked):** that key is skipped for every model
  for `LLM_BAD_KEY_COOLDOWN_SECONDS` (default 3600).
- **Everything fails:** nothing breaks. The bot sends the stored answer word
  for word.

You only need to set up the providers you want. Any provider without a key
(or, for Ollama, without a running server) is simply skipped.

### Option A: hosted only (Groq and/or Gemini)

This is the lightest setup and needs no extra memory on the server. Add to
`.env`:

```bash
ASSISTANT_ENABLED=true
LLM_ENABLED=true

GROQ_API_KEY=<your groq key>
GEMINI_API_KEYS=<key1>,<key2>,<key3>,<key4>
```

Each Gemini model is tried on every key before moving to the next model, so a
key that hits its free-tier limit hands over to the next key.

> **Privacy:** Groq and Gemini are hosted APIs. When they answer, the prompt,
> including rows from the customer's sheet, leaves your server.

Apply it:

```bash
docker compose --profile tls up -d
```

### Option B: add local models with Ollama

Local models keep prompts on your server, but a 7B model holds about 7 GB of
RAM, and on two ARM cores expect 2–5 s to the first word.

```bash
docker compose --profile tls --profile llm up -d

# Pull every model listed in OLLAMA_MODELS
for m in qwen2.5:7b llama3.1:8b gemma2:9b; do
  docker compose exec ollama ollama pull $m
done
```

Ollama keeps one model in memory at a time, so falling back from one local
model to another costs a model load of tens of seconds. From now on, include
`--profile llm` in every `up` command.

## 11. Updating

```bash
cd ~/nexora
git pull                                      # or re-upload and unzip
docker compose --profile tls up -d --build
```

Compose rebuilds only what changed. That is worth knowing here, because the
three images are very different sizes:

| Changed | Rebuilds | Roughly |
|---|---|---|
| `next-frontend/` | `web` | 1–2 min |
| `node-backend/` | `api` | 1–2 min |
| `bot-service/` | `bot-service` | 15–25 min |
| `requirements.txt` in `bot-service/` | `bot-service`, from the torch layer down | 20–30 min |

So a change to pricing, the admin panel or the dashboard is a two-minute
deploy. Only a change to retrieval, the readers or the Python dependencies
pays for the heavy image.

To rebuild one service without touching the others:

```bash
docker compose --profile tls up -d --build api
```

**A note on the `NEXT_PUBLIC_*` values.** They are compiled into the browser
bundle at build time, so changing them in `.env` does nothing until `web` is
rebuilt — `docker compose restart web` will not pick them up. Everything else
(`MONGO_URI`, the admin key, the Firebase service account, every threshold) is
read at run time, so those only need a restart.

Roll back by checking out the previous commit and running the same command;
nothing in the database changes shape between builds.

## 12. Backups

All data is in MongoDB, so backing up the database backs up everything.

- **Atlas:** paid tiers include automatic snapshots. On the free M0 tier, export
  it yourself from any machine with the MongoDB Database Tools installed:

```bash
mongodump --uri "mongodb+srv://user:password@cluster0.xxxxx.mongodb.net/nexora" \
  --out backup-$(date +%F)
```

Restore with `mongorestore --uri "<uri>" backup-YYYY-MM-DD`.

Oracle reclaims **idle** Always Free instances (low CPU, network and memory
over 7 days). A site with real traffic usually doesn't qualify, but keep
backups off the instance.

## 13. Troubleshooting

| Symptom | Likely cause |
|---|---|
| No certificate, Caddy keeps retrying | Port 80 closed in the VCN or `iptables`, or DNS doesn't point at the instance yet |
| Site loads, but nobody stays signed in | `BILLING_COOKIE_SECURE` isn't `true`, or `PUBLIC_BASE_URL` is still `http://` |
| Container restarts in a loop | `MONGO_URI` is wrong, or the instance IP isn't in Atlas Network Access |
| `bot-service` stuck at `health: starting` for 5+ min | It is loading the embedding model. Past that, it is almost always memory — check `docker compose logs bot-service` for an OOM kill and confirm the swap file from step 4 is active (`free -h`) |
| `api` won't start, log says it is waiting | `bot-service` never became healthy. Fix that first; `api` depends on it |
| Templates or `/v1/ask` return 503 | `api` cannot reach `bot-service`. Check both are `Up`, and that `INTERNAL_API_KEY` is the **same value** in both — a mismatch gives 403 from the bot service, which `api` reports as unavailable |
| Everything is `Up` but the site 502s | `web` is running but `api` isn't healthy, or Caddy is routing to the wrong upstream. `curl 127.0.0.1:3000` and `curl 127.0.0.1:8000/health` from the host to see which half is down |
| `/health` works, but templates return 500 | Same as above: the app starts, but can't reach the database |
| Google sign-in shows `auth/unauthorized-domain` | The domain isn't in Firebase -> Authentication -> Settings -> Authorized domains |
| Google button doesn't appear at all | `NEXT_PUBLIC_FIREBASE_API_KEY`/`NEXT_PUBLIC_FIREBASE_APP_ID` were empty at **build** time — rebuild with `--build` |
| Sign-in fails with `auth/operation-not-allowed` | The provider isn't enabled under Firebase -> Authentication -> Sign-in method |
| Widget installs but never connects | `PUBLIC_API_ORIGIN` unset or `http://`. Fix it, then download the package again |
| Assistant says the model is unavailable | `ASSISTANT_ENABLED` not `true`, no provider keys set, or Ollama started without `--profile llm` |
| A setting in `.env` has no effect | It isn't listed under `environment:` in `docker-compose.yml`, or the container wasn't recreated (`up -d`). A `NEXT_PUBLIC_*` value needs `--build`, not just `up -d` |
| `/admin` won't accept the password | `ADMIN_PASSWORD` is empty in `.env`, or the container wasn't recreated after setting it. Five wrong tries lock that address out for 15 minutes |
| Bots answer "no indexed FAQ sheet" | The sheet was never uploaded to this database, or `MONGO_URI`/`MONGO_DB_NAME` points at a different database than before |
| Atlas says the storage quota is full | FAQ vectors are the largest collection (`faq_vectors`). Remove unused bots or move off the 512 MB M0 tier |
