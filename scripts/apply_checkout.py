# -*- coding: utf-8 -*-
r"""
Propaga i link di cassa da checkout.json a tutte le pagine, e li verifica.

PERCHE' ESISTE
L'URL del checkout compare in 8 punti su 6 file, e due di quei file vengono
RIGENERATI ogni lunedi' dal cron: correggerli a mano significa vederli tornare
indietro dopo sette giorni. Con questo script il cambio di cassa e' UN comando,
e il rollback e' lo stesso comando con un valore diverso.

COME FUNZIONA
Ogni <a> di pagamento porta un attributo inerte:

    <a data-rs-checkout="pro_mensile" href="https://..." class="...">

Lo script riscrive SOLO il valore di href dentro quel tag. Non tocca classi,
stili o testo: e' un'operazione di trenta caratteri, ed e' idempotente.
Funziona su un bottone e su un link dentro un paragrafo allo stesso modo.

⛔ Niente risoluzione via JavaScript: un bottone di pagamento che dipende da JS
   smette di funzionare in silenzio, e le issue-*.html finiscono incollate
   dentro beehiiv, dove il JS viene rimosso.

USO
  python scripts/apply_checkout.py --check          verifica, non scrive (exit 0/1)
  python scripts/apply_checkout.py --apply          propaga il rail attivo
  python scripts/apply_checkout.py --rail beehiiv --apply    rollback

Solo stdlib: gira sia in GitHub Actions sia col python portatile.
"""
import argparse
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(REPO, "checkout.json")

# <a ... data-rs-checkout="CHIAVE" ... href="URL" ...>  in QUALUNQUE ordine
ANCORA = re.compile(
    r'<a\b(?P<attrs>[^>]*\bdata-rs-checkout\s*=\s*"(?P<chiave>[a-z_]+)"[^>]*)>',
    re.IGNORECASE | re.DOTALL,
)
HREF = re.compile(r'(?P<pre>\bhref\s*=\s*")(?P<url>[^"]*)(?P<post>")', re.IGNORECASE)

CHIAVI_SEGRETE = re.compile(r'\b(sk_live_|rk_live_|sk_test_|rk_test_)')


def leggi(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def scrivi(p, s):
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


def carica():
    with open(CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


# Due file parlano di questi pattern per mestiere: checkout.json contiene il
# rollback, e questo script contiene le stringhe che cerca. Senza l'esenzione
# il controllo si autodenuncia e diventa impossibile da soddisfare - stesso
# difetto trovato il 26/07 in guard-docs, dove INDEX.md si flaggava da solo
# mentre spiegava quali fatti erano morti.
AUTO_ESENTI = {
    os.path.abspath(CONFIG),
    os.path.abspath(__file__),
}


def file_da_scandire():
    """Tutti i file di testo del repo, esclusi .git e le cartelle di servizio."""
    esclusi = {".git", "__pycache__", "node_modules"}
    for radice, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in esclusi]
        for f in files:
            if not f.endswith((".html", ".py", ".xml", ".json", ".txt", ".md", ".yml", ".csv")):
                continue
            p = os.path.join(radice, f)
            if os.path.abspath(p) in AUTO_ESENTI:
                continue
            yield p


def sostituisci(testo, links):
    """Riscrive l'href di ogni ancora. Ritorna (nuovo_testo, {chiave: conteggio})."""
    trovate = {}

    def _fix(m):
        chiave = m.group("chiave")
        trovate[chiave] = trovate.get(chiave, 0) + 1
        url = links.get(chiave)
        if not url:
            return m.group(0)          # nessun link per questa chiave: non tocco
        attrs, n = HREF.subn(lambda h: h.group("pre") + url + h.group("post"),
                             m.group("attrs"), count=1)
        if n == 0:
            return m.group(0)
        return "<a" + attrs + ">"

    return ANCORA.sub(_fix, testo), trovate


def comando_apply(cfg, rail, dry):
    links = cfg["rails"][rail]["links"]
    mancanti = [k for k, v in links.items() if not v]
    if mancanti:
        print("ATTENZIONE: il rail %r non ha ancora i link per: %s" % (rail, ", ".join(mancanti)))
        print("            Le ancore con quelle chiavi restano invariate.")

    toccati = 0
    for nome in cfg["ancore"]:
        if nome.startswith("_"):
            continue
        path = os.path.join(REPO, nome)
        if not os.path.isfile(path):
            print("  saltato (non esiste): %s" % nome)
            continue
        prima = leggi(path)
        dopo, trovate = sostituisci(prima, links)
        if dopo != prima:
            if not dry:
                scrivi(path, dopo)
            toccati += 1
            print("  %s %-42s %s" % ("[dry]" if dry else "  ok ", nome,
                                     ", ".join("%s x%d" % kv for kv in sorted(trovate.items()))))
        else:
            print("       %-42s gia' allineato" % nome)
    print("%s: %d file %s" % ("SIMULAZIONE" if dry else "APPLICATO", toccati,
                              "da aggiornare" if dry else "aggiornati"))
    return 0


def comando_check(cfg, rail):
    links = cfg["rails"][rail]["links"]
    problemi = []

    # 1. Manifest + 2. coerenza href <-> json
    for nome, attese in cfg["ancore"].items():
        if nome.startswith("_"):
            continue
        path = os.path.join(REPO, nome)
        if not os.path.isfile(path):
            problemi.append("%s: dichiarato nelle ancore ma non esiste" % nome)
            continue
        testo = leggi(path)
        conteggio = {}
        for m in ANCORA.finditer(testo):
            chiave = m.group("chiave")
            conteggio[chiave] = conteggio.get(chiave, 0) + 1
            atteso = links.get(chiave)
            h = HREF.search(m.group("attrs"))
            if atteso and (not h or h.group("url") != atteso):
                problemi.append("%s: l'ancora %r punta a %r invece che al link del rail %r"
                                % (nome, chiave, h.group("url") if h else "(nessun href)", rail))
        for chiave, n in attese.items():
            if conteggio.get(chiave, 0) != n:
                problemi.append("%s: attese %d ancore %r, trovate %d"
                                % (nome, n, chiave, conteggio.get(chiave, 0)))
        for chiave in conteggio:
            if chiave not in attese:
                problemi.append("%s: ancora %r non dichiarata in checkout.json" % (nome, chiave))

    # 3. Asserzione negativa: nessun residuo di un rail NON attivo.
    #    E' il controllo che cattura l'occorrenza dimenticata in un file
    #    che nessuno ricordava di avere.
    for altro, frammenti in cfg["_residui_vietati"].items():
        if altro == rail:
            continue
        for path in file_da_scandire():
            try:
                testo = leggi(path)
            except (UnicodeDecodeError, OSError):
                continue
            for fr in frammenti:
                if fr in testo:
                    rel = os.path.relpath(path, REPO)
                    riga = next((i for i, l in enumerate(testo.splitlines(), 1) if fr in l), "?")
                    problemi.append("%s:%s contiene un residuo del rail %r (%r)"
                                    % (rel, riga, altro, fr))

    # 4. Sanita': niente URL di test in produzione, niente chiavi nel repo
    for path in file_da_scandire():
        try:
            testo = leggi(path)
        except (UnicodeDecodeError, OSError):
            continue
        rel = os.path.relpath(path, REPO)
        if CHIAVI_SEGRETE.search(testo):
            problemi.append("%s: sembra contenere una chiave API. Il repo e' PUBBLICO." % rel)
        if "buy.stripe.com/test_" in testo:
            problemi.append("%s: contiene un Payment Link in MODALITA' TEST. "
                            "La pagina sembra funzionare e non incassa niente." % rel)

    if problemi:
        print("BLOCCA - %d problemi (rail attivo: %s)" % (len(problemi), rail))
        for p in problemi:
            print("  - " + p)
        return 1
    print("passa - rail attivo: %s, ancore coerenti, nessun residuo, nessuna chiave" % rail)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--rail", help="sovrascrive rail_attivo (usato per il rollback)")
    args = ap.parse_args()

    try:
        cfg = carica()
    except Exception as ex:
        print("ERRORE: checkout.json illeggibile (%s)" % ex)
        return 2

    rail = args.rail or cfg["rail_attivo"]
    if rail not in cfg["rails"]:
        print("ERRORE: rail %r sconosciuto. Disponibili: %s"
              % (rail, ", ".join(cfg["rails"])))
        return 2

    if args.apply:
        if args.rail and args.rail != cfg["rail_attivo"]:
            cfg["rail_attivo"] = args.rail
            if not args.dry:
                with open(CONFIG, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                print("rail_attivo -> %r" % args.rail)
        return comando_apply(cfg, rail, args.dry)

    return comando_check(cfg, rail)


if __name__ == "__main__":
    sys.exit(main())
