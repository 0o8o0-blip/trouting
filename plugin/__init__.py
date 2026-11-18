"""KiCad loads this: it registers Trouting's action plugin (toolbar button and Tools > External Plugins)."""
try:
    from .trouting_plugin import TroutingPlugin
    TroutingPlugin().register()
except Exception as e:                # show why in KiCad's plugin list instead of failing silently
    import logging
    logging.exception("Trouting plugin failed to load: %s", e)
    raise
