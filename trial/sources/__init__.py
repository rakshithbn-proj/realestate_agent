"""Non-Apify data sources.

Apify wraps listing portals — commoditised data every investor already sees, and
(per the acres99 parking in config.py) the least reliable link in the chain. The
collectors here talk to official public registries directly: no actor, no API
key, no third-party uptime in the path.

Each collector keeps the same run envelope as trial/scrape.py — start_run,
raw archive before parsing, anomaly guard on volume collapse, finish_run with
cost/duration — so source health is visible on the dashboard from day one.
"""
