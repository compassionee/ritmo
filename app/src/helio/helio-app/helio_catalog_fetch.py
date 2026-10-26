"""Download of a food catalog package for "Update from URL": the only network code of the food catalog.

Kept apart from helio_catalog.py (search, installation) so that searching cannot reach the network: the
app imports this module only when the user asks for an update from the saved address. HTTPS only, with
the system certificates, a size limit and a timeout; the package is then checked by helio_catalog.install.
"""
import os
import urllib.error
import urllib.request

import helio_catalog as hc

TIMEOUT = 60
MAX_PACKAGE = hc.MAX_XZ + hc.MAX_MANIFEST + 64 * 1024


def download(url, dest, max_bytes=MAX_PACKAGE, timeout=TIMEOUT):
    """Saves the package at url into dest; raises CatalogError ("cat.e.url", "cat.e.download", "cat.e.size")."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise hc.CatalogError("cat.e.url")
    req = urllib.request.Request(url, headers={"User-Agent": "Ritmo-food-catalog/1"})
    total = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as out:
            if not r.geturl().startswith("https://"):
                raise hc.CatalogError("cat.e.url")  # redirected away from HTTPS
            for block in iter(lambda: r.read(1 << 16), b""):
                total += len(block)
                if total > max_bytes:
                    raise hc.CatalogError("cat.e.size")
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
    except hc.CatalogError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise hc.CatalogError("cat.e.download", reason=str(getattr(e, "reason", e))[:120])
    return total
