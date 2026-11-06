#!/usr/bin/env python
"""
ritmo.py — quando un account puo' pubblicare, e perche' no quando non puo'.

Perche' esiste un file solo per questo
--------------------------------------
La regola e' misurata sul campo e costa cara sbagliarla: @canale_lab ha fatto
218 views col primo video, poi sei post in nove minuti, e da li' 0-6 views per
settimane. Da qui il tetto giornaliero, la distanza minima fra due post e la
rampa per gli account nuovi.

Era scritta in `publish_agent.py`, dentro il comando che pubblicava all'epoca in
cui fu scritta. Poi e' nato l'orchestratore multi-piattaforma, che chiama
direttamente la funzione di caricamento e quindi non passava di li': le clip 01
e 02 sono uscite a **sette minuti** l'una dall'altra su un canale nuovo. Nessun
errore, perche' la pubblicazione riesce lo stesso; il danno arriva settimane
dopo e non e' attribuibile a niente.

Percio' la regola sta qui, in un modulo di sola libreria standard, e tutti e tre
i lettori la importano: l'agente TikTok, l'orchestratore e la dashboard. Se un
domani nasce un quarto punto d'ingresso, il modo giusto e' gia' l'unico
disponibile.

Il conto e' **per account**, non per piattaforma
------------------------------------------------
Due account TikTok non si sommano fra loro: ognuno ha il suo tetto e la sua
rampa, perche' e' l'algoritmo di quell'account a decidere. Finche' gli account
erano uno per piattaforma le due cose coincidevano per caso, ed e' esattamente
il tipo di coincidenza che smette di valere senza avvisare.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# La rampa di un account nuovo: prima si esiste, poi si pubblica piano. Le
# soglie sono giorni dall'apertura, la quota e' quanti post al giorno.
RAMPA = (
    (7, 0, "rampa: settimana di sola presenza"),
    (14, 1, "rampa: seconda settimana"),
    (21, 2, "rampa: terza settimana"),
    (28, 3, "rampa: quarta settimana"),
)


def tetto_giornaliero(massimo: int, aperto_il=None, oggi: date | None = None) -> tuple[int, str]:
    """
    Quanti post ammette oggi questo account, e con quale motivazione.

    `aperto_il` e' la data di apertura dell'account: senza, si assume maturo —
    che e' l'assunzione ottimista, quindi va scritta nel registro delle identita'
    ogni volta che si sa.
    """
    massimo = int(massimo or 0)
    if not aperto_il:
        return massimo, "account maturo"
    oggi = oggi or datetime.now().date()
    try:
        giorni = (oggi - date.fromisoformat(str(aperto_il))).days
    except ValueError:
        return massimo, "data di apertura non valida, uso il tetto pieno"
    for soglia, quota, etichetta in RAMPA:
        if giorni < soglia:
            return min(quota, massimo) if massimo else quota, f"{etichetta}, giorno {giorni}"
    return massimo, f"account maturo, giorno {giorni}"


def quando_si_puo(
    pubblicazioni,
    *,
    distanza_minima: int = 0,
    massimo: int = 0,
    aperto_il=None,
    adesso: datetime | None = None,
) -> dict:
    """
    Il verdetto sul ritmo di **un** account.

    `pubblicazioni` sono i momenti in cui quell'account ha gia' pubblicato:
    datetime, o stringhe ISO, in qualunque ordine. Torna sempre un dizionario,
    anche quando si puo' pubblicare, perche' la dashboard deve poter dire
    *perche'* si puo' — «tetto 3, oggi 1» e' un'informazione, «ok» no.
    """
    adesso = adesso or datetime.now()
    momenti = []
    for p in pubblicazioni or []:
        if isinstance(p, datetime):
            momenti.append(p)
            continue
        try:
            momenti.append(datetime.fromisoformat(str(p)))
        except (TypeError, ValueError):
            continue

    quota, motivo_tetto = tetto_giornaliero(massimo, aperto_il, adesso.date())
    oggi_n = sum(1 for m in momenti if m.date() == adesso.date())
    ultimo = max(momenti) if momenti else None

    esito = {
        "puo": True,
        "motivo": f"{motivo_tetto} — oggi {oggi_n}/{quota}" if quota else motivo_tetto,
        "quando": None,
        "oggi": oggi_n,
        "tetto": quota,
        "tetto_motivo": motivo_tetto,
        "ultimo": ultimo.isoformat(timespec="seconds") if ultimo else None,
    }

    if quota <= 0:
        esito.update(puo=False, motivo=motivo_tetto)
        return esito
    if oggi_n >= quota:
        esito.update(
            puo=False, motivo=f"tetto giornaliero raggiunto: {oggi_n}/{quota} ({motivo_tetto})"
        )
        return esito

    if distanza_minima and ultimo:
        passati = (adesso - ultimo).total_seconds()
        if passati < distanza_minima:
            manca = timedelta(seconds=int(distanza_minima - passati))
            esito.update(
                puo=False,
                quando=(adesso + manca).isoformat(timespec="seconds"),
                motivo=(
                    f"troppo presto: dall'ultimo post sono passati "
                    f"{timedelta(seconds=int(passati))}, ne servono "
                    f"{timedelta(seconds=int(distanza_minima))} "
                    f"(alle {(adesso + manca):%H:%M})"
                ),
            )
    return esito
