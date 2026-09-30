# Copyright (c) 2026, Solrise and contributors

"""The customer portal: the button-first surface a station employee lands on.

The portal is three small pieces and nothing else:

* :mod:`solrise_erp.portal.catalog` - what the buttons are. Data, not markup, so
  adding a button later is a one-line change (docs/17).
* :mod:`solrise_erp.portal.identity` - which station (``Customer``) the logged-in
  user speaks for.
* :mod:`solrise_erp.portal.guard` - the login gate every portal page passes
  through, so a page can never forget it.

The pages live under ``www/start/`` and are rendered by Frappe's website engine,
which is why they inherit the site's branding, the chat widget and the login
session without shipping a JavaScript framework. ``hooks.py`` points the standard
``Customer`` role at ``/start``.
"""
