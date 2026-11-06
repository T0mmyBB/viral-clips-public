"""L'indirizzo della radice del pannello Whop, da cui l'applicazione si apre davvero.

Sta qui e non in `whop_agent.py` perche' quello si ferma all'importazione se manca
Scrapling, che vive nel venv condiviso dello scraping: `prepara.py` gira nel `.venv` del
progetto e deve poter calcolare la stessa radice senza il browser.
"""

from __future__ import annotations

import re


def radice_app(url: str) -> str:
    """L'indirizzo della **radice** del pannello, da cui l'applicazione si apre davvero.

    Il collegamento profondo alla singola campagna non si puo' usare: su
    Clipping Culture `whop.com/experiences/<exp>/campaigns/<uuid>` rimbalza
    sulla home del whop, e chi ci arriva trova un riquadro vuoto — nessun
    collegamento da cliccare, e l'errore che ne esce parla di un clic scaduto
    invece che della pagina sbagliata. La radice `/<slug>/<exp>/app/` invece
    carica, ed e' da li' che si naviga.
    """
    m = re.match(r"(https://whop\.com/[^/]+/exp_[^/]+/app)(?:/|$)", url)
    if m:
        return m.group(1) + "/"
    m = re.match(r"(https://whop\.com/experiences/exp_[^/]+)(?:/|$)", url)
    if m:
        return m.group(1) + "/app/"
    return url
