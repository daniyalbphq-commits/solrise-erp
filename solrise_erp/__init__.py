# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""The Solrise application layer.

`pyproject.toml` declares the version dynamically, so the flit build backend
reads `__version__` from here - without it `bench get-app`'s editable install
fails during metadata generation and the image bake dies before the app is even
fetched. `bench version` and the Desk's About dialog read the same attribute.
"""

__version__ = "1.0.0"
