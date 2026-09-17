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
9. [Optional: Google sign-in](#9-optional-google-sign-in)
10. [Optional: LLM features](#10-optional-llm-features)
11. [Updating](#11-updating)
12. [Backups](#12-backups)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Before you start

| You need | Notes |
|---|---|
| Oracle Cloud account | Always Free tier. A card is verified but not charged. |
| A domain name | A free [DuckDNS](https://www.duckdns.org) subdomain works. Google sign-in won't accept a bare IP address. |
| MongoDB | A free Atlas M0 cluster (step 6). |
| Optional API keys | Groq and/or Gemini, for the LLM features (step 10). |

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

Install Git LFS **before** cloning, or images arrive as small text files:

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
# Generate it: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
ADMIN_API_KEY=<paste the generated value>
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

The first build takes **15–30 minutes** on two ARM cores. Most of it is
installing torch and baking the embedding model into the image, so the first
request after a restart isn't slow.

Check it's working:

```bash
docker compose ps                             # nexora and caddy should be "Up"
curl https://yourname.duckdns.org/health      # {"status":"ok"}
```

Then open `https://yourname.duckdns.org` in a browser and create an account.

To create an unlimited owner key (there is deliberately no web endpoint for
this):

```bash
docker compose exec nexora python backend/scripts/create_owner_key.py you@example.com "Your Name"
```

## 9. Optional: Google sign-in

Without these settings, the "Continue with Google" button stays hidden and
email/password sign-in still works.

**In [Google Cloud Console](https://console.cloud.google.com/apis/credentials)**,
open your OAuth 2.0 client and add:

| Field | Value |
|---|---|
| Authorized JavaScript origins | `https://yourname.duckdns.org` |
| Authorized redirect URIs | `https://yourname.duckdns.org/api/auth/google/callback` |

**In `.env`:**

```bash
GOOGLE_CLIENT_ID=<client id>
GOOGLE_CLIENT_SECRET=<client secret>
GOOGLE_REDIRECT_URI=https://yourname.duckdns.org/api/auth/google/callback
```

The redirect URI must match **exactly** in both places: `https`, same domain,
no trailing slash. Otherwise Google shows `redirect_uri_mismatch`.

Apply it:

```bash
docker compose --profile tls up -d
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
git pull
docker compose --profile tls up -d --build    # add --profile llm if you use Ollama
```

Data survives updates: everything (accounts, FAQ vectors, support messages,
rate limits) lives in MongoDB Atlas, not in the container.

> `docker compose down -v` only deletes local volumes (Caddy's certificates and
> Ollama's downloaded models). Your data is safe, but you'd re-download those.

Useful commands:

```bash
docker compose logs -f nexora      # app logs
docker compose logs -f caddy       # certificate issues
docker compose restart nexora      # restart the app only
```

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
| `/health` works, but templates return 500 | Same as above: the app starts, but can't reach the database |
| Google sign-in shows `redirect_uri_mismatch` | `GOOGLE_REDIRECT_URI` doesn't exactly match the URI in Google Cloud Console |
| Widget installs but never connects | `PUBLIC_API_ORIGIN` unset or `http://`. Fix it, then download the package again |
| Assistant says the model is unavailable | `ASSISTANT_ENABLED` not `true`, no provider keys set, or Ollama started without `--profile llm` |
| A setting in `.env` has no effect | It isn't listed under `environment:` in `docker-compose.yml`, or the container wasn't recreated (`up -d`) |
| Bots answer "no indexed FAQ sheet" | The sheet was never uploaded to this database, or `MONGO_URI`/`MONGO_DB_NAME` points at a different database than before |
| Atlas says the storage quota is full | FAQ vectors are the largest collection (`faq_vectors`). Remove unused bots or move off the 512 MB M0 tier |
