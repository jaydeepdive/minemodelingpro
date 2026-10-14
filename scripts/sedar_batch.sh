#!/bin/bash
# RETIRED 2026-10-14 — the SEDAR+ direct scraper no longer runs.
#
# It drove a real Chrome session on a Mac to pull NI 43-101 PDFs straight from
# SEDAR+. SEDAR+ changed its search form in July 2026, after which every run
# reported "filter type: NOT FOUND … collected 0 new report(s)", and it was the
# last piece of MineModelingPro that depended on a particular machine being awake.
#
# New technical reports now come from the ceo.ca public feed instead, collected
# hourly in GitHub Actions (.github/workflows/ceo-sedar.yml -> ceo_sedar.py) and
# extracted daily by mmp-43101-reports. Nothing here needs a local machine.
#
# This stub stays so that any launchd job still pointing at it exits quietly.
# To remove that job from the Mac entirely:
#     launchctl unload ~/Library/LaunchAgents/com.thedeepdive.sedar.plist
#     rm ~/Library/LaunchAgents/com.thedeepdive.sedar.plist
echo "$(date -u +%FT%TZ) sedar_batch.sh is retired — see the ceo.ca collector (hourly, in CI)."
exit 0
