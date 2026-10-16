# Notes for working on Splat Workshop

## Releasing – every release, also a patch

1. Only with the maintainer's explicit OK for each step: push, tag, GitHub release, editing release notes.
2. `VERSION` in `splat_workshop/server.py`, a `## [x.y.z] – date` section and the compare link in `CHANGELOG.md`.
3. Tests green locally and in GitHub Actions before tagging.
4. **Release notes always have this shape** (a patch release too – people who only read the latest
   release must still see what the series brought):
   - one bold line: what the series is about, plus what this release adds;
   - the screenshot (`docs/groups.png`, example data only);
   - `## Highlights of x.y` – the highlights of the whole minor series, copied from the `x.y.0` release;
   - a link to the `x.y.0` release for all details;
   - `## New in x.y.z` – what this release adds or fixes (for `x.y.0` itself: `## New in x.y`);
   - `**Full changelog:**` compare link to the previous tag.
   Check the finished notes against this list before publishing.

## Tests

`uv run --no-project --with pytest --with numpy --with scipy --with pillow pytest`

Expectations come from how the synthetic data is built (`tests/synth.py`), not from the code. Tests must
never touch real data, the Frame or this PC's power state (`systemctl`, `ssh`, `notify-send` are faked).

## Running scripts

`splat.sh`, `to-frame.sh`, `combine.sh`, `versions.sh` run as one `{ … exit; }` block; still, never edit
one in place while a job uses it – write a new file and `mv` it over.

## File systems

Runs may live on NTFS: no folder or file name may end with a dot or contain `:`; git there needs
`core.fileMode false`. With that, git does not see the executable bit: a new shell script needs
`git update-index --chmod=+x scripts/<name>.sh`, or it is not executable in CI and fresh clones.
