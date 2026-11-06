"""Genera i sottotitoli ASS animati per una clip.

    python make_subs.py work/<slug> --start 12.0 --end 48.0 --out subs.ass
    python make_subs.py work/<slug> --start 12 --end 48 --keywords "gratis,mai,soldi"

Perche ASS e non SRT: l'SRT non ha ne stile ne animazione. Qui servono
evidenziazione della parola corrente, keyword ingrandite e numeri colorati, che
sono tutti tag di override ASS.

Come funziona l'evidenziazione: **non** si usa il karaoke `\\k` nativo. Quello
colora progressivamente tutte le parole gia pronunciate, mentre l'effetto che
funziona sui social e "una sola parola accesa alla volta".

**Ogni parola e un evento a se, con la sua posizione.** Prima la riga era un
evento unico e la parola accesa si ingrandiva al 112% dentro la riga: la riga
si allargava, e siccome e centrata **tutte le altre parole scivolavano** di una
ventina di pixel a ogni cambio di parola. Adesso le larghezze si misurano dal
font (tabelle `hmtx` e `cmap` del TTF, libreria standard), ogni parola ha il suo
`\\pos`, e lo scatto di scala avviene attorno al suo centro senza toccare le
vicine. Per ogni parola al massimo tre eventi — prima, mentre, dopo che e detta:
su una clip da 45 secondi qualche centinaio di righe, per libass niente.

Le coordinate sono in spazio 1080x1920: i sottotitoli stanno a circa il 67%
dell'altezza, sopra la UI di TikTok e Reels che mangia il fondo schermo.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from functools import lru_cache
from pathlib import Path

from common import FONTS_DIR, emit, log, read_json

PLAY_W, PLAY_H = 1080, 1920

# I preset sono il punto di taratura estetica. Il colore e in RGB leggibile,
# la conversione al formato ASS (BGR invertito) la fa to_ass_colour().
STYLES = {
    "bold": {
        "font": "Archivo Black",
        "size": 78,
        "primary": "FFFFFF",
        "outline_colour": "000000",
        "outline": 6.0,
        "shadow": 2.0,
        "active": "FFE500",  # giallo: massimo contrasto sul bianco
        "keyword": "00E5FF",  # ciano per le parole che scelgo io
        "number": "6BFF6B",  # verde per le cifre
        "y": 1290,
        "max_words": 4,
        "max_chars": 20,
        "uppercase": True,
        "punch": 112,  # scala della parola attiva, in percentuale
    },
    "minimal": {
        "font": "Archivo Black",
        "size": 66,
        "primary": "FFFFFF",
        "outline_colour": "000000",
        "outline": 4.0,
        "shadow": 1.0,
        "active": "FFFFFF",  # nessun colore: solo il leggero ingrandimento
        "keyword": "FFE500",
        "number": "FFFFFF",
        "y": 1330,
        "max_words": 5,
        "max_chars": 26,
        "uppercase": False,
        "punch": 106,
    },
    "punch": {
        "font": "Archivo Black",
        "size": 92,
        "primary": "FFFFFF",
        "outline_colour": "000000",
        "outline": 8.0,
        "shadow": 3.0,
        "active": "FF3B3B",  # rosso acceso, molto aggressivo
        "keyword": "FFE500",
        "number": "FFE500",
        "y": 1250,
        "max_words": 3,
        "max_chars": 16,
        "uppercase": True,
        "punch": 118,
    },
}

# Frase d'aggancio in cima alla clip: dice di cosa si parla prima che lo spettatore
# lo capisca da solo, e fa da promessa. Sta in alto perche il basso e occupato dai
# sottotitoli e, sulle piattaforme, dall'interfaccia.
HOOK_STYLE = {
    "font": "Archivo Black",
    "size": 84,
    "primary": "FFFFFF",
    "outline_colour": "000000",
    "accent": "FFE500",  # per le parole marcate con *asterischi*
    "outline": 7.0,
    "shadow": 3.0,
    "y": 250,  # sotto la zona dei controlli in alto delle app
    "max_chars": 19,  # per riga, prima di andare a capo
    "max_lines": 3,
    "line_spacing": 96,
    "seconds": 5.0,  # quanto resta prima di sparire
    "fade_out": 400,  # millisecondi
    "pop_in": 170,
}

# Richiamo al video intero. Non e l'hook e non deve competerci: parte quando
# l'hook ha finito di sfumare e ne prende il posto in cima, cosi i primi secondi
# restano tutti alla promessa della clip. Piu piccolo dell'hook di proposito —
# e un rimando, non la ragione per cui si guarda.
CTA_STYLE = {
    "font": "Archivo Black",
    "size": 56,
    "primary": "FFFFFF",
    "outline_colour": "000000",
    "accent": "FF2A2A",  # rosso: e il colore che l'utente chiede per la chiamata
    "outline": 5.0,
    "shadow": 2.0,
    "y": 250,  # lo stesso posto dell'hook, che a quel punto e sparito
    "max_chars": 24,
    "max_lines": 2,
    "line_spacing": 64,
    "fade_in": 300,
    "gap": 0.3,  # respiro fra la fine dell'hook e la comparsa
}

NUMBER_RE = re.compile(r"\d")
EMPHASIS_RE = re.compile(r"\*([^*]+)\*")

# Larghezza utile di una riga di sottotitoli: oltre, la riga si rimpicciolisce
# invece di uscire dal quadro. Con `WrapStyle: 2` nessuno la manderebbe a capo.
LINE_MAX_W = PLAY_W - 2 * 60
# Scala delle parole chiave a riposo, e quella d'ingresso del gruppo.
KEYWORD_SCALE = 106
ENTRY_SCALE = 88
ENTRY_MS = 120
RELEASE_MS = 80


# ------------------------------------------------------------------------------
# Larghezza del testo, letta dal font
# ------------------------------------------------------------------------------


class FontMetrics:
    """Le larghezze d'avanzamento di un TTF, e la scala con cui libass le usa.

    libass chiede a FreeType un'altezza di `size * (asc - desc) / (winAscent +
    winDescent)` pixel per l'intervallo `asc - desc` della tabella hhea: in
    pratica un'unita del font vale `size / (winAscent + winDescent)` pixel. Qui si
    rifa lo stesso conto, e la verifica e sui pixel: una riga composta parola per
    parola deve coincidere con la stessa riga composta da libass.
    """

    def __init__(self, path: Path):
        data = path.read_bytes()
        n_tables = struct.unpack(">H", data[4:6])[0]
        tables = {}
        for i in range(n_tables):
            tag, _, offset, length = struct.unpack(">4sIII", data[12 + 16 * i : 28 + 16 * i])
            tables[tag.decode("latin-1")] = (offset, length)
        self.data = data
        self.tables = tables

        o = tables["hhea"][0]
        self.n_hmetrics = struct.unpack(">H", data[o + 34 : o + 36])[0]
        o = tables["OS/2"][0]
        win_ascent, win_descent = struct.unpack(">HH", data[o + 74 : o + 78])
        self.em_height = float(win_ascent + win_descent)
        self.family = self._family()
        self.cmap = self._cmap()

    def _family(self) -> str:
        o = self.tables["name"][0]
        count, string_offset = struct.unpack(">HH", self.data[o + 2 : o + 6])
        found = {}
        for i in range(count):
            platform, encoding, _, name_id, length, offset = struct.unpack(
                ">HHHHHH", self.data[o + 6 + 12 * i : o + 18 + 12 * i]
            )
            if name_id not in (1, 16):
                continue
            raw = self.data[o + string_offset + offset : o + string_offset + offset + length]
            text = (
                raw.decode("utf-16-be", "replace") if platform in (0, 3) else raw.decode("latin-1")
            )
            found.setdefault(name_id, text)
        return found.get(16) or found.get(1) or ""

    def _cmap(self) -> dict[int, int]:
        o = self.tables["cmap"][0]
        n = struct.unpack(">H", self.data[o + 2 : o + 4])[0]
        subtables = {}
        for i in range(n):
            platform, encoding, offset = struct.unpack(
                ">HHI", self.data[o + 4 + 8 * i : o + 12 + 8 * i]
            )
            subtables[(platform, encoding)] = o + offset
        for key in ((3, 10), (0, 4), (3, 1), (0, 3)):
            if key in subtables:
                return self._read_cmap(subtables[key])
        return {}

    def _read_cmap(self, o: int) -> dict[int, int]:
        d = self.data
        fmt = struct.unpack(">H", d[o : o + 2])[0]
        out: dict[int, int] = {}
        if fmt == 12:
            groups = struct.unpack(">I", d[o + 12 : o + 16])[0]
            for g in range(groups):
                start, end, glyph = struct.unpack(">III", d[o + 16 + 12 * g : o + 28 + 12 * g])
                for c in range(start, min(end, 0x2FFFF) + 1):
                    out[c] = glyph + (c - start)
        elif fmt == 4:
            seg2 = struct.unpack(">H", d[o + 6 : o + 8])[0]
            seg = seg2 // 2
            ends = struct.unpack(f">{seg}H", d[o + 14 : o + 14 + seg2])
            starts = struct.unpack(f">{seg}H", d[o + 16 + seg2 : o + 16 + 2 * seg2])
            deltas = struct.unpack(f">{seg}h", d[o + 16 + 2 * seg2 : o + 16 + 3 * seg2])
            ro_pos = o + 16 + 3 * seg2
            range_offsets = struct.unpack(f">{seg}H", d[ro_pos : ro_pos + seg2])
            for s, e, delta, ro, k in zip(starts, ends, deltas, range_offsets, range(seg)):
                for c in range(s, e + 1):
                    if c == 0xFFFF:
                        continue
                    if ro == 0:
                        glyph = (c + delta) & 0xFFFF
                    else:
                        p = ro_pos + 2 * k + ro + 2 * (c - s)
                        glyph = struct.unpack(">H", d[p : p + 2])[0]
                        if glyph:
                            glyph = (glyph + delta) & 0xFFFF
                    out[c] = glyph
        return out

    def advance(self, glyph: int) -> int:
        o = self.tables["hmtx"][0]
        k = min(glyph, self.n_hmetrics - 1)
        return struct.unpack(">H", self.data[o + 4 * k : o + 4 * k + 2])[0]

    def width(self, text: str, size: float) -> float:
        units = sum(self.advance(self.cmap.get(ord(ch), 0)) for ch in text)
        return units * size / self.em_height


@lru_cache(maxsize=8)
def font_metrics(family: str) -> FontMetrics | None:
    """Il TTF di questa famiglia fra i font della skill, o None se non c'e."""
    candidates = sorted(FONTS_DIR.glob("*.tt[fc]"))
    fallback = None
    for path in candidates:
        try:
            metrics = FontMetrics(path)
        except (KeyError, struct.error, OSError):
            continue
        if metrics.family.lower() == family.lower():
            return metrics
        fallback = fallback or metrics
    return fallback


def text_width(text: str, family: str, size: float) -> float:
    metrics = font_metrics(family)
    if metrics is None:
        # Senza il file del font resta una stima grossolana: meglio di niente, ed
        # e il caso di una macchina appena preparata a cui mancano gli asset.
        return len(text) * size * 0.62
    return metrics.width(text, size)


def to_ass_colour(rgb: str, alpha: str = "00") -> str:
    """RGB leggibile -> &HAABBGGRR. L'ASS vuole i canali invertiti e l'alfa
    all'incontrario (00 = opaco)."""
    rgb = rgb.lstrip("#")
    r, g, b = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H{alpha}{b}{g}{r}".upper()


def ass_time(seconds: float) -> str:
    """H:MM:SS.CC — l'ASS usa i centesimi, non i millesimi."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def escape(text: str) -> str:
    """Le graffe delimitano i tag di override: nel testo vanno neutralizzate."""
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


# Fine di proposizione: qui la riga si chiude sempre. E la regola che toglie le
# righe come `I WAS SCARED. I`, dove l'ultima parola appartiene gia alla frase
# dopo ma l'occhio la legge insieme a quella prima.
STRONG_END = ".!?…"
# Confini piu deboli: buoni per andare a capo, ma solo se la riga ha gia un peso suo.
SOFT_END = ",;:"
# Un silenzio tra due parole e una punteggiatura che nessuno ha scritto.
PAUSE_BREAK = 0.30
MIN_WORDS_SOFT = 2

# Le virgolette e le parentesi stanno *dopo* il punto: vanno tolte prima di
# guardare l'ultimo carattere, altrimenti `disse."` non sembra una fine frase.
TRAILING = "\"')]»”"


def ends_clause(word: str) -> bool:
    return word.rstrip(TRAILING).endswith(tuple(STRONG_END))


def ends_phrase(word: str) -> bool:
    return word.rstrip(TRAILING).endswith(tuple(SOFT_END))


def merge_orphan_tail(groups: list[list[dict]], max_words: int, max_chars: int) -> list[list[dict]]:
    """Una parola sola in coda non e una riga, e un rimasuglio.

    Si riattacca alla precedente **solo** se non c'e una fine di frase in mezzo:
    unirle attraverso un punto fermo ricreerebbe il difetto che stiamo togliendo.
    """
    if len(groups) < 2 or len(groups[-1]) != 1:
        return groups

    prev, last = groups[-2], groups[-1]
    if ends_clause(prev[-1]["word"]):
        return groups

    merged = prev + last
    width = sum(len(w["word"]) for w in merged) + len(merged) - 1
    if len(merged) <= max_words and width <= max_chars:
        groups[-2:] = [merged]
    return groups


def salda_elisioni(parole: list[dict]) -> list[dict]:
    """Riattacca le elisioni che Whisper spezza in due token.

    L'italiano ne e pieno — `l'importante`, `dell'azienda`, `un'anima` — e
    Whisper le divide in `l` e `'importante`. Trattate come parole separate
    finiscono su righe diverse: una riga chiude con una `L` sola e la successiva
    si apre con un apostrofo, che a schermo sembra un refuso.

    Si salda qui e non nel transcript perche e un fatto di composizione, non di
    trascrizione: i tempi restano quelli delle due parti, dall'inizio della prima
    alla fine della seconda, e il karaoke accende l'elisione tutta insieme —
    che e poi come si legge.
    """
    saldate: list[dict] = []
    for w in parole:
        if saldate and w["word"].startswith("'"):
            saldate[-1] = {
                **saldate[-1],
                "word": saldate[-1]["word"] + w["word"],
                "end": w["end"],
            }
            continue
        saldate.append(w)
    return saldate


def group_words(words: list[dict], max_words: int, max_chars: int) -> list[list[dict]]:
    """Spezza il flusso di parole in righe, seguendo la frase e non il righello.

    Raggruppare per numero di caratteri produce righe come `I WAS SCARED. I`: e
    corretta come misura e sbagliata come lettura, perche l'ultima parola apre
    una frase che continua nella riga dopo. Qui la riga si chiude dove finisce
    quello che si sta dicendo — punto fermo, virgola, o una pausa vera nel
    parlato — e i limiti di lunghezza restano solo come tetto per lo schermo.

    3-5 parole per riga e comunque la lunghezza che regge sul telefono: piu
    lunga e l'occhio deve muoversi, e sui formati verticali si perde il ritmo.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []
    chars = 0

    for i, w in enumerate(words):
        text = w["word"]
        would_be = chars + len(text) + (1 if current else 0)

        # Tetto fisico: oltre, la riga non ci sta sullo schermo.
        if current and (len(current) >= max_words or would_be > max_chars):
            groups.append(current)
            current, chars, would_be = [], 0, len(text)

        current.append(w)
        chars = would_be

        if i + 1 >= len(words):
            continue
        gap = words[i + 1]["start"] - w["end"]
        abbastanza = len(current) >= MIN_WORDS_SOFT
        # Un taglio dei silenzi fra le due parole chiude sempre la riga: la pausa
        # che separava le frasi non c'e piu, ma la frase e finita lo stesso.
        taglio = w.get("tratto") != words[i + 1].get("tratto")

        if (
            taglio
            or ends_clause(text)
            or (abbastanza and (ends_phrase(text) or gap >= PAUSE_BREAK))
        ):
            groups.append(current)
            current, chars = [], 0

    if current:
        groups.append(current)
    return merge_orphan_tail(groups, max_words, max_chars)


def layout_group(texts: list[str], scales: list[float], style: dict) -> tuple[list[float], float]:
    """Il centro orizzontale di ogni parola della riga, e la scala che la fa stare.

    La riga e centrata nel quadro; ogni parola occupa la sua larghezza misurata
    dal font, per la sua scala a riposo, piu uno spazio. Se la riga supera la
    larghezza utile si rimpicciolisce tutta: una riga che esce dal quadro non da
    errore, semplicemente non si legge.
    """
    family, size = style["font"], style["size"]
    space = text_width(" ", family, size)
    widths = [text_width(t, family, size) * s / 100.0 for t, s in zip(texts, scales)]
    # **Lo spazio fra due parole tiene conto dello scatto.** La parola accesa
    # cresce attorno al proprio centro di mezzo `punch` per lato: con lo spazio
    # normale di Archivo Black (una ventina di pixel, meno i contorni) le lettere
    # toccavano le vicine — «NOTBECAUSEOF», visto sul primo render. Qui ogni
    # spazio e largo abbastanza da contenere la crescita della piu larga delle due.
    crescita = max(0.0, style["punch"] / 100.0 - 1.0) / 2.0
    gaps = [space + crescita * max(a, b) for a, b in zip(widths, widths[1:])]
    total = sum(widths) + sum(gaps)
    fit = min(1.0, LINE_MAX_W / total) if total > 0 else 1.0
    left = PLAY_W / 2.0 - total * fit / 2.0
    centres = []
    for i, w in enumerate(widths):
        centres.append(left + w * fit / 2.0)
        left += (w + (gaps[i] if i < len(gaps) else 0.0)) * fit
    return centres, fit


def build_events(
    groups: list[list[dict]], style: dict, keywords: set[str], clip_start: float, clip_end: float
) -> list[str]:
    """Tre eventi per parola al massimo: prima, mentre e dopo che viene detta.

    Tutti alla stessa posizione, cosi lo scatto della parola accesa non sposta le
    vicine. Il gruppo entra con un piccolo ingrandimento e una dissolvenza di
    60 ms; la parola accesa sale al `punch` in 90 ms e, spenta, torna giu in 80
    invece di crollare in un fotogramma.
    """
    active_c = to_ass_colour(style["active"])
    keyword_c = to_ass_colour(style["keyword"])
    number_c = to_ass_colour(style["number"])
    punch = style["punch"]
    y = style["y"]
    events: list[str] = []

    def line(start: float, end: float, tags: str, text: str) -> None:
        if ass_time(end) <= ass_time(start):
            return
        events.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Main,,0,0,0,,{{{tags}}}{text}"
        )

    for group in groups:
        group_start = max(0.0, group[0]["start"])
        group_end = min(group[-1]["end"], clip_end - clip_start)

        texts, rest_scales, rest_colours = [], [], []
        for w in group:
            text = escape(w["word"])
            texts.append(text.upper() if style["uppercase"] else text)
            bare = w["word"].strip(".,!?;:\"'()").lower()
            if bare in keywords:
                rest_scales.append(KEYWORD_SCALE)
                rest_colours.append(f"\\c{keyword_c}")
            elif NUMBER_RE.search(w["word"]):
                rest_scales.append(100)
                rest_colours.append(f"\\c{number_c}")
            else:
                rest_scales.append(100)
                rest_colours.append("")

        centres, fit = layout_group(texts, rest_scales, style)

        def sc(value: float) -> str:
            v = round(value * fit)
            return f"\\fscx{v}\\fscy{v}"

        for i, word in enumerate(group):
            pos = f"\\an5\\pos({centres[i]:.0f},{y})"
            lit_from = max(group_start, word["start"])
            # L'evidenziazione dura fino all'inizio della parola successiva, cosi
            # il gruppo resta a schermo senza buchi.
            lit_to = group[i + 1]["start"] if i + 1 < len(group) else group_end
            rest = rest_scales[i]

            if lit_from > group_start:
                line(
                    group_start,
                    lit_from,
                    f"{pos}{rest_colours[i]}\\fad(60,0){sc(rest * ENTRY_SCALE / 100)}"
                    f"\\t(0,{ENTRY_MS},{sc(rest)})",
                    texts[i],
                )
            entrata = "\\fad(60,0)" if lit_from <= group_start else ""
            # La parola accesa e l'unico evento con questo scatto `\t(0,90,`:
            # `check_sync.py` la riconosce da li.
            line(
                lit_from,
                lit_to,
                f"{pos}\\c{active_c}{entrata}{sc(100)}\\t(0,90,{sc(punch)})",
                texts[i] + "{\\r}",
            )
            if lit_to < group_end:
                line(
                    lit_to,
                    group_end,
                    f"{pos}{rest_colours[i]}{sc(punch)}\\t(0,{RELEASE_MS},{sc(rest)})",
                    texts[i],
                )

    return events


def wrap_hook(text: str, max_chars: int, max_lines: int) -> list[str]:
    """Spezza la frase in righe corte.

    Il wrapping va calcolato qui e scritto con `\\N` espliciti: l'intestazione ASS
    usa `WrapStyle: 2`, che disattiva del tutto l'andata a capo automatica. Senza
    interruzioni esplicite una frase lunga uscirebbe dai bordi.
    """
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        candidate = f"{cur} {w}".strip()
        # La lunghezza si misura sul testo senza i marcatori di enfasi.
        if len(EMPHASIS_RE.sub(r"\1", candidate)) <= max_chars or not cur:
            cur = candidate
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)

    if len(lines) > max_lines:
        # Meglio una riga piu lunga che una frase troncata.
        lines = lines[: max_lines - 1] + [" ".join(lines[max_lines - 1 :])]
    return lines


def build_hook_event(text: str, duration: float, style: dict) -> str:
    """Un solo evento Dialogue con la frase d'aggancio.

    Entra con un piccolo scatto di scala, resta, e sfuma via: la comparsa attira
    l'occhio, la sparizione libera l'inquadratura quando ha gia fatto il suo lavoro.
    """
    lines = wrap_hook(text, style["max_chars"], style["max_lines"])
    body = "\\N".join(escape(l) for l in lines)

    # Le parole tra asterischi prendono il colore d'accento.
    accent = to_ass_colour(style["accent"])
    primary = to_ass_colour(style["primary"])
    body = EMPHASIS_RE.sub(rf"{{\\c{accent}}}\1{{\\c{primary}}}", body)

    shown = min(duration, style["seconds"]) if style["seconds"] > 0 else duration
    tags = (
        f"\\an8\\pos({PLAY_W // 2},{style['y']})"
        f"\\fscx82\\fscy82\\t(0,{style['pop_in']},\\fscx100\\fscy100)"
        f"\\fad(0,{style['fade_out']})"
    )
    return f"Dialogue: 1,{ass_time(0)},{ass_time(shown)},Hook,,0,0,0,,{{{tags}}}{body}"


def build_cta_event(text: str, start: float, end: float, style: dict) -> str:
    """Il richiamo al video intero, dalla fine dell'hook alla fine della clip.

    Un solo evento: non si muove e non si evidenzia, deve solo restare leggibile
    senza rubare l'occhio ai sottotitoli. Le parole tra `*asterischi*` prendono
    il colore d'accento, come nell'hook — qui e il rosso.
    """
    lines = wrap_hook(text, style["max_chars"], style["max_lines"])
    body = "\\N".join(escape(l) for l in lines)

    accent = to_ass_colour(style["accent"])
    primary = to_ass_colour(style["primary"])
    body = EMPHASIS_RE.sub(rf"{{\\c{accent}}}\1{{\\c{primary}}}", body)

    tags = f"\\an8\\pos({PLAY_W // 2},{style['y']})\\fad({style['fade_in']},0)"
    return f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Cta,,0,0,0,,{{{tags}}}{body}"


def cta_timing(
    hook: str | None, hook_style: dict, duration: float, cta_style: dict
) -> tuple[float, int]:
    """Quando parte il richiamo, e a che altezza.

    Di norma prende il posto dell'hook appena questo e sfumato. Ma l'hook puo
    essere richiesto per tutta la clip (`hook_seconds: 0`): li non c'e nessun
    posto da prendere, quindi il richiamo scende **sotto** il blocco dell'hook
    invece di scriverglisi sopra. L'altezza dipende da quante righe occupa
    davvero l'hook, non dal suo massimo.
    """
    if not hook:
        return 0.0, cta_style["y"]

    shown = hook_style["seconds"] if hook_style["seconds"] > 0 else duration
    shown = min(shown, duration)
    dopo = shown + hook_style["fade_out"] / 1000.0 + cta_style["gap"]

    if dopo < duration - 1.0:
        return dopo, cta_style["y"]

    righe = len(wrap_hook(hook, hook_style["max_chars"], hook_style["max_lines"]))
    return 0.0, hook_style["y"] + righe * hook_style["line_spacing"]


def sospendi(parole: list[dict], pause: list[tuple[float, float]]) -> list[dict]:
    """Toglie le parole dette mentre e a schermo una grafica, e spezza le righe li.

    La grafica sta nella fascia dei sottotitoli e porta lei la frase: due testi
    nello stesso posto non si leggono. Una riga non attraversa la grafica per la
    stessa ragione per cui non attraversa un taglio dei silenzi — il `tratto`
    cambia, e `group_words` chiude la riga.
    """
    out = []
    for w in parole:
        if any(w["start"] < b and w["end"] > a for a, b in pause):
            continue
        prima = sum(1 for a, _ in pause if a <= w["start"])
        out.append({**w, "tratto": (w.get("tratto"), prima)})
    return out


def build_ass(
    words: list[dict],
    clip_start: float,
    clip_end: float,
    style_name: str = "bold",
    keywords: list[str] | None = None,
    hook: str | None = None,
    hook_seconds: float | None = None,
    cta: str | None = None,
    tenuti: list[tuple[float, float]] | None = None,
    pause: list[tuple[float, float]] | None = None,
) -> str:
    """Il file ASS della clip.

    `tenuti` sono i tratti sopravvissuti ai tagli dei silenzi (secondi relativi a
    `clip_start`): se ci sono, le parole passano al tempo della clip montata e
    nessuna riga resta a schermo attraverso un taglio. `pause` sono gli
    intervalli, nel tempo della clip montata, in cui c'e una grafica.
    """
    style = STYLES[style_name]
    keyset = {str(k).strip().lower() for k in (keywords or []) if str(k).strip()}

    # Solo le parole della finestra, riportate a tempo relativo alla clip:
    # con -ss prima di -i ffmpeg azzera i timestamp in uscita.
    picked = [
        {
            "word": w["word"],
            "start": round(w["start"] - clip_start, 3),
            "end": round(w["end"] - clip_start, 3),
        }
        for w in words
        if w["end"] > clip_start and w["start"] < clip_end
    ]
    for w in picked:
        w["start"] = max(0.0, w["start"])
        w["end"] = min(w["end"], clip_end - clip_start)

    picked = salda_elisioni(picked)
    duration = clip_end - clip_start
    if tenuti and len(tenuti) > 1:
        import tagli

        picked = tagli.remap_words(picked, [tuple(t) for t in tenuti])
        duration = sum(b - a for a, b in tenuti)
    if pause:
        picked = sospendi(picked, pause)
    groups = group_words(picked, style["max_words"], style["max_chars"])
    events = build_events(groups, style, keyset, 0.0, duration)

    hstyle = dict(HOOK_STYLE)
    if hook_seconds is not None:
        hstyle["seconds"] = hook_seconds

    if hook:
        # In testa alla lista: l'ordine degli eventi non conta per libass, che usa
        # i tempi, ma tenerlo in cima rende il file leggibile.
        events.insert(0, build_hook_event(hook, duration, hstyle))

    if cta:
        cstyle = dict(CTA_STYLE)
        inizio, y = cta_timing(hook, hstyle, duration, cstyle)
        cstyle["y"] = y
        events.insert(1 if hook else 0, build_cta_event(cta, inizio, duration, cstyle))

    hs = HOOK_STYLE
    cs = CTA_STYLE
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_W}
PlayResY: {PLAY_H}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{style["font"]},{style["size"]},{to_ass_colour(style["primary"])},{to_ass_colour(style["active"])},{to_ass_colour(style["outline_colour"])},&H80000000,0,0,0,0,100,100,0,0,1,{style["outline"]},{style["shadow"]},5,60,60,60,1
Style: Hook,{hs["font"]},{hs["size"]},{to_ass_colour(hs["primary"])},{to_ass_colour(hs["accent"])},{to_ass_colour(hs["outline_colour"])},&H80000000,0,0,0,0,100,100,0,0,1,{hs["outline"]},{hs["shadow"]},8,70,70,60,1
Style: Cta,{cs["font"]},{cs["size"]},{to_ass_colour(cs["primary"])},{to_ass_colour(cs["accent"])},{to_ass_colour(cs["outline_colour"])},&H80000000,0,0,0,0,100,100,0,0,1,{cs["outline"]},{cs["shadow"]},8,70,70,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return header + "\n".join(events) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Genera i sottotitoli ASS di una clip")
    ap.add_argument("work_dir")
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--style", default="bold", choices=sorted(STYLES))
    ap.add_argument("--keywords", default="", help="parole da evidenziare, separate da virgola")
    ap.add_argument(
        "--hook",
        default=None,
        help="frase d'aggancio in cima alla clip. Le parole tra "
        "*asterischi* prendono il colore d'accento.",
    )
    ap.add_argument(
        "--hook-seconds",
        type=float,
        default=None,
        help="quanto resta l'hook (0 = per tutta la clip)",
    )
    ap.add_argument(
        "--cta",
        default=None,
        help="richiamo al video intero, dalla fine dell'hook in poi. "
        "Le parole tra *asterischi* prendono il rosso.",
    )
    args = ap.parse_args()

    work = Path(args.work_dir)
    transcript = read_json(work / "transcript.json")

    text = build_ass(
        transcript["words"],
        args.start,
        args.end,
        style_name=args.style,
        keywords=args.keywords.split(",") if args.keywords else [],
        hook=args.hook,
        hook_seconds=args.hook_seconds,
        cta=args.cta,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")

    n_events = text.count("Dialogue:")
    log(f"{n_events} eventi, stile '{args.style}', font in {FONTS_DIR}")
    emit({"path": str(out), "events": n_events, "style": args.style})


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
