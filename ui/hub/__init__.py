"""The OpenFlow main window ("hub"): sidebar + pages (spec 2026-10-01-app-hub-design.md).

Layout and copy come from the approved mockups (export:
~/Downloads/OpenFlow App Screens.pdf). Every page is a `Page` subclass in
ui/hub/pages/<name>.py, built against `HubContext` and the tokens in style.py.
"""
