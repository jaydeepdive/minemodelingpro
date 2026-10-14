#!/usr/bin/env python3
"""Rebuild the model library page and the thumbnails from the model pages already
in site/models/, without re-running the (slow) modelling.

Useful when only the gallery template or the thumbnail drawing has changed:

    PYTHONPATH=src python3 scripts/regen_gallery.py [site]
"""
import os
import re
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from minemodelingpro import model3d as M3           # noqa: E402

RX = re.compile(r'<script id="model"[^>]*>(.*?)</script>', re.S)


def main(site="site"):
    out = os.path.join(site, "models")
    cards = []
    for fn in sorted(os.listdir(out)):
        if not fn.endswith(".html") or fn.startswith("_"):
            continue
        m = RX.search(open(os.path.join(out, fn)).read())
        if not m:
            continue
        try:
            model = json.loads(m.group(1))
            M3.write_thumb(model, os.path.join(out, fn[:-5] + ".svg"))
            cards.append(M3.card_of(model))
        except Exception as e:
            print(f"  skip {fn}: {type(e).__name__}: {str(e)[:90]}")
    M3._write_gallery(cards, os.path.join(site, "models.html"))
    print(f"gallery: {len(cards)} models, {sum(1 for c in cards if c.get('resource'))} with a published resource")


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["site"]))
