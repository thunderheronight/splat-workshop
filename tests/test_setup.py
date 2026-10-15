"""Setup hints (0.4.1, from issues #1 and #2): typos in the config, splat-transform only on the shell's PATH,
an Arcturus pipeline that PyAV 19 breaks."""
import os
import subprocess

import pytest

from conftest import ROOT, server
from splat_workshop import config

UNPINNED = '''#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "av>=18.1",
#   "numpy",
# ]
# ///
import av
av.open("x", metadata_errors="strict")
'''


def test_a_typo_in_the_config_is_named_with_the_right_spelling():
    unknown = config.unknown_keys({"paths": {"splat-transform": "/x", "pipeline": "/p"}, "frmae": {"host": ""}})
    assert unknown == [("paths.splat-transform", "paths.splat_transform"), ("frmae", "frame")]
    c = config.Config(config._merge(config.DEFAULTS, {}), None, unknown)
    keys = [k for k, _ in c.problems()]
    assert keys[:2] == ["setup.unknown_key", "setup.unknown_key"]


def test_a_pipeline_that_pyav_19_breaks_is_found(tmp_path):
    for name in config.PYAV_FILES:
        (tmp_path / name).write_text(UNPINNED)
    assert config.pyav_unpinned(tmp_path) == list(config.PYAV_FILES)
    (tmp_path / "av1_colmap.py").write_text(UNPINNED.replace('"av>=18.1"', '"av>=18.1,<19"'))
    assert config.pyav_unpinned(tmp_path) == ["av1_to_3dgs.py"]
    c = config.Config(config._merge(config.DEFAULTS, {"paths": {"pipeline": str(tmp_path)}}), None)
    hint = dict(c.problems())["setup.pyav"]
    assert hint["files"] == "av1_to_3dgs.py"


def test_a_conversion_failing_on_pyav_says_why():
    out = server.parse_output("Traceback ...\nTypeError: open() got an unexpected keyword argument 'metadata_errors'\n")
    assert out["info"]["hint"] == "hint.pyav"
    assert "hint" not in server.parse_output("Traceback ...\nValueError: something else\n")["info"]


@pytest.mark.parametrize("existing", [False, True])
def test_install_gives_the_full_path_of_splat_transform(tmp_path, existing):
    """install.sh finds splat-transform on the shell's PATH: a new config gets the full path, an existing one
    a line to add – the app, started from the desktop, does not see that PATH."""
    home, xdg, bin_ = tmp_path / "home", tmp_path / "xdg", tmp_path / "bin"
    for d in (home, xdg, bin_):
        d.mkdir()
    (bin_ / "splat-transform").write_text("#!/bin/sh\n")
    (bin_ / "splat-transform").chmod(0o755)
    conf = xdg / "splat-workshop" / "config.toml"
    if existing:
        conf.parent.mkdir()
        conf.write_text('[paths]\nsplat-transform = "/typo"\n')
    env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(xdg), "XDG_DATA_HOME": str(tmp_path / "data"),
           "PATH": f"{bin_}:{os.environ['PATH']}"}
    env.pop("SPLAT_WORKSHOP_CONFIG", None)
    r = subprocess.run([str(ROOT / "install.sh")], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    line = f'splat_transform = "{bin_ / "splat-transform"}"'
    if existing:
        assert line in r.stdout and line not in conf.read_text()
        assert 'Unknown setting "paths.splat-transform"' in r.stdout and '"paths.splat_transform"' in r.stdout
    else:
        assert line in conf.read_text() and "written to the config" in r.stdout
