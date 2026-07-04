"""Composition artifacts for productized runs (bridge-local, spike S-lane).

Modules here turn the *documented* composition artifacts (site profile bundle, run
manifest, effective-composition record — docs/productization/04:83-136 / 09:42-181)
into typed, verifiable objects. Each S-lane adds its own module; this ``__init__``
intentionally re-exports NOTHING so parallel lanes do not conflict on this file
(import submodules directly, e.g. ``from ...composition.profile import load_site_profile``).
"""
