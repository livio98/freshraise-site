# Delivery — come l'edizione arriva a chi paga

> Scritto il **28/07/2026**, quando il fornitore di newsletter è stato tolto dal business per
> decisione di Federico. Questo file esiste perché senza di esso la domanda *"un cliente paga,
> e poi?"* non ha una risposta scritta da nessuna parte.

## Il modello in una riga

**Stripe è la lista. La casella di posta è il mezzo. Il sito è il prodotto.**

Non c'è nessuna piattaforma di newsletter, nessun elenco separato da tenere in sincronia, e
quindi nessuna sincronizzazione che possa rompersi in silenzio.

## Il giro completo

1. **Lunedì ~06:00 UTC** il cron `weekly.yml` genera l'edizione e la pubblica:
   - `issue-<label>.html` — edizione datata, pubblica, indicizzabile (solo fatti pubblici)
   - `sample.html` — teaser pubblico, nasconde apposta i migliori
   - `feed-<token>.xml` — edizione **integrale**, URL non indovinabile, ⚠️ mai in pubblico
2. **Chi ha pagato riceve una email**, scritta a mano, con: una riga su cosa c'è questa settimana
   e il link all'edizione integrale. Una email sola, in **Ccn**.
3. **Chi sono "quelli che hanno pagato"**: si leggono da Stripe, non da un file.
   ```
   founder/tools/_stripe.py  ->  GET /subscriptions?status=active   e   status=trialing
   ```
   Un cliente che disdice esce dalla lista **da solo**, perché la lista è lo stato di Stripe.

## Perché non un fornitore di invio

A 0 clienti, aggiungere un fornitore vuol dire: un account nuovo, una spesa, un dominio da
autenticare (SPF/DKIM), una lista da tenere in sincronia con Stripe — e **una sincronizzazione
è precisamente la cosa che si rompe senza che nessuno se ne accorga**, fino al lunedì in cui
serve. Una email in Ccn non ha nessuno di questi modi di fallire.

## Dove si rompe questo modello — dichiarato adesso, non dopo

| Soglia | Cosa si rompe | Cosa si fa |
|---|---|---|
| **~30 abbonati** | La Ccn manuale diventa noiosa e fragile; i limiti di invio Gmail (~500/giorno) restano lontani ma la *disciplina* no | Si valuta un fornitore vero, con il ricavo che ormai lo paga |
| **primo abbonato** | Serve un modo per dire "sono uscito" che non sia solo la disdetta Stripe | Già coperto: `privacy.html` promette *"rispondi e smettiamo"*, e una risposta arriva a una persona vera |
| **recapito** | Una email personale a più destinatari può finire in spam | Ccn, niente immagini pesanti, un solo link, nessun tracciamento |

⚠️ **La promessa scritta sul sito è la prima cosa che si adegua, non l'ultima.** `privacy.html`
oggi dichiara esattamente questo modello: Stripe come unico terzo che tiene i dati, Google come
provider di posta, nessuno strumento di invio massivo. Se il modello cambia, quella pagina cambia
nello stesso commit — è una dichiarazione GDPR, non una descrizione di marketing.

## Cosa NON è cambiato

Il motore. `weekly_feed.py` non ha mai parlato con nessuna piattaforma di newsletter: scriveva
file e basta. La consegna è sempre stata manuale — prima l'incollavi in un pannello, adesso la
mandi da una casella. **La differenza è solo dove si incolla**, e un fornitore in meno da pagare.
