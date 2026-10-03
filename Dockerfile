FROM python:3.13-slim

# ── OCI image metadata (org.opencontainers.image.*) ─────────────────────────
# The standard annotation keys, so an image describes itself instead of the tag
# name having to carry everything:
#   podman inspect --format '{{index .Labels "org.opencontainers.image.revision"}}' <image>
# answers "which commit is this?" for ANY tag — :staging, :latest or :v5.3.0.
#
# Only the values that never change live here. version/revision/created are
# applied by CI with `podman build --label`, which overrides these defaults.
#
# The source URL is the PUBLIC host, gitlab.e-meril.in (103.244.123.27), not the
# internal git.e-meril.in (10.10.30.160). Both serve the same GitLab, but the
# internal name does not resolve off the corporate network, and a provenance
# label nobody outside the office can open is not provenance. CI overrides this
# with $CI_PROJECT_URL, which GitLab sets from its own external_url — also the
# public name.
#
# They are deliberately NOT ARG + LABEL "${ARG}": buildah (1.33.7, the runner's
# version) does NOT invalidate a cached LABEL layer when a referenced build-arg
# changes, so on a shell-executor runner — which keeps its layer cache between
# pipelines — every build after the first would inherit the previous build's
# commit SHA. Verified by building twice with different --build-arg values and
# getting identical labels. `--label` is applied at commit time, outside the
# layer cache, so it is always correct. Labels that lie about provenance are
# worse than no labels.
#
# The defaults below are what a plain local `podman build` gets: honest about
# being an untracked build rather than claiming a version it does not have.
LABEL org.opencontainers.image.title="NexGen Platform" \
      org.opencontainers.image.description="Meril NexGen — Django/Daphne ASGI platform" \
      org.opencontainers.image.vendor="Meril Life Sciences" \
      org.opencontainers.image.licenses="UNLICENSED" \
      org.opencontainers.image.source="https://gitlab.e-meril.in/nexgen-it/nexgen.e-meril.in" \
      org.opencontainers.image.version="dev" \
      org.opencontainers.image.revision="unknown" \
      org.opencontainers.image.created="unknown"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# ✅ System dependencies (ONLY required ones)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ✅ Install Python dependencies first (better caching)
COPY requirements.txt /app/
# Build parallelism is pinned to one job for this step only.
#
# numpy is pinned to 1.26.4, which publishes no cp313 wheel — so on the
# python:3.13 base pip downloads numpy-1.26.4.tar.gz and COMPILES it. Meson
# defaults to one compiler per core (six here), and six concurrent C compiles
# exhausted the build VM's memory: the machine itself went down mid-build and
# podman came back with "server probably quit: unexpected EOF". Twice.
#
# -j1 trades build time for a build that finishes on modest hardware. The
# variables are set on this RUN rather than as ENV so they do not persist into
# the runtime image, which is byte-identical either way.
#
# The durable fix is to stop compiling numpy at all — either a base image with
# cp313 wheels for this pin (python:3.12) or a numpy new enough to ship them
# (>=2.1, which needs a pandas compatibility check first). Both change what
# production runs, so neither is done here.
RUN MAKEFLAGS="-j1" NPY_NUM_BUILD_JOBS=1 MAX_JOBS=1     pip install --no-cache-dir -r requirements.txt


# ✅ Copy project files
COPY . /app

# ✅ Create static directory with proper permissions
RUN mkdir -p staticfiles && chmod 755 staticfiles

# ✅ Expose Daphne port
EXPOSE 7000

# ✅ Run ASGI server (Channels)
CMD ["daphne", "-b", "0.0.0.0", "-p", "7000", "core.asgi:application"]
