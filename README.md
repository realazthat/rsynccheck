<!--

WARNING: This file is auto-generated by snipinator. Do not edit directly.
SOURCE: `README.md.jinja2`.

-->
<!--







-->

# <div align="center">[![RSyncCheck][1]][2]</div>

<div align="center">

<!-- Icons from https://lucide.dev/icons/users -->
<!-- Icons from https://lucide.dev/icons/laptop-minimal -->

![**Audience:** Developers][3] ![**Platform:** Linux][4]

</div>

<p align="center">
  <strong>
    <a href="https://github.com/realazthat/rsynccheck">🏠Home</a>
    &nbsp;&bull;&nbsp;
    <a href="#-features">🎇Features</a>
    &nbsp;&bull;&nbsp;
    <a href="#-installation">🔨Installation</a>
    &nbsp;&bull;&nbsp;
    <a href="#-usage">🚜Usage</a>
    &nbsp;&bull;&nbsp;
    <a href="#-command-line-options">💻CLI</a>
    &nbsp;&bull;&nbsp;
    <a href="#-examples">💡Examples</a>
  </strong>
</p>
<p align="center">
  <strong>
    <a href="#-jinja2-api">🤖Jinja2 API</a>
    &nbsp;&bull;&nbsp;
    <a href="#-requirements">✅Requirements</a>
    &nbsp;&bull;&nbsp;
    <a href="#-docker-image">🐳Docker</a>
    &nbsp;&bull;&nbsp;
    <a href="#-gotchas-and-limitations">🚸Gotchas</a>
  </strong>
</p>

<div align="center">

![Top language][5] [![GitHub License][6]][7] [![PyPI - Version][8]][9]
[![Python Version][10]][9]

**CLI to embed (testable) snippets from your codebase into your README**

</div>

---

<div align="center">

|                   | Status                      | Stable                    | Unstable                  |                          |
| ----------------- | --------------------------- | ------------------------- | ------------------------- | ------------------------ |
| **[Master][11]**  | [![Build and Test][12]][13] | [![since tagged][14]][15] |                           | [![last commit][16]][17] |
| **[Develop][18]** | [![Build and Test][19]][13] | [![since tagged][20]][21] | [![since tagged][22]][23] | [![last commit][24]][25] |

</div>

<img src="./.github/demo.gif" alt="Demo" width="100%">

## ❔ What

Check the progress of a long running rsync operation, by hashing chunks of the
files.

## 🎇 Features

- Can use any {md5sum/xxhash}-compatible CLI to hash the files.
- 🐳🌊🖥️ Docker Image (See [README: Docker Image](#-docker-image)).

## 🔨 Installation

```bash
# Install from pypi (https://pypi.org/project/rsynccheck/)
pip install rsynccheck

# Install from git (https://github.com/realazthat/rsynccheck)
pip install git+https://github.com/realazthat/rsynccheck.git@v0.1.0
```

## 🚜 Usage

Example:

<!---->
```bash

# Generate the audit.yaml file.
python -m rsynccheck.cli \
  hash \
  --ignorefile ".gitignore" \
  --ignoreline .trunk --ignoreline .git \
  --audit-file ".deleteme/check-changes-audit.yaml" \
  --progress none \
  --chunk-size "${CHUNK_SIZE}" \
  --directory "${SRC_DIRECTORY}"

# Check the audit.yaml file on the other machine.
python -m rsynccheck.cli \
  audit \
  --audit-file ".deleteme/check-changes-audit.yaml" \
  --progress none \
  --output-format table \
  --mismatch-exit 0 \
  --directory "${DST_DIRECTORY}"
```
<!---->

Screenshot in terminal:

<!---->
<img src="README.example.generated.svg" alt="Output of `./snipinator/examples/example_example.sh`" />
<!---->

## 💻 Command Line Options

<!---->
<img src="README.help.generated.svg" alt="Output of `python -m rsynccheck.cli --help`" />
<!---->

<!---->
<img src="README.hash.help.generated.svg" alt="Output of `python -m rsynccheck.cli hash --help`" />
<!---->

<!---->
<img src="README.audit.help.generated.svg" alt="Output of `python -m rsynccheck.cli audit --help`" />
<!---->

## ✅ Requirements

- Linux-like environment
  - Why: Uses pexpect.spawn().
- Python 3.8+
  - Why: Some dev dependencies require Python 3.8+.

### Tested Platforms

- WSL2 Ubuntu 20.04, Python `3.8.0`.
- Ubuntu 20.04, Python `3.8.0, 3.9.0, 3.10.0, 3.11.0, 3.12.0`, tested in GitHub Actions
  workflow ([build-and-test.yml](./.github/workflows/build-and-test.yml)).

## 🐳 Docker Image

Docker images are published to [ghcr.io/realazthat/rsynccheck][26] at each
tag.

**NOTE: You can't use a custom hashing command with the Docker image, because it
isn't available inside the container. xxhash is installed in the image.**

<!---->
```bash

# Use the published images at ghcr.io/realazthat/rsynccheck.
# Generate the audit.yaml file.
# /data in the docker image is the working directory, so paths are simpler.
docker run --rm --tty \
  -v "${PWD}:/data" \
  ghcr.io/realazthat/rsynccheck:v0.1.0 \
  hash \
  --ignorefile ".gitignore" \
  --ignoreline .trunk --ignoreline .git \
  --audit-file ".deleteme/check-changes-audit.yaml" \
  --progress none \
  --chunk-size "${CHUNK_SIZE}" \
  --directory "${SRC_DIRECTORY}"

# Check the audit.yaml file on the other machine.
docker run --rm --tty \
  -v "${PWD}:/data" \
  ghcr.io/realazthat/rsynccheck:v0.1.0 \
  audit \
  --audit-file ".deleteme/check-changes-audit.yaml" \
  --progress none \
  --output-format table \
  --mismatch-exit 0 \
  --directory "${DST_DIRECTORY}"
```
<!---->

If you want to build the image yourself, you can use the Dockerfile in the
repository.

<!---->
```bash

docker build -t my-rsynccheck-image .

# Generate the audit.yaml file.
# /data in the docker image is the working directory, so paths are simpler.
docker run --rm --tty \
  -v "${PWD}:/data" \
  my-rsynccheck-image \
  hash \
  --ignorefile ".gitignore" \
  --ignoreline .trunk --ignoreline .git \
  --audit-file ".deleteme/check-changes-audit.yaml" \
  --progress none \
  --chunk-size "${CHUNK_SIZE}" \
  --directory "${SRC_DIRECTORY}"

# Check the audit.yaml file on the other machine.
docker run --rm --tty \
  -v "${PWD}:/data" \
  my-rsynccheck-image \
  audit \
  --audit-file ".deleteme/check-changes-audit.yaml" \
  --progress none \
  --output-format table \
  --mismatch-exit 0 \
  --directory "${DST_DIRECTORY}"
```
<!---->

## 🤏 Versioning

We use SemVer for versioning. For the versions available, see the tags on this
repository.

## 🔑 License

This project is licensed under the MIT License - see the
[./LICENSE.md](./LICENSE.md) file for details.

## 🙏 Thanks

- Colorful CLI help:
  [hamdanal/rich-argparse](https://github.com/hamdanal/rich-argparse).
- pathspec, for gitignore support:
  [cpburnz/python-pathspec](https://github.com/cpburnz/python-pathspec).
- humanize: [jmoiron/humanize](https://github.com/jmoiron/humanize).
- xxhash: [Cyan4973/xxHash](https://github.com/Cyan4973/xxHash).
- pydantic: [pydantic/pydantic](https://github.com/pydantic/pydantic).

## 🤝 Related Projects

Not complete, and not necessarily up to date. Make a PR
([contributions](#-contributions)) to insert/modify.

| Project                  | Stars | Last Update  | Language | Platform | Similarity X Obviousness |
| ------------------------ | ----- | ------------ | -------- | -------- | ------------------------ |
| [RsyncProject/rsync][27] | 2.4k  | `2024/05/28` | C        | CLI      | ⭐⭐⭐                   |

## 🫡 Contributions

### Development environment: Linux-like

- For running `pre.sh` (Linux-like environment).

  - From [./.github/dependencies.yml](./.github/dependencies.yml), which is used for
    the GH Action to do a fresh install of everything:

    ```yaml
    bash: scripts.
    findutils: scripts.
    grep: tests.
    xxd: tests.
    git: scripts, tests.
    xxhash: scripts (changeguard).
    rsync: out-of-directory test.
    expect: for `unbuffer`, useful to grab and compare ansi color symbols.
    jq: dependency for [yq](https://github.com/kislyuk/yq), which is used to generate
      the README; the README generator needs to use `tomlq` (which is a part of `yq`)
      to query `pyproject.toml`.
    unzip: scripts (pyenv).
    curl: scripts (pyenv).
    git-core: scripts (pyenv).
    gcc: scripts (pyenv).
    make: scripts (pyenv).
    zlib1g-dev: scripts (pyenv).
    libbz2-dev: scripts (pyenv).
    libreadline-dev: scripts (pyenv).
    libsqlite3-dev: scripts (pyenv).
    libssl-dev: scripts (pyenv).
    libffi-dev: bdist_wheel (otherwise `pip install .` fails). If installing pyenv, this
      must be installed _first_.
    
    ```

  - Requires `pyenv`, or an exact matching version of python as in
    [./.python-version](./.python-version) (which is currently
    `3.8.0`).
  - act (to run the GH Action locally):
    - Requires nodejs.
    - Requires Go.
    - docker.
  - Generate animation:
    - docker
  - docker (for building the docker image).

### Commit Process

1. (Optionally) Fork the `develop` branch.
2. Stage your files: `git add path/to/file.py`.
3. `bash ./scripts/pre.sh`, this will format, lint, and test the code.
4. `git status` check if anything changed (generated
   [./README.md](./README.md) for example), if so, `git add` the
   changes, and go back to the previous step.
5. `git commit -m "..."`.
6. Make a PR to `develop` (or push to develop if you have the rights).

## 🔄🚀 Release Process

These instructions are for maintainers of the project.

1. In the `develop` branch, run `bash ./scripts/pre.sh` to ensure
   everything is in order.
2. In the `develop` branch, bump the version in
   [./pyproject.toml](./pyproject.toml), following semantic versioning
   principles. Also modify the `last_release` and `last_stable_release` in the
   `[tool.rsynccheck-project-metadata]` table as appropriate. Run
   `bash ./scripts/pre.sh` to ensure everything is in order.
3. In the `develop` branch, commit these changes with a message like
   `"Prepare release X.Y.Z"`. (See the contributions section
   [above](#commit-process)).
4. Merge the `develop` branch into the `master` branch:
   `git checkout master && git merge develop --no-ff`.
5. `master` branch: Tag the release: Create a git tag for the release with
   `git tag -a vX.Y.Z -m "Version X.Y.Z"`.
6. Publish to PyPI: Publish the release to PyPI with
   `bash ./scripts/deploy-to-pypi.sh`.
7. Push to GitHub: Push the commit and tags to GitHub with
   `git push && git push --tags`.
8. The `--no-ff` option adds a commit to the master branch for the merge, so
   refork the develop branch from the master branch:
   `git checkout develop && git merge master`.
9. Push the develop branch to GitHub: `git push origin develop`.

[1]: ./.github/logo-exported.svg
[2]: https://github.com/realazthat/rsynccheck
[3]:
  https://img.shields.io/badge/Audience-Developers-0A1E1E?style=plastic&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiIGNsYXNzPSJsdWNpZGUgbHVjaWRlLXVzZXJzIj48cGF0aCBkPSJNMTYgMjF2LTJhNCA0IDAgMCAwLTQtNEg2YTQgNCAwIDAgMC00IDR2MiIvPjxjaXJjbGUgY3g9IjkiIGN5PSI3IiByPSI0Ii8+PHBhdGggZD0iTTIyIDIxdi0yYTQgNCAwIDAgMC0zLTMuODciLz48cGF0aCBkPSJNMTYgMy4xM2E0IDQgMCAwIDEgMCA3Ljc1Ii8+PC9zdmc+
[4]:
  https://img.shields.io/badge/Platform-Linux-0A1E1E?style=plastic&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiIGNsYXNzPSJsdWNpZGUgbHVjaWRlLWxhcHRvcC1taW5pbWFsIj48cmVjdCB3aWR0aD0iMTgiIGhlaWdodD0iMTIiIHg9IjMiIHk9IjQiIHJ4PSIyIiByeT0iMiIvPjxsaW5lIHgxPSIyIiB4Mj0iMjIiIHkxPSIyMCIgeTI9IjIwIi8+PC9zdmc+
[5]:
  https://img.shields.io/github/languages/top/realazthat/rsynccheck.svg?cacheSeconds=28800&style=plastic&color=0A1E1E
[6]:
  https://img.shields.io/github/license/realazthat/rsynccheck?style=plastic&color=0A1E1E
[7]: ./LICENSE.md
[8]:
  https://img.shields.io/pypi/v/rsynccheck?style=plastic&color=0A1E1E
[9]: https://pypi.org/project/rsynccheck/
[10]:
  https://img.shields.io/pypi/pyversions/rsynccheck?style=plastic&color=0A1E1E
[11]: https://github.com/realazthat/rsynccheck/tree/master
[12]:
  https://img.shields.io/github/actions/workflow/status/realazthat/rsynccheck/build-and-test.yml?branch=master&style=plastic
[13]:
  https://github.com/realazthat/rsynccheck/actions/workflows/build-and-test.yml
[14]:
  https://img.shields.io/github/commits-since/realazthat/rsynccheck/v0.1.0/master?style=plastic
[15]:
  https://github.com/realazthat/rsynccheck/compare/v0.1.0...master
[16]:
  https://img.shields.io/github/last-commit/realazthat/rsynccheck/master?style=plastic
[17]: https://github.com/realazthat/rsynccheck/commits/master
[18]: https://github.com/realazthat/rsynccheck/tree/develop
[19]:
  https://img.shields.io/github/actions/workflow/status/realazthat/rsynccheck/build-and-test.yml?branch=develop&style=plastic
[20]:
  https://img.shields.io/github/commits-since/realazthat/rsynccheck/v0.1.0/develop?style=plastic
[21]:
  https://github.com/realazthat/rsynccheck/compare/v0.1.0...develop
[22]:
  https://img.shields.io/github/commits-since/realazthat/rsynccheck/v0.1.0/develop?style=plastic
[23]:
  https://github.com/realazthat/rsynccheck/compare/v0.1.0...develop
[24]:
  https://img.shields.io/github/last-commit/realazthat/rsynccheck/develop?style=plastic
[25]: https://github.com/realazthat/rsynccheck/commits/develop
[26]: https://ghcr.io/realazthat/rsynccheck
[27]:
  https://github.com/RsyncProject/rsync
  "Not easy to extract a completeness fraction"
