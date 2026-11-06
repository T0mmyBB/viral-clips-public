# Stato di avanzamento — skill `viral-clips`

> **Stato del lavoro e misure che decidono.** Le decisioni stabili stanno in
> [CLAUDE.md](CLAUDE.md), le istruzioni di esecuzione in
> [SKILL.md](.claude/skills/viral-clips/SKILL.md). Qui sta cio' che e' vero adesso: le
> misure che giustificano una scelta, i componenti, e i tempi che `report.py` rigenera.

## Le misure che decidono

Quelle citate da CLAUDE.md e da SKILL.md per giustificare una scelta. Le altre — i tempi
per fase e per video — le scrive `report.py` piu sotto, e nessuno le digita.

| cosa | misura | macchina |
|---|---|---|
| proxy 480p contro sorgente 4K, cambi scena | 30 s contro oltre 15 min non finiti | prima |
| proxy scaricato contro transcodificato | 23 s di rete contro 5.5 min di CPU | prima |
| `-hwaccel` su 60 s di 4K AV1 | software 12.8 s · vulkan 19.2 · dxva2 33.8 · d3d11va 43.7 | prima (Radeon 680M) |
| render `--jobs 2` su 6 clip | 200.1 s contro 227.4 s in sequenza (1.14x) | 16 core |
| render `--jobs 2` su **38 clip** | 1431 s contro 1329 s in sequenza (**7.7% piu lento**) | 16 core |
| `--threads` su 10 min di audio, turbo | 8 -> 181.2 s · 12 -> 175.0 · 16 -> **186.1** | 16 core fisici |
| bordi sui confini di segmento | 0 clip su 38 da rivedere, contro 38 su 38 | — |
| filtro delle risate | da 85 falsi allarmi a 0 su un monologo di studio | — |
| camera, per inquadratura | escursione 15% -> 2%, inversioni 2.5/s -> 0.00 | — |
| camera, micro-salti isolati del crop | **79-240 al minuto -> 0-5**, su 4 clip | — |
| render con la camera pianificata | 19,3 s contro 19,4 s; file -3,5% | 16 core |
| sottotitoli parola per parola contro la riga di libass | scarto **entro 1 px** su tre righe | — |
| vista a segmenti contro `transcript.json` | **38 volte** piu piccola a parita di decisione | — |
| giro del radar, per passo | catalogo ~1 min · 528 schede ~3 min · 265 brief ~13 min | seconda |
| master 4K da Drive (16 min di video) | **7,0 GB**, ~35 min di rete · il transcodificato 1080p accanto: 266 MB | seconda |
| `ffprobe` sull'indirizzo http di quel master | **5 s** per width, height, codec e fps, senza scaricarlo | seconda |
| render di 18 clip, 4K -> 1080x1920, `--jobs 1` | 1072 s (59 s a clip, 597 s di uscita) | seconda |

## La pipeline

| Script | Cosa fa |
|---|---|
| `setup.ps1` | ffmpeg, Python 3.12, venv, modelli, font |
| `run.py` | `prepare` e `render`, le due meta del flusso |
| `common.py` | percorsi, helper ffmpeg, `analysis_source()` |
| `check_docs.py` | verifica che i documenti descrivano il codice vero |
| `check_material.py` | anomalie: verticale, senza volti, senza parlato |
| `ingest.py` | download a due fasi, lingua audio, proxy scaricato, metadati |
| `transcribe.py` | faster-whisper, timestamp per parola |
| `signals.py` | energia, pause, risate (filtrate sul parlato), cambi scena |
| `faces.py` | prescansione volti, linea temporale composizione |
| `track_subject.py` | chi inquadrare, stacchi, ricucitura |
| `camera.py` | la regia: pose, movimenti, zoom, crop a ogni fotogramma |
| `tagli.py` | i silenzi dentro la clip: cosa si toglie, e i tempi dopo |
| `grafiche.py` | le grafiche animate: modelli HyperFrames, istante sulla parola detta, sottotitoli sospesi |
| `campagna.py` | aggancia la cartella, copia il brief, ne marca i vincoli, tiene le regole di campagna |
| `automatico.py` | la catena intera senza fermarsi: prepara, legge il brief, sceglie, valida, renderizza |
| `windows.py` | vista a segmenti, anomalie e paletti in testa, `--words` per i bordi |
| `apply_corrections.py` | le parole sentite male, corrette da Claude |
| `validate.py` | durata, punteggi, logo, **e i bordi sul transcript**; con `--consegna`, `clips.md` accanto agli mp4 |
| `check_sync.py` | sottotitoli in sincrono, sui pixel del render, anche dopo i tagli; colore acceso preso dallo stile |
| `check_camera.py` | il crop che salta, rigiocando il `sendcmd`: micro-salti, inciampi, quadro deformato |
| `make_subs.py` | ASS karaoke parola per parola + hook in sovrimpressione |
| `render_clip.py` | un solo passaggio ffmpeg da `decisions.json` |
| `report.py` | rigenera i numeri misurati di questo file |
| `export_skill.py` | impacchetta la skill senza i media |

### Misure rigenerate — nessuno le digita

Tutto quello che sta **dentro i marcatori** lo scrive `report.py`, leggendo i tempi che
`run.py` registra mentre lavora. Si aggiorna con `python report.py --write`.

<!-- metrics:start -->
<!-- metrics:end -->

## Da fare, in ordine

