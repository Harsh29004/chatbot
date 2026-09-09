# Deploying on Oracle Cloud Always Free

The whole product — API, web app, vector store and a local 7B model — on one
machine that costs nothing, forever.

## Why this box

The assistant and the grounded-rewording feature need a language model. Running
one is a memory problem before it is anything else, and that rules out most
free hosting:

| Host | Free allowance | Fits a 7B model? |
|---|---|---|
| Railway free | 0.5 GB RAM, $1/mo credit | No — not even close. Won't fit the app either. |
| Railway Hobby | $5/mo *minimum plus usage* | Technically, but ~6 GB held 24/7 bills far past $5. |
| Oracle Always Free (ARM) | 2 OCPU / 12 GB / 200 GB | **Yes**, with room for the app beside it. |

Oracle halved this allowance in June 2026 — it was 4 OCPU / 24 GB. 12 GB is
still enough, which is why the memory limits in `docker-compose.yml` are
explicit rather than left to the kernel.

## 1. Provision the instance

Create an **Ampere A1 (aarch64)** compute instance:

- **Shape:** VM.Standard.A1.Flex — 2 OCPU, 12 GB RAM
- **Image:** Ubuntu 22.04 or 24.04 (ARM build)
- **Boot volume:** 100 GB or more (the model is ~4.7 GB, the image another few)

> **If you get "Out of host capacity"** — that is normal for A1 and not
> something you did wrong. Try a different availability domain, or a different
> home region. Frankfurt and Singapore usually provision quickly; US East often
> does not.

Open port 8000 in **both** places, because Oracle has two layers of firewall
and forgetting the second is the classic wasted afternoon:

```bash
# 1. The VCN security list / NSG — add an ingress rule for TCP 8000 (in the console)

# 2. The instance's own iptables, which Oracle's Ubuntu images ship locked down
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save
```

## 2. Add swap

12 GB is enough for steady state but not for the moment Ollama loads a model
while the app is starting. Swap turns a possible OOM kill into a slow few
seconds.

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 3. Install Docker

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER && newgrp docker
```

## 4. Configure

```bash
git clone <your repo> nexora && cd nexora
cp .env.example .env
```

Edit `.env`:

```bash
ADMIN_API_KEY=<paste from: python -c "import secrets; print(secrets.token_urlsafe(32))">
WEB_ORIGINS=https://your.domain
PUBLIC_BASE_URL=https://your.domain
BILLING_COOKIE_SECURE=true

# The model features
OLLAMA_MODEL=qwen2.5:7b
ASSISTANT_ENABLED=true      # the dashboard chat
LLM_ENABLED=true            # lets bot owners opt in to grounded rewording
```

`ASSISTANT_ENABLED` and `LLM_ENABLED` are independent. The assistant is a
website feature for signed-in customers; `LLM_ENABLED` only makes the per-bot
rewording toggle available, and each bot still has to be switched on
individually from its dashboard.

## 5. Start it, then pull the model

```bash
docker compose up -d --build

# The model is NOT in the image — it lives on a named volume so a rebuild
# doesn't re-download 4.7 GB. Pull it once:
docker compose exec ollama ollama pull qwen2.5:7b
```

The first build takes a while: it compiles nothing, but it does install torch
and bake the embedding model into the image so a cold container doesn't stall
on its first request.

Check it came up:

```bash
curl localhost:8000/health
docker compose exec ollama ollama list
```

## 6. Put TLS in front

Session cookies are `Secure` in production, so the site needs HTTPS or nobody
can stay signed in. Caddy is the least effort:

```bash
sudo apt install -y caddy
```

```caddyfile
# /etc/caddy/Caddyfile
your.domain {
    reverse_proxy localhost:8000 {
        # The assistant streams server-sent events. Without this, the proxy
        # buffers the whole answer and the user watches a spinner for a minute
        # instead of watching the text arrive.
        flush_interval -1
    }
}
```

```bash
sudo systemctl reload caddy
```

Then set `BILLING_COOKIE_SECURE=true` and point `WEB_ORIGINS` /
`PUBLIC_BASE_URL` at `https://your.domain`.

## What to expect

On 2 Ampere cores with `qwen2.5:7b`:

| | Typical |
|---|---|
| First token | 2–5 s |
| Generation | ~3–6 tokens/sec |
| A 200-word answer | 60–90 s, streamed |
| FAQ bot answer (no model) | still ~18 ms |

The FAQ bot is unaffected by any of this — it does not call a model unless a
bot's owner opted in, and never on a strong match.

**It will not match ChatGPT, Gemini or Claude.** Those are hundreds of billions
of parameters on datacenter GPUs; this is 7 billion on two free ARM cores. It
is genuinely useful for chat, drafting, summarising and straightforward
reasoning, and noticeably weaker on hard reasoning, long documents and code.
Streaming is what makes the speed acceptable — the answer is readable while it
is still being written.

### If it feels too slow

- `OLLAMA_MODEL=qwen2.5:3b` — roughly twice the speed, noticeably less capable.
- Raise `ASSISTANT_CONCURRENCY` only if you move to a bigger box. On 2 cores it
  makes two people wait instead of one.

## Keeping it alive

Oracle reclaims **idle** Always Free compute instances. The advertised rule is
sustained low CPU, low network and low memory over a 7-day window; an instance
holding a model in memory and serving a site does not usually qualify, but it
is worth knowing the policy exists before you put anything you care about on
it. Take backups of the volumes:

```bash
docker run --rm -v nexora_sqlite-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/sqlite-$(date +%F).tar.gz -C /data .
docker run --rm -v nexora_chroma-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/chroma-$(date +%F).tar.gz -C /data .
```
