#!/usr/bin/env python3
"""Invalidate cached chat scripts on every widget entry page, then check them."""
import argparse
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ('mascot.css', 'mascot.js', 'chat-config.js')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    hashes = {name: hashlib.sha256((ROOT/'assets'/name).read_bytes()).hexdigest()[:12] for name in ASSETS}
    pages = 0
    for path in sorted(ROOT.rglob('*.html')):
        relative = path.relative_to(ROOT)
        if any(p.startswith('.') or p in {'tools','node_modules'} for p in relative.parts):
            continue
        html = path.read_text()
        if not re.search(r'src=["\'][^"\']*\bmascot\.js\b', html):
            continue
        stamped = html
        for name, digest in hashes.items():
            pattern = r'((?:src|href)=["\'][^"\']*/' + re.escape(name) + r')(?:\?[^"\']*)?(["\'])'
            stamped, matches = re.subn(pattern, lambda m: m[1]+'?v='+digest+m[2], stamped)
            if matches != 1:
                raise SystemExit('Expected exactly one '+name+' in '+str(relative))
        if stamped != html:
            if not args.write:
                raise SystemExit('Stale chat assets in '+str(relative)+': run with --write')
            path.write_text(stamped)
        pages += 1
    if pages < 2:
        raise SystemExit('Missing homepage or dashboard widget entry')
    print('Chat cache fingerprints verified on',pages,'entry pages')

if __name__ == '__main__':
    main()
