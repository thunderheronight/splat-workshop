"""The page itself (web/index.html): what its script touches the moment it loads must already be there.
In 0.5.1 the footer stood below the script; setting up its link failed, and the rest of the script –
among it the regular status updates – never ran: a finished job stayed "running" until a reload."""
import re

from conftest import ROOT

HTML = (ROOT / "web" / "index.html").read_text()


def test_everything_the_script_sets_up_on_load_is_above_it():
    script = HTML.index("<script>")
    above = set(re.findall(r'id="([\w-]+)"', HTML[:script]))
    code = HTML[script:]
    on_load = set(re.findall(r'^\$\("#([\w-]+)"\)\.', code, re.M))          # statements at the top level
    missing = sorted(on_load - above)
    assert not missing, f"used when the page loads, but not (yet) in the page: {missing}"


def test_the_status_is_polled():
    assert re.search(r"^setInterval\(poll, \d+\);", HTML, re.M)
