# Erbium

![GitHub Release](https://img.shields.io/github/v/release/amplimit/Erbium)
![GitHub Stars](https://img.shields.io/github/stars/amplimit/Erbium)

![poster](assets/poster.png)

Erbium is amplimit's internal compute platform, forked from
[ProjectNeura/Erbium](https://github.com/ProjectNeura/Erbium) under the Apache License 2.0. It is designed to suit mid-scale organizational needs and 100%
open-source to avoid concerns about backdoors. It includes a minimal account system for small trusted teams:
administrators create accounts, and members sign in to queue for the node. Our main goal is to build a general and scalable solution for automatic job
scheduling, orchestration, and reinforced file backup. We bring these together using C++ integrated with Python running
in Docker containers, and services exposed via APIs. With the distributed nodes organized using Cloudflare, we are able
to manage dozens to hundreds of nodes without hiring any IT ops. Each host machine simply needs to install Erbium and
run the Docker containers. It is 99% similar to Vast.ai with very little complexity.

Currently, we only considered hosts with Nvidia GPUs, as they are the most developer-friendly and widely used. You can
simply fork this repository and replace the driver-related code with your own.

## Key Features

- SSH Tunneling
- JupyterLab
- Job Scheduling
- Accounts, Admin Console, and Per-Session SSH Credentials
- Email Notifications
- File History Backup

Some facts:

- SSH tunnel defaults to ~, not "/workspace"
- JupyterLab can only explore files in "/workspace", but its terminal can access anything
- Only the output folder "/workspace/output" is backed up every 12 hours
- Jobs that reach their requested duration will be killed
- All members share the "access" Linux user, so files in its home and "/workspace" persist between reservations and are
  visible to the next member; remove personal tokens before your reservation ends
- Codex, Claude Code, and GitHub CLI are signed out automatically when a reservation ends

## Installation

```shell
pip install git+https://github.com/amplimit/Erbium
```

## Accessing an Erbium Node

Each node serves three hostnames through Cloudflare Tunnel. For a node named "main":

- https://node-main-erbium.amplimit.com is the dashboard. Sign in with the account an administrator created for
  you, join the queue, and manage your settings.
- main-erbium.amplimit.com is SSH. The username is always "access".
- https://jupyter-main-erbium.amplimit.com is JupyterLab.

When your reservation starts, the dashboard shows a one-time SSH password and a JupyterLab link for that session, and
installs the SSH public keys saved in your settings. When the reservation ends, all processes of the session are killed
and the credentials stop working. Add an email address in settings to be notified when your job starts or is about to
end.

To use other nodes, simply replace "main" with the name of the node.

## Setting Up Your Workstation as a Host

### Clone the Repository

Keep the deployment in a root-owned directory, because the generated Compose file mounts files from it into the
container:

```shell
sudo git clone https://github.com/amplimit/Erbium /opt/erbium-deploy/Erbium
```

### Install the Dependencies

```shell
sudo python3 -m venv /opt/erbium-deploy/venv
sudo /opt/erbium-deploy/venv/bin/pip install -e /opt/erbium-deploy/Erbium
cd /opt/erbium-deploy/Erbium
```

The remaining commands run from this directory as root, for example with `sudo /opt/erbium-deploy/venv/bin/python -m
erbium ...`.

### Set Up Cloudflare Tunnel

You need to save the Cloudflare Tunnel token locally as "cloudflared_tunnel_token.txt" in the root directory of the
project.

Then, run the following command to start the tunnel:

```shell
python -m erbium docker init
```

### Build a Docker Image

> Rerun this step after pulling a new version of the repository or changing any of the options below

```shell
python -m erbium docker create -n ${SERVICE_NAME} ${INPUT_DIR} ${OUTPUT_DIR} ${BACKUP_DIR} ./docker-compose.yaml
```

To disable automatic output backups, omit `BACKUP_DIR` and pass `--no-backup`:

```shell
python -m erbium docker create -n ${SERVICE_NAME} --no-backup ${INPUT_DIR} ${OUTPUT_DIR} ./docker-compose.yaml
```

To use the host's Python environment instead of a virtual environment, mount its site-packages read-only and pick a
base image whose `python3` has the same version as the host. For example, on an Ubuntu 26.04 host:

```shell
python -m erbium docker create -n ${SERVICE_NAME} -b ubuntu:26.04 --host_site_packages /usr/local/lib/python3.14/dist-packages --host_site_packages /usr/lib/python3/dist-packages ${INPUT_DIR} ${OUTPUT_DIR} ${BACKUP_DIR} ./docker-compose.yaml
```

The first directory is mounted at the same path; further ones, such as the apt-managed packages, are appended to
`sys.path` in the given order. Members then use `/usr/bin/python3` directly, notebooks run on the same interpreter, and the console scripts of the host
packages are available on `PATH`. The mounted packages cannot be modified from the container; use `pip install --user
--break-system-packages` for personal additions.

### Start the Docker Container

```shell
python -m erbium docker run ./docker-compose.yaml ${SERVICE_NAME}
```

The container restarts automatically after crashes and host reboots (`restart: unless-stopped`). Every start reruns the
setup script, so it takes a few minutes before the dashboard is reachable again. Running jobs survive restarts: the
scheduler grants the session again once the server is back, but open SSH and Jupyter connections are dropped.

The Compose project is named `erbium-${SERVICE_NAME}`, and its volumes hold the accounts, the queue, and the shared home
directories. Stop the container with `docker compose -f ./docker-compose.yaml stop`, and never pass `-v` to
`docker compose down`, which deletes these volumes.

### Configure Email Notifications (Optional)

Create an env file outside the repository, for example `erbium.env`:

```shell
RESEND_API_KEY=re_xxxxxxxx
ERBIUM_MAIL_FROM=Erbium <erbium@your-verified-domain.org>
ERBIUM_PUBLIC_URL=https://node-main-erbium.amplimit.com
```

Pass it with `--env_file ./erbium.env` when running `docker create`. Without it, notifications are disabled.

### Create the First Administrator

After the container is running:

```shell
docker exec -it ${SERVICE_NAME} /opt/erbium/venv/bin/python -m erbium user add ${USERNAME} --admin
```

Further accounts can be created from the admin page at `/admin`, or with `erbium user add` / `passwd` / `remove` /
`list`. Accounts, the queue, and settings are stored in `/var/lib/erbium` on a dedicated Docker volume.

### Update Erbium

The container installs the Erbium server from the `main` branch the first time it starts. To update it, reinstall it
inside the container and restart:

```shell
docker exec ${SERVICE_NAME} /root/.local/bin/uv pip install --python /opt/erbium/venv/bin/python --reinstall-package erbium git+https://github.com/amplimit/Erbium
docker restart ${SERVICE_NAME}
```

Changes to the Compose template, such as new mounts or options, additionally require pulling the repository in
`/opt/erbium-deploy/Erbium`, rerunning `docker create`, and starting the container again with `docker run`.

## Using Erbium as a Resource Monitor

Add the following code to your main script.

```python
from erbium.api import ResourceMonitor

if __name__ == "__main__":
    monitor = ResourceMonitor("path/to/save/reports")
    monitor.start()
    ...  # your job
```

## Using Erbium on Vast

### Initialize the Machine

You need an access token from Hugging Face that can access the bucket. You can specify the bucket to use by setting
`--hf_bucket ${BUCKET_NAME}`. By default, it will use the bucket `ProjectNeura/ErbiumOnVast`.

```shell
python -m erbium_on_vast init --node_id ${NODE_ID} --hf_token ${HF_TOKEN}
```

### Upload the Workspace to Hugging Face

```shell
python -m erbium_on_vast upload
```

### Download the Workspace from Hugging Face

```shell
python -m erbium_on_vast download
```
