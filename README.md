# Backend raccolta sagre

Script Python che raccoglie eventi da piu' fonti, li pulisce, elimina i doppioni, trova le
coordinate dei comuni e scrive `docs/sagre.json`, il feed letto dall'app.

## Come funziona
fonti (`sources.yaml` + `data/manuali.csv`) -> normalizzazione -> filtro date -> deduplica
-> geocodifica (OpenStreetMap Nominatim, con cache in `data/geocache.json`) -> `docs/sagre.json`.

Protezioni: non sovrascrive il feed se il risultato e' vuoto o crolla sotto la meta' di prima
(fonte rotta); scrive solo se qualcosa e' cambiato.

## Prova in locale
    pip install -r requirements.txt
    CONTACT_EMAIL=tua@email.it python collector/main.py

## Pubblicazione gratuita (GitHub)
1. Crea un repository **pubblico** e carica questa cartella.
2. Settings > Secrets and variables > Actions
3. : aggiungi il secret `CONTACT_EMAIL` (la tua email;
   serve a Nominatim per identificarti).
3. Settings > Pages: Source = "Deploy from a branch", branch `main`, cartella `/docs`.
4. Actions > "Aggiorna sagre" > Run workflow (poi va da solo ogni giorno).
5. Il feed sara' su `https://TUO-UTENTE.github.io/NOME-REPO/sagre.json`: mettilo in `feedURL`
   dentro `SagreStore.swift` dell'app.

## Aggiungere dati
- A mano: righe in `data/manuali.csv` (date `2026-11-14` o `14/11/2026`).
- Da fonti online: blocchi in `sources.yaml` (tipi `ical`, `json`, `csv`, `html`).

## Attenzione
- Usa solo fonti di cui puoi usare i dati: controlla condizioni d'uso, licenza e robots.txt;
  preferisci feed ufficiali, open data e calendari che le Pro Loco ti autorizzano a usare.
- Nominatim consente 1 richiesta/secondo: lo script lo rispetta (max 300 nuovi comuni a giro).
- GitHub puo' sospendere i workflow pianificati dopo 60 giorni senza attivita': controlla ogni tanto.
