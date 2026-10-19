"""Standalone site theme for MineModelingPro.

Provides the interface model3d.py's gallery expects: THEME_CSS, FONTS,
header(active), footer(). Same Deep Dive visual language as the Closeology
site, but the nav points only at MMP pages so the site stands alone.
"""

THEME_CSS = """
:root{ --red:#D71920; --ink:#111418; --mut:#636363; --line:#e6e8eb; --panel:#f5f7fa; --bg:#ffffff; --chip:#EDF2F7; }
*{box-sizing:border-box;}
html,body{margin:0;background:var(--bg);color:var(--ink);font-family:'Roboto',-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:15px;line-height:1.55;}
h1,h2,h3,h4{font-family:'Bitter',Georgia,serif;font-weight:700;color:var(--ink);margin:0;}
a{color:var(--red);text-decoration:none;} a:hover{text-decoration:underline;}
.topbar{border-bottom:1px solid var(--line);background:#fff;position:sticky;top:0;z-index:50;}
.topwrap{max-width:1180px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;padding:12px 22px;gap:20px;flex-wrap:wrap;}
.brand{display:flex;flex-direction:column;gap:3px;text-decoration:none;}
.brand .name{font-family:'Bitter',serif;font-weight:800;font-size:20px;color:var(--ink);letter-spacing:-.2px;}
.brand .name b{color:var(--red);}
.brand .sub{font-family:'Bitter',serif;font-weight:700;font-size:10.5px;letter-spacing:2.5px;color:var(--mut);text-transform:uppercase;}
nav.menu{display:flex;align-items:center;gap:22px;flex-wrap:wrap;}
nav.menu a{display:inline-flex;align-items:center;height:32px;color:var(--ink);font-size:14px;font-weight:500;line-height:1;border-bottom:2px solid transparent;text-decoration:none;}
nav.menu a:hover{color:var(--red);text-decoration:none;}
nav.menu a.active{color:var(--red);border-bottom-color:var(--red);}
nav.menu a.signin{background:var(--red);color:#fff;border-radius:7px;padding:0 14px;font-weight:700;}
nav.menu a.signin:hover{background:#b3141a;color:#fff;}
nav.menu a.ext{color:var(--mut);font-size:13px;}
.wrap{max-width:1180px;margin:0 auto;padding:26px 22px 60px;}
.hero h1{font-size:30px;letter-spacing:-.3px;} .hero p{color:var(--mut);max-width:760px;}
.rule{height:3px;width:52px;background:var(--red);margin:10px 0 0;border-radius:2px;}
footer.site{border-top:1px solid var(--line);color:var(--mut);font-size:12px;line-height:1.6;padding:22px;max-width:1180px;margin:0 auto;}
footer.site b{color:var(--ink);}
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Bitter:wght@500;700;800&'
         'family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">')

def header(active="models"):
    """Site chrome. The menu bar itself lives in site/nav.js so that the static
    pages, the gallery and the Studio all render one definition; this only says
    which link is lit and where the site root is from here."""
    a = str(active or "").replace(".html", "")
    if a == "app":
        a = "dashboard"
    return ('<div class="topbar" id="topbar"></div>'
            '<script src="nav.js"></script>'
            f'<script>MMPNav.render({{base:"",active:"{a}"}});</script>')


def footer():
    return (
        '<footer class="site">'
        '<b>MineModelingPro</b> — interactive 3D deposit models built from drill-hole '
        'assays and NI 43-101 technical reports. Grades are inverse-distance estimates '
        'for visualization only, not a mineral resource estimate. Verify every figure '
        'against the source filing before relying on it.'
        '</footer>')
