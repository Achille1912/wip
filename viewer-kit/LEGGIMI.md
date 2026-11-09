# Kit: schema vivo della pipeline

- `pipeline-viewer`: lo strumento (un file Python, solo libreria standard).
- `vendor/`: le librerie del disegno (Cytoscape.js, ELK, etichette HTML), servite dallo strumento senza CDN. **Va copiata accanto a `pipeline-viewer`**, altrimenti la pagina resta vuota.
- `pipeline-viewer.md`: agente per Claude Code che lo usa.
- `esempio-archivio.spec.json`: spec reale (28 blocchi, layout manuale).

## Provarlo subito
```
python3 pipeline-viewer --init          # crea pipeline.spec.json di esempio
python3 pipeline-viewer                 # http://127.0.0.1:8766/
python3 pipeline-viewer esempio-archivio.spec.json --port 8767
```
Modifica la spec: la pagina si aggiorna da sola. Le note vanno in `.pipeline-viewer/note.json` accanto alla spec.

## In un altro progetto
1. Copia `pipeline-viewer` e la cartella `vendor/` nel progetto (es. `tools/`) e `pipeline-viewer.md` in `.claude/agents/`.
2. Nella sessione Claude Code: «usa l'agente pipeline-viewer per disegnare la pipeline di questo progetto».
3. Per far applicare le note: «leggi le note della pipeline».

Senza agente: «Leggi `pipeline-viewer.md` nel kit e fai quello che descrive per questo progetto».
