#!/bin/bash
# Double-click launcher for the Omnia Desktop Clipper.
# Runs the app from source under Terminal, which keeps its Accessibility + Input Monitoring
# grants across code changes — unlike the unsigned .app, whose TCC grants reset every rebuild.
cd "$(dirname "$0")" || exit 1
exec ./.venv-build/bin/python -m omnia_desktop_clipper
