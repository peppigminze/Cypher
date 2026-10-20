"""
Windows PC control building blocks (FIX2 point 3).

The tools in app/tools/*_tools.py stay thin; the OS work lives here so it
can be tested (and replaced by dummies) on its own:

    powershell.py   run PowerShell commands, background jobs, kill the tree
    app_index.py    Start menu index: app name -> shortcut / AppUserModelID
    windows.py      list/focus/minimize/close top-level windows (Win32)
    sendinput.py    keyboard and mouse via SendInput (fragile, last resort)
    elevation.py    is Cypher elevated? did a command need admin rights?

Risk is never decided here - that is app/security/classifier.py.
"""
