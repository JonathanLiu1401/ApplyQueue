# Sync across devices (private GitHub repo)

ApplyQueue can keep its state in **one private GitHub repository that you own**: the job queue,
the append-only application ledger, claim locks (so two computers or two agents never apply to
the same job), your job-portal logins and signed-in portal sessions.

> **This repository must stay private.** It holds your job portal passwords and sessions.
> ApplyQueue checks `private: true` through the GitHub API before every upload and refuses to
> write anything if the repo is public or internal. Never add collaborators you would not give
> your passwords to.

What is **never** written to the repo: your GitHub token, AI-provider keys or subscription
logins (Claude, Codex/ChatGPT, Grok, Cursor), email OAuth tokens, and cookies for email or AI
sites. A token guard checks every write.

## 1. Sign in with GitHub

ApplyQueue does not ship its own GitHub app, so you use your own (free, two minutes):

1. GitHub → Settings → Developer settings → **OAuth Apps** → New OAuth App. Any name and
   homepage URL (e.g. `http://localhost`) work; the callback URL is unused.
2. Tick **Enable Device Flow** and save. Copy the **Client ID**.
3. ApplyQueue → Settings → *Sync across devices* → paste the Client ID → **Sign in with GitHub**.
   Enter the shown code on github.com/login/device. The app asks for the `repo` scope because
   it creates and writes a private repository.

Alternative: a **fine-grained personal access token** limited to the one state repo with
*Contents: read and write* and *Metadata: read* (create the private repo yourself first).

## 2. Create or connect the repo

Enter a name (default `applyqueue-state`), confirm the privacy checkbox and press
**Create / connect private repo**. If the repo does not exist it is created private. On your
other computers sign in the same way and enter the same name.

## 3. Optional encryption

Off by default. When on, `credentials/` and `sessions/` are stored as AES-256-GCM ciphertext.
Press **Show key** on the first device and **Import key** on the others; without the key a
device keeps its own local logins and syncs everything else.

## How it behaves

- **Every 5 minutes** (and on *Sync now*) ApplyQueue pulls, merges, and pushes. Pushes are
  fast-forward only; if another device pushed first it merges and retries.
- **Offline**: changes are committed locally and uploaded when the network is back.
- **Conflicts**: ledger/queue files are merged line-by-line (`merge=union`); logins use the newest
  edit per site; sessions keep the newest save.
- **Locks**: starting a job writes `locks/<job>.json`. If another device holds an unexpired lock
  the job is skipped for 15 minutes. Locks expire after 2 hours (crashed devices), and are
  released when the job finishes.
- **Stop syncing** on a device deletes its local GitHub token and turns sync off; the local
  copy and the GitHub repo are left as they are (delete the repo on github.com if you want).
