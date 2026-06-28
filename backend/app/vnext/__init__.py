"""SViam Interview vNext — isolated live-backend foundation (Phases E1+E2).

Everything under ``app.vnext`` is additive and self-contained: it must not be
imported by, nor mutate, any existing route/service. The only shared touch is a
single ``app.include_router`` line in ``app/main.py``.
"""
