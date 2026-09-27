# Distribution channels: PyPI → Debian (.deb + signed apt repo) → Homebrew

## Context

treehawk is currently installed only through `install.sh` or `uv tool install <wheel URL>`
from GitHub releases. `.github/workflows/release.yml` already does most of the work:
it runs whenever a new version lands on `main`, runs the full CI through `ci.yml`,
builds the sdist and the universal wheel (`py3-none-any`), and creates the GitHub
release `v<version>`. The goal is to add three channels in order, all driven by
that same release pipeline:

1. `pip install treehawk` / `uv tool install treehawk` / `pipx install treehawk` (PyPI)
2. `sudo apt install treehawk` (a .deb attached to each release, plus a signed apt repo)
3. `brew install ibadrather/tap/treehawk` (your own Homebrew tap)

What I checked:
- The name `treehawk` is free on PyPI (the API returns 404).
- Homebrew core has `python-matplotlib` (3.11.2, built on `python@3.14`) and `numpy`.
- The README uses absolute links only, so it renders correctly on PyPI.

Design rules: one pipeline, one version (from `pyproject.toml`), no long-lived PyPI
token, and each channel is a separate job so a failure in one doesn't hide the others.
uv's workflow served only as a reference (Trusted Publishing, a separate publish job,
and a protected environment). Its cargo-dist setup is not copied.

## Target release pipeline (`.github/workflows/release.yml`)

```
plan ─► test (ci.yml) ─► build (sdist+wheel) ─┬─► publish-pypi ──┬─► github-release ─► publish-apt
                                              └─► build-deb ×2 ──┘                  └► publish-homebrew
```

- **PyPI goes before the GitHub release.** PyPI uploads can't be changed afterwards,
  so if the upload fails, no release or tag gets created.
- **Re-running is safe.** `plan` still uses `gh release view v<version>` to decide
  whether to release. The PyPI step uses `skip-existing: true`, so re-running after a
  partial failure doesn't fail on files that are already uploaded.
- Every job has only the permissions it needs. Only `publish-pypi` has `id-token: write`.

---

## Phase 1: PyPI

**Changes**
- `release.yml`: add a `publish-pypi` job (needs `plan` and `build`):
  - `environment: pypi` (the Trusted Publisher is tied to this environment name) and
    `permissions: id-token: write`.
  - Download the `dist` artifact, then run `pypa/gh-action-pypi-publish@release/v1`
    (pinned to a commit SHA) with `skip-existing: true`. This action is used instead
    of `uv publish` because it creates PEP 740 digital attestations automatically,
    which is PyPI's current recommended practice.
  - `release` then gets `needs: [plan, build, publish-pypi, build-deb]`.
- `build` job: add `uvx twine check --strict dist/*` after `uv build`. This catches
  README or metadata problems before they reach PyPI.
- Docs: in `README.md` and `docs/install.md`, add `uv tool install treehawk` /
  `pipx install treehawk` under "Other ways", with `install.sh` still the main option.
  The long release wheel URL is no longer needed.
- `pyproject.toml`: bump the version (for example `uv version --bump minor` → 0.9.0).
  Only a new version triggers the release workflow, and 0.8.0 already has a GitHub release.

**Things you do yourself (before the first merge)**
1. Create a pypi.org account and turn on 2FA (required).
2. Go to pypi.org → *Your projects* → *Publishing* → **Add a pending publisher**
   (GitHub). Enter PyPI project `treehawk`, owner `ibadrather`, repository `treehawk`,
   workflow `release.yml`, environment `pypi`. **No API token is needed.**
3. In GitHub, open repo Settings → Environments and create `pypi`. Set *Deployment
   branches* to `main` only. You can also add yourself as a *Required reviewer*, so each
   upload waits for you to click approve.
4. Optional: repeat step 2 on test.pypi.org if you want to practise a first upload there.

---

## Phase 2: Debian package and signed apt repo

**Packaging approach: a self-contained .deb.** Debian and Ubuntu ship Python packages
that are too old for treehawk (for example trixie has rich 13 and typer 0.15, but
treehawk needs rich ≥15 and typer ≥0.27). So the .deb includes its own Python and
dependencies, and needs nothing from the system except glibc:

```
/opt/treehawk/python/   python-build-standalone 3.13 (installed by uv)
/opt/treehawk/venv/     venv with the treehawk wheel and its dependencies
/usr/bin/treehawk       → symlink to /opt/treehawk/venv/bin/treehawk
/usr/lib/systemd/system/treehawk.service   (installed but not enabled)
```

**New files**
- `packaging/deb/build.sh`: a short POSIX script, run in CI for each architecture:
  1. `UV_PYTHON_INSTALL_DIR=/opt/treehawk/python uv python install 3.13`
  2. `uv venv /opt/treehawk/venv --python <that python>`
  3. `uv pip install --python /opt/treehawk/venv dist/treehawk-*.whl --python-platform <arch>-manylinux_2_28`.
     The `--python-platform` flag makes sure the numpy and matplotlib wheels still run on
     any distro with glibc 2.28 or newer (Debian 10+, Ubuntu 20.04+), even though the
     runner itself is newer.
  4. Remove `__pycache__`, then run `nfpm package` to build the .deb.
  - It is built directly at `/opt/treehawk` on the runner because venvs contain absolute paths.
- `packaging/deb/nfpm.yaml`: package name, version (from an environment variable),
  arch, maintainer, description, homepage, license, and the file mapping above.
  Depends only on `libc6 (>= 2.28)`. nfpm is used instead of hand-written
  `DEBIAN/control` files because it's one short YAML file and gets md5sums, permissions
  and maintainer scripts right.
- `packaging/deb/postinst` / `prerm`: only run `systemctl daemon-reload`. They don't
  enable or start the service, which follows Debian policy for packages that are
  "installed but not configured".
- The packaged `treehawk.service`: a copy of `packaging/treehawk.service` with
  `ExecStart=/usr/bin/treehawk top --dir /var/lib/treehawk`. A unit that
  `treehawk service install` writes to `/etc/systemd/system` still overrides it,
  which is how systemd normally works.

**Workflow changes**
- `build-deb` job: a matrix over `ubuntu-24.04` (amd64) and `ubuntu-24.04-arm` (arm64);
  arm runners are free for public repos. It downloads `dist`, runs
  `packaging/deb/build.sh`, and then smoke-tests the result.
  - For the smoke test, it installs the .deb in plain `debian:bookworm` and
    `ubuntu:22.04` containers and checks that `treehawk --version` matches the version.
    This also shows the glibc range really works.
  - It uploads `treehawk_<ver>_<arch>.deb` as an artifact.
- `release` job: also attaches the two .deb files, which gives
  `sudo apt install ./treehawk_*.deb`.
- `publish-apt` job (after `release`): checks out `ibadrather/apt` with a deploy key,
  adds the new .deb files to `pool/main/t/treehawk/`, and keeps only the newest 3
  versions so GitHub Pages stays well under its 1 GB limit. It then rebuilds the repo
  index with `apt-ftparchive` (`dists/stable/main/binary-{amd64,arm64}/Packages(.gz)`
  and `Release`), signs it into `InRelease` and `Release.gpg` using the imported GPG
  key, then commits and pushes.
  - A separate repo is needed because this repo's GitHub Pages already serves the docs,
    and each repo gets only one Pages site.
- Docs: add a Debian/Ubuntu section to `docs/install.md` using the modern
  `signed-by` keyring and a deb822 source file, without `apt-key`:
  ```bash
  curl -fsSL https://ibadrather.github.io/apt/treehawk.gpg | sudo tee /usr/share/keyrings/treehawk.gpg >/dev/null
  printf 'Types: deb\nURIs: https://ibadrather.github.io/apt\nSuites: stable\nComponents: main\nSigned-By: /usr/share/keyrings/treehawk.gpg\n' \
    | sudo tee /etc/apt/sources.list.d/treehawk.sources
  sudo apt update && sudo apt install treehawk
  ```
  Also add a "service from the package" note to `docs/machine.md`:
  `sudo systemctl enable --now treehawk`.

**Things you do yourself**
1. Create a public repo `ibadrather/apt` with an empty `main` branch. In Settings →
   Pages, choose *Deploy from branch* → `main` / root.
2. Create a GPG key used only for signing (no expiry, or a long one, and not your
   personal key):
   `gpg --quick-gen-key "treehawk apt repository <ibad.rather.ir@gmail.com>" ed25519 sign never`.
   - Export the public key with `gpg --export <KEYID> > treehawk.gpg` and commit it to
     the root of `ibadrather/apt`.
   - Export the private key with `gpg --armor --export-secret-keys <KEYID>` and save it
     as the secret `APT_GPG_PRIVATE_KEY` in the treehawk repo. If you set a passphrase,
     also add `APT_GPG_PASSPHRASE`.
   - Keep an offline backup. If the key is lost, every user has to re-import a new one.
3. Create a deploy key for pushing to the apt repo:
   `ssh-keygen -t ed25519 -f apt_deploy -N ""`.
   - Add `apt_deploy.pub` to `ibadrather/apt` → Settings → Deploy keys, with **write access**.
   - Save the private half as the secret `APT_REPO_DEPLOY_KEY` in treehawk.
   - Deploy keys are used instead of a personal access token because each one only works
     for a single repo and doesn't expire.

---

## Phase 3: Homebrew tap

**Formula approach:** an ordinary Homebrew Python formula, which is what
`brew create --python` produces. The big compiled dependency comes from Homebrew
itself: `depends_on "python-matplotlib"` (which brings numpy and pillow), together
with `depends_on "python@3.14"`, the same Python that formula is built on.
Only the small pure-Python dependencies are listed as `resource` blocks: rich,
markdown-it-py, mdurl, pygments, typer, click, shellingham, typing-extensions.
`pypi_packages exclude_packages: %w[matplotlib numpy pillow ...]` tells
`brew update-python-resources` to skip the ones Homebrew already provides. The
install uses `virtualenv_install_with_resources`, and the `test do` block checks
`treehawk --version` and runs one tiny command.

**New files and repo**
- Formula in a new repo `ibadrather/homebrew-tap` at `Formula/treehawk.rb`. It points
  at the PyPI sdist URL and sha256, so it needs Phase 1 done first.
- `release.yml`: a `publish-homebrew` job (after `publish-pypi` and `release`, on
  `macos-latest`) that:
  1. checks out the tap with a deploy key
  2. gets the sdist sha256 from the PyPI JSON API
  3. updates `url`/`sha256` in the formula
  4. runs `brew update-python-resources Formula/treehawk.rb`
  5. runs `brew install --build-from-source ./Formula/treehawk.rb`, `brew test treehawk`
     and `brew audit --strict treehawk`
  6. commits "treehawk <ver>" and pushes

  It is skipped for pre-releases (versions with `a`, `b`, `rc` or `dev`), so brew users
  only ever get stable versions.
- Docs: add `brew install ibadrather/tap/treehawk` to `README.md` and `docs/install.md`.
  The service part stays Linux-only, so no change is needed there.

**Things you do yourself**
1. Create a public repo `ibadrather/homebrew-tap`; the `homebrew-` prefix is required.
   Give it a short README.
2. Create a deploy key with write access for it: `ssh-keygen -t ed25519 -f tap_deploy -N ""`.
   Add the public half to the tap, and save the private half as the secret
   `HOMEBREW_TAP_DEPLOY_KEY` in treehawk.
3. Later, once the project is well known, you can submit it to homebrew-core. Core
   requires notability (roughly 75+ stars or other evidence), so it isn't part of this plan.

---

## Implementation order (one PR per phase)

1. PR 1, PyPI: `release.yml` (`publish-pypi` job, twine check), docs, version bump.
   Merge it, then check that the release shows up on pypi.org with attestations.
2. PR 2, Debian: `packaging/deb/*`, the `build-deb` and `publish-apt` jobs, docs.
3. PR 3, Homebrew: the tap repo and formula (you create the repo, I write the formula),
   the `publish-homebrew` job, docs.

Also:
- `ci.yml`'s `build` job already smoke-tests the wheel. I'll add `twine check` there
  too, so problems show up in PRs and not only at release time.
- The sdist `include` already lists `packaging`, so the new `packaging/deb/` files ship
  in it without any change.
- `AGENTS.md` rules still apply. There are no `src/` changes, but each PR that changes
  `pyproject.toml` bumps the version.

## Verification

- **Locally, before each PR:** `uv run ruff format`, `uv run ruff check`,
  `make typecheck`, `uv run pytest`, `uv build`, `uvx twine check --strict dist/*`.
- **Locally, for the Debian package:** run `packaging/deb/build.sh` inside
  `docker run --platform linux/amd64 debian:bookworm`, then install the .deb in a clean
  container with `apt install ./…deb`, run `treehawk --version`, and check that
  `systemctl cat treehawk` shows the unit.
- **Workflow files:** check them with `actionlint` before pushing.
- **Before trusting the real pipeline:** do a dry run with a pre-release version (for
  example `0.9.0rc1`). It goes to PyPI as a pre-release, which pip ignores by default,
  and it is skipped for Homebrew.
- **Once for each channel after the release:**
  - in a fresh venv: `uv tool install treehawk && treehawk --version`
  - in a `debian:bookworm` container: add the apt source as documented, then
    `apt install treehawk` and later `apt upgrade`
  - on a Mac: `brew install ibadrather/tap/treehawk && brew test treehawk`
