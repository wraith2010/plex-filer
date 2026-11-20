# Deploying plex-filer in an LXC on Proxmox

## 1. Create the container (on the Proxmox host)
Ubuntu 24.04 template, **unprivileged**, 1 core, 512 MB RAM, 4 GB disk. Needs outbound HTTPS to api.anthropic.com.

## 2. Mount the media paths into the container
Easiest is bind mounts from the host (adjust CT id and host paths):

    pct set 201 -mp0 /data/stage,mp=/data/stage
    pct set 201 -mp1 /mnt/plex,mp=/mnt/plex

If the shares are NFS/SMB, mount them on the **Proxmox host** and bind-mount as above
(unprivileged containers can't mount network filesystems themselves).

### UID mapping (the usual gotcha)
In an unprivileged CT, container UID N appears as host UID N+100000. The `plexfiler`
user must be able to **read** /data/stage and **write** /mnt/plex. Options:
- Simplest: on the host, `chmod`/ACL the directories so host UID 101000 (CT uid 1000) has access, or
- add a custom idmap in `/etc/pve/lxc/201.conf` mapping CT uid 1000 straight to the host UID that Plex/the downloader use.

Verify inside the CT before going further:

    sudo -u plexfiler touch /mnt/plex/tvshows/.write-test && sudo -u plexfiler rm /mnt/plex/tvshows/.write-test

## 3. Install (inside the CT)

    apt update && apt install -y python3-venv
    useradd -r -m -d /opt/plex-filer plexfiler        # add to the right group for your mounts
    mkdir -p /etc/plex-filer /var/lib/plex-filer
    chown plexfiler: /var/lib/plex-filer
    # copy this project's plex_filer/ and requirements.txt to /opt/plex-filer
    python3 -m venv /opt/plex-filer/venv
    /opt/plex-filer/venv/bin/pip install -r /opt/plex-filer/requirements.txt
    cp config.example.toml /etc/plex-filer/config.toml
    install -m 600 -o plexfiler /dev/null /etc/plex-filer/env
    echo 'ANTHROPIC_API_KEY=sk-ant-...' > /etc/plex-filer/env

## 4. Email notifications (Gmail)

You get one email per run when something was filed, needs review, or failed. Quiet runs send nothing.
Subject lines: `Plex: 3 new items filed` or `Plex: ATTENTION - 1 error(s), 2 for review`.

### 4a. Create a Gmail app password
Gmail will not accept your normal password for scripts. You need an app password:
1. Sign in at https://myaccount.google.com/security and turn on **2-Step Verification** (required; app passwords don't exist without it).
2. Open https://myaccount.google.com/apppasswords (or search "App passwords" in your Google account settings).
3. Name it `plex-filer` and click **Create**.
4. Google shows a 16-character password like `abcd efgh ijkl mnop`. Copy it now; it is shown only once.
   **Remove the spaces** when you save it.

Notes: if the app-passwords page is missing, 2-Step Verification is off, or the account is a
Workspace account whose admin disabled it, or Advanced Protection is on. Revoke the password
any time from the same page.

### 4b. Store the credentials (inside the CT)
Append to `/etc/plex-filer/env` (mode 600, owned by plexfiler; no quotes, no spaces around `=`):

    SMTP_USER=youraddress@gmail.com
    SMTP_PASSWORD=abcdefghijklmnop

### 4c. Enable it in the config
In `/etc/plex-filer/config.toml`, set `[notify]` `notify_enabled = true` and `mail_to` to where
you want mail delivered (it can be the same Gmail address).

### 4d. Send a test email

    sudo -u plexfiler env $(cat /etc/plex-filer/env) \
      /opt/plex-filer/venv/bin/python -m plex_filer -c /etc/plex-filer/config.toml --test-email

Check the inbox (and spam). If it landed in spam, mark it "Not spam" and create a Gmail filter on
subject `Plex:` with **Never send it to Spam**. Mail sent from and to the same address is
sometimes filtered, so check All Mail too.

Troubleshooting: `Username and Password not accepted` means a wrong or space-containing app password
or a typo in `SMTP_USER`; `timed out` means the CT can't reach smtp.gmail.com:587 (firewall/DNS).

## 5. Dry run first (calls Claude, copies nothing, records nothing, sends no email)

    sudo -u plexfiler env $(cat /etc/plex-filer/env) \
      /opt/plex-filer/venv/bin/python -m plex_filer -c /etc/plex-filer/config.toml --dry-run

Read the plan. Compare with what FileBot produced for the same files. Tune `min_confidence` if needed.

## 6. Enable

    cp deploy/plex-filer.{service,timer} /etc/systemd/system/
    systemctl daemon-reload && systemctl enable --now plex-filer.timer
    journalctl -u plex-filer -f

## Operations
- Review queue: `/data/stage/review/<date>/` holds the file plus `<file>.json` with Claude's guess and why it was rejected.
  File it by hand, or `touch` the original in the source dir to re-queue it after fixing the cause.
- Existing destination = treated as a duplicate and skipped (the source is never deleted).
- API outage: affected files are not recorded in the ledger, so the next run retries them.
