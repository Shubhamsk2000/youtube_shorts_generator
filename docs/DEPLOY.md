# Deploying Studio on AWS

Studio runs on one EC2 instance (c7i-flex.large: 2 vCPU, 4 GB RAM, Ubuntu 24.04). It listens only on the
server itself; you open it from your laptop through an SSH tunnel, so nothing is exposed to the internet.
You change code **only on the laptop**, push it to GitHub, and run one command to update the server.

```
laptop                                   GitHub                    EC2 instance
edit code → test → git push   ───────►   main   ◄─── git pull ───  scripts/server/update.sh
scripts/deploy.sh  ──── ssh ──────────────────────────────────►   (rebuild what changed, restart)
scripts/tunnel.sh  ──── ssh -L 8800 ──────────────────────────►   Studio on 127.0.0.1:8000
scripts/send.sh / fetch-exports.sh ──── rsync ────────────────►   data/
```

Cost: about $0.085 per hour while the instance runs, ~$3 a month for a 40 GB disk. **Stop the instance when you
are done**; a stopped instance only costs the disk.

### Two terminals, two machines

Look at the prompt before you type: each command belongs to one machine.

| Prompt | You are on | Run there |
|---|---|---|
| `shubham@HP-15:~$` | your laptop | everything in `scripts/` **except** `scripts/server/`: `ssh.sh`, `tunnel.sh`, `deploy.sh`, `send.sh`, `fetch-exports.sh`, `sync-settings.sh` |
| `ubuntu@ip-172-31-…:~$` | the AWS server | `scripts/server/setup.sh`, `scripts/server/update.sh`, `df -h`, `journalctl` |

`exit` leaves the server. The laptop scripts read `.deploy.env`, which exists only on the laptop: running one of
them on the server prints *Missing .deploy.env*.

### The project folder name on the server

This guide clones into `~/yt_video_gen`. If you cloned under another name (for example the repo's own name,
`~/youtube_shorts_generator`), set `APP_DIR=` in `.deploy.env` to that name and use it wherever a command below
says `yt_video_gen` on the server.

---

## Part 1: first-time setup (about 45 minutes, once)

### Step 1. A budget alert (2 min)

AWS console → search **Billing and Cost Management** → **Budgets** → **Create budget** → *Use a template* →
**Monthly cost budget** → amount **$20** → your email → Create. You get an email long before credits run out.

### Step 2. Check the instance (5 min)

AWS console → **EC2** → **Instances** → click your instance.

1. **OS**: the *Details* tab → *Platform details* / AMI name must say **Ubuntu** (24.04). If it says Amazon
   Linux, it is easiest to start over now: *Instance state → Terminate*, then *Launch instance* with
   **Ubuntu Server 24.04 LTS**, type **c7i-flex.large**, a new key pair (download the `.pem`), storage **40 GiB gp3**.
2. **Disk**: the *Storage* tab → the volume size. It must be at least **30 GiB, 40 recommended** (see *Disk space*
   below; `setup.sh` stops under 25 GB). To grow it, see *Growing the disk* in Troubleshooting: no reinstall needed.
3. **Firewall**: the *Security* tab → the security group → *Edit inbound rules*. Keep exactly **one** rule:
   type **SSH**, port 22, source **My IP**. Delete anything open to `0.0.0.0/0` other than that.
   (If your home internet IP changes later and SSH stops connecting, come back here and pick *My IP* again.)
4. Note the **Public IPv4 address** on the *Details* tab. It changes each time the instance starts.

### Step 3. Connect from your laptop (5 min)

Put the key somewhere outside the project and lock it down:

```bash
mkdir -p ~/.ssh && mv ~/Downloads/<your-key>.pem ~/.ssh/studio.pem && chmod 400 ~/.ssh/studio.pem
```

In the project folder, create `.deploy.env` (git ignores it):

```bash
cd /media/shubham/E238D10938D0DE1B/yt_video_gen
cat > .deploy.env <<'EOF'
SERVER=ubuntu@<public IPv4 from step 2>
KEY=~/.ssh/studio.pem
APP_DIR=yt_video_gen
EOF
scripts/ssh.sh 'uname -a && df -h /'     # answer "yes" if asked about the fingerprint
```

You should see `Ubuntu` and a disk of about 40G.

### Step 4. Push the code to GitHub (laptop, 2 min)

The server installs exactly what is on GitHub's `main`:

```bash
git status            # everything you want deployed must be committed
git add -A && git commit -m "Studio"
git push
```

### Step 5. Let the server read the repo (5 min)

The repo is private, so the server gets its own read-only key ("deploy key"):

```bash
scripts/ssh.sh
# now on the server:
ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N "" -C "studio-server"
printf 'Host github.com\n  IdentityFile ~/.ssh/github_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
cat ~/.ssh/github_deploy.pub          # copy this line
```

GitHub → your repo → **Settings → Deploy keys → Add deploy key** → title `studio-server`, paste the line,
leave *Allow write access* **off** → Add key. Then, still on the server:

```bash
git clone git@github.com:Shubhamsk2000/youtube_shorts_generator.git yt_video_gen
exit
```

### Step 6. Install everything on the server (15–20 min, unattended)

```bash
scripts/ssh.sh 'cd yt_video_gen && bash scripts/server/setup.sh'
```

`setup.sh` installs ffmpeg, Node 22, the Inter fonts, a 2 GB swap file, the Python environment and the UI, then
starts Studio as a service that also starts on every boot. It is safe to run again. It ends with
`Studio is running on the server`.

### Step 7. Copy your secrets and channel card (laptop, 1 min)

Git deliberately does not carry these. One command **on the laptop** copies `.env` (Gemini key),
`configs/anime.local.yaml` (channel card) and `data/anime/avatar.png`, and restarts Studio so it uses them:

```bash
exit                          # if you are still on the server
cd /media/shubham/E238D10938D0DE1B/yt_video_gen
scripts/sync-settings.sh
```

If it hangs: the instance isn't running, its IP changed (update `SERVER=`), or the security group's *My IP* is
not your current network.

### Step 8. Open Studio (1 min)

```bash
scripts/tunnel.sh             # leave it running
```

Open **http://localhost:8800**. This is the server's Studio (your laptop's own Studio, if running, stays on 8000).
Ctrl+C in that terminal closes the tunnel; the server keeps working.

Send media up, and the first runs download their models (Whisper ~250–480 MB) into the server's `.cache/`:

```bash
scripts/send.sh anime  "data/anime/episodes/[Anime Time] My Hero Academia - 01.mkv"
scripts/send.sh music  ~/Music/track.mp3 --mood hype
```

(Uploading through the Studio pages works too; `send.sh` is faster and resumes if the connection drops.)

---

## Part 2: everyday use

1. AWS console → EC2 → your instance → **Instance state → Start**. Wait ~1 minute.
2. Copy the new **Public IPv4** into `SERVER=` in `.deploy.env`.
3. `scripts/tunnel.sh` → http://localhost:8800 → start jobs (they keep running if you close the tunnel).
4. Approve the shorts you like and press **Export**.
5. `scripts/fetch-exports.sh` on the laptop (see below).
6. **Instance state → Stop**. (Not *Terminate*: that deletes the server.)

### Getting your exports

**Export** writes the final 1080×1920 videos to the **server's** disk; they stay there until you copy them down:

```
~/yt_video_gen/data/                      (on the server)
├── shorts/exports/<video id>/            long video → shorts
│     01_<title>.mp4, 01_<title>.txt …
└── anime/exports/<episode>/              anime edits
      01_<title>.mp4, 01_<title>.txt …
```

Each `.mp4` has a `.txt` beside it with the title, description and hashtags (and the song for song edits), ready to
paste into YouTube. To copy them to the laptop (instance running):

```bash
scripts/fetch-exports.sh
```

They land in `data/server-exports/shorts/…` and `data/server-exports/anime/…` in the project. It uses **rsync**:
only new or changed files are sent, an interrupted copy resumes when you rerun it, and nothing is deleted on
either side.

### Disk space

Fixed cost before any media: Ubuntu + ffmpeg + Node ~5 GB, the Python environment (PyTorch, Whisper, OpenCV) and
its pip cache ~5-6 GB, models ~1-2 GB, swap 2 GB: **~13 GB**. Then each anime episode is 0.3-1.5 GB plus ~1 GB of
work files, a long YouTube video 1-3 GB with its work files. A full disk makes a running render fail, so clean
up once your exports are on the laptop:

```bash
scripts/ssh.sh 'df -h / && du -sh yt_video_gen/data/*/*'                 # what uses the space
scripts/ssh.sh 'rm -rf yt_video_gen/data/shorts/exports/<video id>'       # an export you already fetched
```

Finished episodes and videos: delete them in Studio (their work files are the big part).

---

## Part 3: when you change the code

Rule: **edit only on the laptop**. The server is a copy of GitHub's `main`; `update.sh` refuses to run if
someone edited files on the server.

### The routine

```bash
# 1. change code on the laptop, then check it
.venv/bin/python -m pytest -q                # all tests pass
cd web && npm run build && cd ..             # only if you changed web/: checks types + builds
.venv/bin/studio serve                       # optional: try it at http://127.0.0.1:8000

# 2. save it to GitHub
git add -A
git commit -m "What changed, in one line"
git push

# 3. ship it (instance must be running, .deploy.env up to date)
scripts/deploy.sh
```

`deploy.sh` checks that everything is committed and pushed, then on the server: pulls the code, rebuilds only
what changed, restarts Studio and checks it came back. If a job is running it stops and tells you; wait, or use
`scripts/deploy.sh --force` (the job shows *Paused*; press **Resume** and it continues from its cache).

### What each kind of change needs

| You changed | What deploy does / what you do |
|---|---|
| Python code (`src/`) | restart: automatic |
| A dependency (`pyproject.toml`) | `pip install`: automatic (slower) |
| The UI (`web/`) | `npm ci` + build: automatic (about 1 min) |
| `configs/shorts.yaml`, `configs/anime.yaml` | travel with git: automatic |
| `deploy/studio.service` | reinstalled: automatic |
| `scripts/server/setup.sh` (new system package, font…) | run it again: `scripts/ssh.sh 'cd yt_video_gen && bash scripts/server/setup.sh'` |
| `.env` (Gemini key), channel card, avatar | not in git: `scripts/sync-settings.sh` |
| Songs, episodes | not in git: `scripts/send.sh` |

Tip: the channel card can also be edited directly in the server's Studio (*Channel* page); then it lives only on
the server.

### Something broke after a deploy: roll back

```bash
git log --oneline -5                          # find the last good commit
scripts/deploy.sh --force --ref <good-commit>
```

Fix the code on the laptop, push, then `scripts/deploy.sh` brings the server back to `main`.

---

## Troubleshooting

| Symptom | Check / fix |
|---|---|
| `scripts/ssh.sh` hangs or times out | Instance not running; or `SERVER=` has the old IP; or your home IP changed (security group → SSH → *My IP*) |
| `Permission denied (publickey)` | `KEY=` path wrong, or `chmod 400 ~/.ssh/studio.pem` |
| Tunnel says *address already in use* | `scripts/tunnel.sh 8801` and open http://localhost:8801 |
| Studio page doesn't load | `scripts/ssh.sh 'systemctl status studio --no-pager; journalctl -u studio -n 50 --no-pager'` |
| A job failed | its error shows on the job page; full log: the `journalctl` line above |
| Disk full | `scripts/ssh.sh 'df -h / && du -sh yt_video_gen/data/*/*'`; delete finished episodes in Studio (keeps exports), or remove old `data/*/exports` after fetching; or grow the disk (below) |
| `setup.sh`: *The disk is only 23 GB* | a 24 GiB volume shows as ~23 GB (Ubuntu keeps ~1 GB for boot); grow it to 40 (below) |
| *Missing .deploy.env* | you ran a laptop script on the server: `exit` first |

### Growing the disk

No stop, no reinstall; the files stay.

1. AWS console → EC2 → your instance → *Storage* tab → click the **Volume ID** → *Actions → Modify volume* →
   size **40** → Modify. AWS allows one change per volume **every 6 hours**, and a disk can only grow: pick the
   final size now.
2. Wait about a minute (state *optimizing* is fine). AWS has made the virtual disk bigger; Ubuntu's partition and
   filesystem don't know yet. On the server:

   ```bash
   lsblk                              # disk nvme0n1 shows 40G, partition nvme0n1p1 still ~23G
   sudo growpart /dev/nvme0n1 1       # stretch partition 1 over the disk
   sudo resize2fs /dev/nvme0n1p1      # stretch the filesystem over the partition
   df -h /                            # Size ~39G
   ```

   If `lsblk` shows other names (`xvda` / `xvda1`), use those. A reboot does the same automatically.
| YouTube link fails ("confirm you're not a bot") | YouTube blocks many AWS addresses: download on the laptop and `scripts/send.sh shorts video.mp4`, then use *upload* in Studio → Shorts |
| Gemini "daily quota" | free tier limit; jobs continue with the fallback models, or retry tomorrow |

---

## Coming next (phase 2: unattended)

The scheduled batch from the plan: AWS starts the instance at night, it processes an S3 inbox, writes the
shorts and a `report.html` to an S3 outbox, emails you, and switches itself off. It builds on this setup
(same server, same service); none of the steps above change.
