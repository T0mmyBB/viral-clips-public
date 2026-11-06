"""Collaudo del pannello in un browser vero: carica, zero errori, ogni sezione ha contenuto.

    ..\\publish\\.venv\\Scripts\\python.exe collaudo.py            # il pannello gia' acceso (8787)
    ..\\publish\\.venv\\Scripts\\python.exe collaudo.py --porta 8790

**Perche esiste.** Dopo una modifica a `app.js` o a `stato.py` l'unica verifica che
sembrava disponibile era rileggere il codice, e una pagina vanilla JS si rompe in
silenzio: un'eccezione a meta' di `avvio()` lascia tre schede vuote e nessun messaggio.
Playwright e' gia' installato per la pubblicazione, quindi il collaudo costa una decina
di secondi: la pagina si apre headless, si raccolgono gli errori di console e le
eccezioni, si apre ogni sezione della barra laterale, si contano gli elementi resi e si
salva uno screenshot per sezione in `dump-collaudo/`, piu' uno in tema scuro e uno alla
larghezza di un telefono.

**Clicca solo quello che non scrive.** Le voci della barra, una carta di nicchia, la
testata di una campagna: aprono e chiudono pezzi di pagina e basta. «Aggiorna social» e «radar», «Chiedi»,
«Fai tutto», «scegli», «Segna inviato» fanno partire lavoro vero (`POST`): un collaudo
che li preme non e' un collaudo.

Se il pannello non risponde sulla porta chiesta, ne alza uno temporaneo su una porta
libera col venv del progetto e lo spegne alla fine.

Esce con 1 se c'e' anche un solo errore di console, un'eccezione, una risposta `/api`
sopra 399, o una scheda che resta vuota dove i dati ci sono.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

QUI = Path(__file__).resolve().parent
RADICE = QUI.parent
DUMP = QUI / "dump-collaudo"
sys.path.insert(0, str(RADICE / "publish"))
from interpreti import PYTHON_PROGETTO  # noqa: E402

# Cosa deve comparire in ogni scheda perche' la si possa dire disegnata. Sono
# contenitori che `app.js` riempie da `/api/snapshot`; il minimo e' 1 dove i dati
# ci sono sempre (le nicchie, gli account), 0 dove una lista vuota e' legittima.
SEZIONI = {
    "oggi": [("#oggi-corpo .scheda", 4)],
    "campagne": [(".griglia-nicchie > *", 1), ("#campagne-corpo .scheda", 1)],
    "produzione": [("#produzione-corpo > *", 1)],
    "pubblicazione": [("#pubblicazione-corpo .scheda", 3)],
    "numeri": [
        ("#numeri-corpo .scheda", 3),
        ("#numeri-corpo svg.grafico, #numeri-corpo .vuoto", 1),
    ],
    "chiedi": [("#modulo-chiedi textarea", 1)],
}

# Cosa si apre, in una sezione, prima dello screenshot: solo pezzi di pagina.
APRI = {
    "campagne": ".carta-nicchia.con-account",
    "produzione": ".scheda-campagna .testa",
}

for _flusso in (sys.stdout, sys.stderr):
    try:
        _flusso.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def risponde(porta: int, secondi: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/", timeout=secondi) as r:
            return r.status == 200
    except OSError:
        return False


def porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def alza_temporaneo() -> tuple[subprocess.Popen, int]:
    porta = porta_libera()
    proc = subprocess.Popen(
        [str(PYTHON_PROGETTO), "server.py", "--porta", str(porta), "--no-apri"],
        cwd=str(QUI),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        if risponde(porta, 1.0):
            return proc, porta
        time.sleep(0.5)
    proc.kill()
    raise SystemExit(f"il pannello temporaneo sulla porta {porta} non risponde dopo 20 s")


def collauda(porta: int) -> list[str]:
    from playwright.sync_api import sync_playwright

    problemi: list[str] = []
    DUMP.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("console", lambda m: m.type == "error" and problemi.append(f"console: {m.text}"))
        page.on("pageerror", lambda e: problemi.append(f"eccezione: {e}"))
        page.on(
            "response",
            lambda r: (
                "/api/" in r.url and r.status >= 400 and problemi.append(f"{r.status} su {r.url}")
            ),
        )

        page.goto(f"http://127.0.0.1:{porta}/", wait_until="domcontentloaded")
        try:
            page.wait_for_selector(".griglia-nicchie > *", state="attached", timeout=30000)
        except Exception:
            problemi.append(
                "dopo 30 s le nicchie sono ancora vuote: avvio() non e' arrivato in fondo"
            )
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            problemi.append("dopo 30 s la pagina fa ancora richieste")

        for scheda, attese in SEZIONI.items():
            # Una pagina rotta e' proprio il caso da riportare: un clic che scade
            # diventa un problema elencato, non un traceback che ferma il collaudo
            # prima delle altre sezioni.
            try:
                page.click(f'#navigazione a[data-sezione="{scheda}"]', timeout=5000)
                page.wait_for_timeout(300)
                if not page.locator(f"#sez-{scheda}").is_visible():
                    problemi.append(f"sezione {scheda}: non si mostra")
                if scheda in APRI and page.locator(APRI[scheda]).count():
                    page.locator(APRI[scheda]).first.click(timeout=5000)
                    page.wait_for_timeout(300)
                conti = []
                for selettore, minimo in attese:
                    n = page.locator(selettore).count()
                    conti.append(f"{selettore} {n}")
                    if n < minimo:
                        problemi.append(
                            f"sezione {scheda}: {selettore} ne ha {n}, ne servono almeno {minimo}"
                        )
                page.screenshot(path=str(DUMP / f"{scheda}.png"), full_page=True)
                log(f"  {scheda:<11} {' · '.join(conti)}")
            except Exception as exc:
                problemi.append(
                    f"sezione {scheda}: {type(exc).__name__}: {str(exc).splitlines()[0]}"
                )
        # Il tema scuro e la larghezza di un telefono: due modi di rompere la
        # pagina che il tema chiaro su uno schermo largo non mostra.
        try:
            page.click('#navigazione a[data-sezione="oggi"]', timeout=5000)
            page.evaluate("document.documentElement.dataset.tema = 'scuro'")
            page.wait_for_timeout(200)
            page.screenshot(path=str(DUMP / "oggi-scuro.png"), full_page=True)
            page.evaluate("document.documentElement.dataset.tema = 'chiaro'")
            page.set_viewport_size({"width": 400, "height": 900})
            page.wait_for_timeout(200)
            larga = page.evaluate("document.documentElement.scrollWidth")
            if larga > 420:
                problemi.append(f"a 400 px la pagina scorre di lato: e' larga {larga} px")
            page.screenshot(path=str(DUMP / "oggi-telefono.png"), full_page=True)
        except Exception as exc:
            problemi.append(f"tema scuro o telefono: {type(exc).__name__}: {exc}")
        browser.close()
    return problemi


def main() -> None:
    ap = argparse.ArgumentParser(description="Collaudo del pannello in un browser headless")
    ap.add_argument("--porta", type=int, default=8787)
    args = ap.parse_args()

    proc = None
    porta = args.porta
    if not risponde(porta):
        log(f"sulla porta {porta} non risponde nessuno: alzo un pannello temporaneo")
        proc, porta = alza_temporaneo()
    try:
        log(f"collaudo http://127.0.0.1:{porta}/")
        problemi = collauda(porta)
    finally:
        if proc is not None:
            proc.kill()

    for p in problemi:
        log(f"  PROBLEMA: {p}")
    log(f"screenshot in {DUMP}")
    if problemi:
        raise SystemExit(f"\n{len(problemi)} problemi: il pannello non e' a posto.")
    log("pannello a posto: zero errori, ogni sezione disegnata.")


if __name__ == "__main__":
    main()
