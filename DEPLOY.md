# Deploying on Oracle Cloud Always Free

The whole product — API, web app and vector store — on one machine that costs
nothing, forever, behind a real HTTPS certificate.

Everything is served from **one origin**: the container serves the built SPA
and the API together. That is not laziness, it is what lets the session cookie
stay `SameSite=Lax` with no cross-site handling anywhere. Splitting the
frontend onto another host means changing the cookie policy, adding CORS
origins, and putting TLS on the API anyway — more work, for a CDN you do not
need yet.

## What you need before you start

| | |
|---|---|
| Oracle Cloud account | Always Free tier, card verified (not charged) |
| A hostname | A free DuckDNS subdomain is fine — step 4 |
| MongoDB | Atlas M0 free cluster — step 5 |

## 1. Provision the instance

Create an **Ampere A1 (aarch64)** compute instance:

- **Shape:** VM.Standard.A1.Flex — 2 OCPU, 12 GB RAM
- **Image:** Ubuntu 24.04 (ARM build)
- **Boot volume:** 100 GB or more
- Save the SSH private key it offers you. There is no second chance at it.

> **If you get "Out of host capacity"** — that is normal for A1 and not
> something you did wrong. Try a different availability domain, or try again
> later. It frees up in waves.

## 2. Open the ports

Oracle has two firewalls and forgetting the second one is the classic wasted
afternoon. Ports **80 and 443**, in both places. Port 80 is not optional even
though the site redirects away from it — Let's Encrypt validates over port 80,
and without it you get no certificate at all.

In the console: **VCN → Security Lists → Add Ingress Rules**, source
`0.0.0.0/0`, TCP, destination ports 80 and 443.

Then on the instance itself:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## 3. Install Docker and add swap

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER && newgrp docker
```

Swap is not for steady state — 12 GB is plenty for the app. It is for the
build, where installing torch on two ARM cores briefly wants more than you
would expect. It turns a possible OOM kill into a slow few seconds.

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 4. Point a hostname at it

Copy the instance's **public IP** from the console. Then at
[duckdns.org](https://www.duckdns.org) sign in with GitHub or Google, claim a
subdomain, and paste the IP into its "current ip" box.

Check it resolves before going further. If this is wrong, the certificate
request in step 7 fails and Caddy retries on a backoff that is slow enough to
be confusing:

```bash
dig +short yourname.duckdns.org    # must print your instance's public IP
```

## 5. Create the database

At [mongodb.com/atlas](https://www.mongodb.com/atlas) create a free **M0**
cluster. 512 MB, free forever, no card.

Two things to get right:

- **Database user** — create one and keep the password. It goes in the
  connection string.
- **Network access** — add the instance's public IP. `0.0.0.0/0` also works
  and is what most people end up doing, but it means the only thing between
  your data and the internet is that password, so make it a long one.

Then **Connect → Drivers** and copy the `mongodb+srv://…` string.

## 6. Configure

```bash
git clone <your repo> nexora && cd nexora
cp .env.example .env
```

Edit `.env`:

```bash
# Where it lives
SITE_DOMAIN=yourname.duckdns.org
PUBLIC_BASE_URL=https://yourname.duckdns.org
WEB_ORIGINS=https://yourname.duckdns.org

# The session cookie is Secure in production. Without HTTPS and this flag
# nobody can stay signed in, and the failure looks like a login bug.
BILLING_COOKIE_SECURE=true

# Database
MONGO_URI=mongodb+srv://user:password@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
MONGO_DB_NAME=nexora

# Generate this, do not invent it:
#   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
ADMIN_API_KEY=<paste the output>
```

Leave `BILLING_ALLOW_MANUAL=false`. It activates paid plans without taking
payment, which is useful in development and nowhere else.

## 7. Start it

```bash
docker compose --profile tls up -d --build
```

The first build takes 15–30 minutes on two ARM cores. Most of that is
installing torch and baking the embedding model into the image, which is done
at build time so a cold container does not stall on its first request.

Then:

```bash
curl https://yourname.duckdns.org/health
docker compose logs -f caddy      # certificate issuance, if it did not work
```

## What is deliberately not running

**Ollama.** The dashboard assistant and grounded rewording need a language
model, and a 7B model wants ~7 GB held resident. The FAQ bot does not use it:
it embeds the question, finds the closest row in the customer's sheet, and
returns that answer in about 18 ms with no model anywhere in the path.

Turn it on later, when you want the assistant, not before:

```bash
docker compose --profile tls --profile llm up -d
docker compose exec ollama ollama pull qwen2.5:7b   # 4.7 GB, once
```

Then set `ASSISTANT_ENABLED=true` and/or `LLM_ENABLED=true` in `.env` and
restart. The two flags are independent: the assistant is a dashboard feature
for signed-in customers, while `LLM_ENABLED` only makes the per-bot rewording
toggle available, and each bot still opts in separately from its own
dashboard.

On two Ampere cores expect 2–5 s to first token and ~3–6 tokens/sec. Streaming
is what makes that acceptable — the answer is readable while it is still being
written. It will not match ChatGPT or Claude, and it is not trying to.

## Keeping it alive

Oracle reclaims **idle** Always Free compute. The rule is sustained low CPU,
network and memory over a 7-day window. An instance serving a site does not
usually qualify, but the policy exists and it is worth knowing before you put
something you care about on it.

Back up the vector store — Mongo is Atlas's problem, this one is yours:

```bash
docker run --rm -v nexora_chroma-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/chroma-$(date +%F).tar.gz -C /data .
```

## When something is wrong

| Symptom | Cause |
|---|---|
| No certificate, Caddy retrying | Port 80 closed in the VCN, or DNS not pointing at the instance yet |
| Site loads, nobody stays signed in | `BILLING_COOKIE_SECURE` not `true`, or `PUBLIC_BASE_URL` still `http://` |
| Container restarts on boot | `MONGO_URI` wrong, or the instance IP is not in the Atlas allowlist |
| `/health` fine, templates 500 | Same as above — the app starts, the database call does not |
