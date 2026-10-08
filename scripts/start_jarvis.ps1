# Kept for the old "JARVIS Autostart" task, which still points here:
# it now simply starts Cypher the new way (start_cypher.ps1, no browser).
# `python scripts/autostart.py install` replaces that task with "Cypher Autostart".
& (Join-Path $PSScriptRoot 'start_cypher.ps1') -NoBrowser
exit $LASTEXITCODE
