#!/usr/bin/env python3
"""Offline release checks. Never calls a model or reads runtime credentials."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
commands = [[sys.executable, 'tools/chat-ui-check/stamp-chat-assets.py']]
commands += [['node', str(path.relative_to(ROOT))] for path in sorted((ROOT / 'tools/chat-ui-check').glob('*.test.cjs'))]
commands += [['node', str(path.relative_to(ROOT))] for path in sorted((ROOT / 'tools/operations-forecast').glob('test_*.cjs'))]
commands += [[sys.executable, '-m', 'unittest', 'discover', '-s', folder] for folder in (
    'tools/operations-chat/tests', 'tools/operations-chat-cloud/tests')]
for command in commands:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        print((result.stdout + result.stderr)[-8000:], file=sys.stderr)
        raise SystemExit(result.returncode)
    print('PASS ' + ' '.join(command[1:]), flush=True)
print('Chat release checks passed; real public reply must still be verified.')
