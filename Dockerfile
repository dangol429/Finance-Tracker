# The API image.
#
# Two stages, and the split is the whole point: the `builder` stage installs
# dependencies into a virtualenv, and the final stage copies that venv into a
# fresh base image and adds nothing else. Whatever pip needed to *do* the
# install — its cache, its own machinery, any compiler a package might have
# wanted — stays behind in a layer that is never shipped. The result is an image
# that contains the app, its dependencies and a Python runtime, and no build
# tooling for anyone who gets a shell in it to use.
#
# The other thing this file is arranged around is **layer caching**. Docker
# caches each instruction and reuses it until one of its inputs changes, so the
# order of the COPY lines below decides whether editing a router re-runs `pip
# install`. Requirements are copied and installed *before* the application code
# for exactly that reason.

# --- Stage 1: build the virtualenv -----------------------------------------

# Pinned to a minor version, not `python:3.12` and certainly not `python:latest`.
# A floating tag means the image silently changes under you between two builds
# of the same commit, which is the precise opposite of what containerising this
# was supposed to buy. `-slim` is the Debian base without the ~700 MB of
# toolchain and docs the default image carries.
FROM python:3.12-slim AS builder

# A virtualenv inside a container looks redundant — the container is already
# isolated — and it earns its place anyway: it puts every installed package
# under one directory that the next stage can copy in a single COPY. Installing
# into the system Python instead scatters files across /usr/local/lib,
# /usr/local/bin and dist-packages, and copying *that* selectively between
# stages is fiddly enough that people give up and ship the build image.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# Just the requirements file, on its own line, before any application code.
# This is the caching decision: the expensive layer below depends only on this
# file, so it is rebuilt when a dependency changes and reused on every build
# where only the app changed. Copying the whole project first — the tempting
# `COPY . .` — would invalidate the install on every single source edit and turn
# a two-second rebuild into a ninety-second one.
COPY requirements.txt .

# `--no-cache-dir`: pip's wheel cache is useless in an image that will never
# install anything again, and it is tens of megabytes.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt


# --- Stage 2: the runtime image --------------------------------------------

FROM python:3.12-slim AS runtime

# PYTHONDONTWRITEBYTECODE: don't litter .pyc files into the container's
#   filesystem. They would be written to a fresh, throwaway layer on every run
#   and buy nothing, since the interpreter starts from the same source each time.
# PYTHONUNBUFFERED: send stdout/stderr straight out instead of block-buffering
#   them. Without it a crashed container's last words sit in a buffer that is
#   discarded with the process, and `docker logs` shows a tidy nothing.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# A non-root user to run as. Containers run as root by default, and root in the
# container is root on the host kernel — a container escape, or simply a
# bind-mounted volume, then has write access it should never have had. This is
# the cheapest hardening step available and it is one line.
#
# Created before the venv is copied so the layers below can be owned correctly
# without a second `chown` pass over the whole tree.
RUN useradd --create-home --uid 1000 appuser

# The only thing carried over from the build stage. No pip cache, no build
# tooling, no requirements.txt — just the installed packages.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Application code last, because it is what changes most often. Everything above
# this line stays cached across ordinary edits.
COPY --chown=appuser:appuser app ./app

USER appuser

# Documentation, not a firewall rule: EXPOSE records which port the process
# listens on so `docker run -P` and tooling can find it. Publishing the port is
# still the run command's or compose file's job.
EXPOSE 8000

# `--host 0.0.0.0`, and this is the one line people lose an afternoon to.
# Uvicorn's default is 127.0.0.1, the loopback interface *inside the container*,
# which nothing outside it can reach — the container starts, the logs look
# perfect, and every request from the host is refused. Binding to 0.0.0.0 means
# "all interfaces in this network namespace", which is what makes the published
# port work.
#
# No `--reload` here: the image is the production artifact, and the reloader
# watches the filesystem and spawns a child process for no benefit when the code
# is baked in. The compose file overrides this command for development, where
# the code is bind-mounted and reloading is the entire point.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
