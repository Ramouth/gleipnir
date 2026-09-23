import re
import subprocess
import sys
from pathlib import Path

GL = Path(__file__).resolve().parents[1] / 'scripts' / 'gl.py'


def test_every_flag_a_command_takes_is_defined_on_the_parser():
    """A flag named in USES but never added to the parser crashes the command
    (2e7bc8a shipped `fetch` broken this way: 'Namespace' has no attribute 'fresh')."""
    src = GL.read_text()
    uses = src[src.index('USES = {'):src.index('DEFAULTS = {')]
    flags = set(re.findall(r"'([a-z_]+)'", uses.split('{', 1)[1])) - {
        c for c in re.findall(r"'([a-z_]+)':", uses)}
    defined = set(re.findall(r"add_argument\('--([a-z-]+)'", src))
    missing = {f for f in flags if f.replace('_', '-') not in defined}
    assert not missing, f'flags in USES with no add_argument: {missing}'


def test_fetch_parses_its_flags(tmp_path):
    out = subprocess.run([sys.executable, str(GL), 'fetch', str(tmp_path / 'ws'), 'not-a-url', '--fresh'],
                         capture_output=True, text=True, env={'PYTHONPATH': str(GL.parents[1] / 'src')})
    assert 'AttributeError' not in out.stderr and 'Traceback' not in out.stderr
