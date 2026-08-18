"""Round-six adversarial corpus for core.enrich: nothing may be invented.

The rule is unchanged, narrow and absolute: **for any input, every address
returned must be one the page actually states.** A miss is fine. A plausible
wrong address is not — it becomes `best_email`, exports to the CSV and gets
cold-emailed, and the business never learns why.

Round five closed two root causes (`@` as shorthand for the word "at", and
invisible splits inside a local part) and this file re-asserts both. It then
opens ground nobody has tested:

* `PROSE_CORPUS` — 207 lines of small-business web copy in **six** languages
  (EN in four case styles, FR, DE, ES, NL, IT), carrying `at` and `dot` in
  every ordinary position. No line contains an `@`, so the correct yield from
  all 207 is zero.
* `STATED_CORPUS` — the half nobody has run: prose that **does** state one
  address, in ordinary sentence position, in all six languages. Exactly that
  address may come back. This is where a scanner that over-reaches at a
  sentence boundary shows itself.
* `ATTACK_SURFACES` — every channel that has fabricated before (handles,
  credits, captions, `at`/`dot` as words, business names containing Dot,
  invisible characters, entity forms, srcset, `data:` URIs, punycode, percent
  encoding, JSON escapes, split tags, hidden decoys, CSS and JS assembly)
  **plus** eight surfaces never tried: `<noscript>`, Open Graph meta, an SVG
  `<title>`, RDFa, a base64 data attribute, `<template>`, an ARIA label, and a
  print stylesheet.
* `SAFE_*` — every legitimate discovery path, so a fix cannot buy the zero by
  deleting the feature.

Not one corpus line, domain or surface is reused from
`tests/test_enrich_fabrication.py` or `tests/test_enrich_email.py`.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import enrich as E  # noqa: E402


class _Pages:
    """Swap the module's one network seam for a fixed {url: html} map."""

    def __init__(self, pages):
        self._pages = dict(pages)
        self.asked = []
        self._saved = None

    def __enter__(self):
        self._saved = E._fetch_page
        E._fetch_page = self._serve
        return self

    def __exit__(self, *exc_info):
        E._fetch_page = self._saved
        return False

    def _serve(self, url, timeout=8.0):
        self.asked.append(url)
        for key in (url, url.rstrip("/"), url + "/"):
            if key in self._pages:
                return url, self._pages[key], ""
        return "", "", "http 404"


# ════════════════════════════════════════════════════════════════════════
# Battery 1 — 207 lines of ordinary copy. Correct yield: zero.
# ════════════════════════════════════════════════════════════════════════

EN_LOWER = (
    "the dot on our sign is hand painted at the workshop each spring.",
    "we keep a spare dot matrix head at the back of the parts drawer.",
    "ask at the hatch for dot, she does the alterations herself.",
    "our second van is parked at the rear of dot lane all week.",
    "every tin at dot and rowe hardware is weighed at the counter.",
    "a dot of beeswax at each hinge keeps the drawer running sweet.",
    "the smallest dot at the corner of a plate is our maker's mark.",
    "we start at six and the ovens are down by two, dot on.",
    "the echo dot in the waiting room plays the local station.",
    "boards at dot timber are cut at no extra charge.",
    "look for the amber dot at the end of the shelf for seconds.",
    "dot marchant has swept this floor at closing since 1991.",
    "hire rates at the yard start at nine pounds the day.",
    "the forge at wrenfield dot ironworks is lit at first light.",
    "there is always a dot of chalk at the edge of the cutting table.",
    "our stall at the wednesday market opens at seven sharp.",
    "the till at dot provisions still rings a bell at every sale.",
    "one dot at the seam tells you the piece was fired twice.",
    "we sand at the bench, never at the machine, on thin veneer.",
    "prices at the hatch begin at two pounds forty the pound.",
    "the counter at halloway dot bakery closes at four on saturdays.",
    "a red dot at the base means the glaze is lead free.",
    "dot keeps the appointment book at the desk by the door.",
)

EN_TITLE = (
    "Dot Matrix Labels Cut To Length At Our Bindery",
    "Meet Dot Ackroyd, Who Has Run The Counter Since 1998",
    "Echo Dot Demonstrations At The County Fair On Saturday",
    "Dot Grid Ledgers Back In Stock At The Stationers",
    "Free Parking At The Rear Of Our Yard On Kiln Lane",
    "Reserve A Bench At Dot And Marrow From Midday",
    "Every Pie At Dot Kitchen Is Rolled At Five In The Morning",
    "Meet Us At The Harbour Gate At Eight On Sunday",
    "Dot Grid Card Trimmed At The Guillotine While You Wait",
    "Our Surveyor Will Call At Your Yard Before Noon",
    "Resoling At The Bench Starts At Twenty Two Pounds",
    "Ask At The Hatch For The Dot Rewards Booklet",
    "The Amber Dot At The Handle Means Seconds Quality",
    "Trade Cards Issued At Dot Merchants The Same Day",
    "Dot Matrix Docket Books Printed At Our Works On Foundry Row",
    "Collect At The Loading Bay At A Quarter Past Four",
    "Dot Prentice Has Kept The Books At This Branch Since 2004",
    "An Echo Dot Sits At Every Bench In The New Workshop",
    "Estimates At The Kerb Start At Forty Five Pounds",
    "Find The Silver Dot At The Foot Of Every Piece We Cast",
    "Sharpening At The Wheel Is Booked At The Desk Only",
    "Dot And Fennel Florists Open At Half Seven On Weekdays",
    "Deliveries At The Side Door At Ten And At Three",
)

EN_CAPS = (
    "DOT MATRIX RIBBON REWINDS DONE AT THE BENCH",
    "FIND DOT AT THE TRADE HATCH FROM SIX THIRTY",
    "ECHO DOT STARTER PACKS AT ALL THREE BRANCHES",
    "THE SALE AT DOT AND BARROW ENDS AT CLOSING",
    "OUR YARD IS AT 8 FOUNDRY ROW, GATE TWO",
    "EVERY BLADE GROUND AT OUR WHEEL GETS A DOT STAMP",
    "QUOTES AT THE KERB START AT 12.00",
    "COLLECT AT THE LOADING BAY ANY WEEKDAY AT FOUR",
    "DOT GRID PADS AT THREE FOR TWO ALL MONTH",
    "NO BOOKING NEEDED AT THE DOT WALK IN BENCH",
    "THE VAN IS AT THE QUAY AT FIRST LIGHT",
    "WATCH FOR THE AMBER DOT AT THE SHELF EDGE",
    "REFITS AT ANCHOR DOT BOATYARD BOOK MONTHS AHEAD",
    "ASK AT THE DESK FOR DOT MATRIX DOCKET PAPER",
    "DOT RENNIE MINDS THE COUNTER AT OUR THIRD SHOP",
    "EVERY CRATE AT THE HATCH IS WEIGHED AT DISPATCH",
    "THE KILN AT MARLOW DOT POTTERY FIRES AT DUSK",
    "PARKING AT THE QUAY IS FREE AFTER 18.00",
    "AN AMBER DOT AT THE SPINE MEANS SHOP COPY ONLY",
    "CALL AT THE HUT AT 4 FOUNDRY ROW FOR THE KEY",
    "DOT AND SON UPHOLSTERY WORKS AT THE OLD MILL",
    "SAMPLES AT THE DESK ARE FREE AT ALL TIMES",
    "THE PRESS AT VELLUM DOT BINDERY RUNS AT DAWN",
)

EN_SENTENCE = (
    "Dot has kept the flower stall at the station since 1990.",
    "The dot matrix dockets at our yard print in quadruplicate.",
    "An Echo Dot sits at the bench so the radio never stops.",
    "Look for the amber dot at the foot of every jug we throw.",
    "Our office at 11 Foundry Row opens at half eight.",
    "Every survey at Dot Glazing is free and takes twenty minutes.",
    "The needle at the front dial should rest at the dot.",
    "You may settle at the hatch or at the van when we deliver.",
    "Dot and her brother have kept this forge for four decades.",
    "Deliveries at the side gate at nine, collections at five.",
    "A single dot at the foot of the docket closes the job.",
    "We keep ribbon at the desk for dot matrix dockets.",
    "Rates at the shed at wrenfield dot works begin at 42.00 an hour.",
    "Ask at the hatch and Dot will fetch it from the back.",
    "The bindery at foulis dot press folds every sheet by hand.",
    "Dot Rennie served her time at the yard on Quay Street in 1982.",
    "A dot of tallow at the thread stops the screw seizing.",
    "Our joiners start at seven and sweep down at four.",
    "The Echo Dot at the hatch takes song requests from the queue.",
    "Swatches at the desk are free, and a full case costs 9.00.",
    "Dot Marchant taught half the trade at the night school.",
    "The dot on the gauge at the boiler must sit at nine.",
    "We fettle at the bench and fire at the far kiln.",
)

FR_LINES = (
    "l'atelier dot et soeurs restaure des horloges depuis 1954.",
    "la dot de la mariée est mentionnée au registre paroissial.",
    "notre imprimante à aiguilles avance dot après dot.",
    "Madame Dot Vasseur tient le comptoir le mercredi matin.",
    "Nous Sommes Au 4 Quai Des Chartrons, Ouvert Dès Sept Heures",
    "L'ENCEINTE ECHO DOT RESTE EN VITRINE JUSQU'À DIMANCHE",
    "LES TARIFS COMMENCENT À 8,40 EUROS AU COMPTOIR",
    "Le chien de l'atelier dort au pied de l'établi.",
    "Chaque commande au dépôt est pesée à la réception.",
    "l'atelier dot et associés pose des volets depuis 1968.",
    "NOTRE ENTREPÔT SE TROUVE AU 17 RUE DU MOULIN NEUF",
    "le petit dot doré sur le socle signale une pièce unique.",
    "Réservez Un Établi Au Bistrot Dot Avant Vingt Heures",
    "Les travaux au 9 place du Marché durent jusqu'en juin.",
    "un dot de cire à chaque charnière suffit amplement.",
    "Le Rendez-Vous Au Salon Dot Se Prend Au Comptoir",
    "LA FORGE DE L'ATELIER DOT CHAUFFE DÈS SIX HEURES",
    "Monsieur Dot Cheval tient la scierie depuis 1979.",
    "les prix au kilo commencent à 5,20 au marché.",
    "Notre Dépôt Au 6 Chemin Des Peupliers Ferme À Treize Heures",
    "un merle chante toujours au-dessus de la verrière.",
    "LA BOULANGERIE AU FOURNIL DOT MEULE CUIT AU FEU DE BOIS",
    "notre comptoir au 3 rue Basse ouvre au lever du jour.",
)

DE_LINES = (
    "die tischlerei dot und töchter liegt am alten schlachthof.",
    "unser nadeldrucker schiebt das papier dot um dot weiter.",
    "Frau Dot Kellner führt die Kasse seit 1987.",
    "Echo Dot Vorführung In Unserer Filiale Am Ring",
    "DOT MATRIX FARBBÄNDER LIEGEN AM LAGER BEREIT",
    "DIE PREISE STARTEN BEI 9,60 EURO AN DER THEKE",
    "Unser Laden liegt in der Gerberstr. 21 im Hinterhof.",
    "der gelbe dot am griff bedeutet zweite wahl.",
    "Wir Öffnen Am Freitag Um Sieben Uhr Fünfzehn",
    "Herr Dot liefert die Bretter am Donnerstag selbst aus.",
    "DIE SCHLOSSEREI DOT UND NEFFEN ARBEITET SEIT 1949",
    "am tor finden sie einen blauen dot auf dem pflaster.",
    "Die Werkstatt Am Wehr Dot Schmiede Nimmt Reparaturen An",
    "Termine am späten Nachmittag sind oft noch offen.",
    "EIN DOT AUF DER ANZEIGE MELDET DEN LEERLAUF",
    "Beratung An Der Theke Kostet Bei Uns Nichts",
    "die drechslerei am steg dot holzwerk liefert am mittwoch.",
    "Herr Dot Lindner richtet Sensen seit fünfunddreissig Jahren.",
    "UNSERE PREISE BEGINNEN BEI 6,40 EURO JE METER",
    "Ein Echo Dot Steht Am Werktisch Fuer Das Radio",
    "der laden in der Seilerstr. 9 hat am Mittwoch geschlossen.",
    "EIN DOT KLEBER AN DER KANTE REICHT VOELLIG AUS",
    "Am Samstag beginnt der Verkauf am Hoftor um acht.",
)

ES_LINES = (
    "el taller dot y sobrinos repara toldos desde 1977.",
    "TU ECHO DOT SE CONFIGURA GRATIS EN EL MOSTRADOR",
    "Doña Dot despacha en la barra por las tardes.",
    "Estamos en la Ronda del Puerto, número 12.",
    "los precios arrancan en 7,60 euros en la barra.",
    "La Impresora De Agujas Avanza Punto A Punto, Dot A Dot",
    "EL PUNTO ÁMBAR EN LA BALDA INDICA SEGUNDA CALIDAD",
    "Cada pedido en el obrador se pesa al recogerlo.",
    "el obrador dot y nietos amasa a las cuatro y media.",
    "Reserve Su Banco En La Taberna Dot Antes De Las Nueve",
    "ABRIMOS DE MARTES A SÁBADO A LAS OCHO EN PUNTO",
    "un dot de cera en cada bisagra basta y sobra.",
    "La Cerrajería Dot Y Sobrinos Copia Llaves En Diez Minutos",
    "Nuestro almacén en la calle del Horno, 5, abre a las seis.",
    "EL TALLER DOT FRAGUA BATE EL HIERRO EN CALIENTE",
    "Puede abonar en barra o al recibir el pedido.",
    "don dot pereira lleva la balanza desde hace cuarenta años.",
    "LA MATRICIAL SIGUE IMPRIMIENDO DOT TRAS DOT",
    "Nuestro Taller En La Calle Del Horno Dot Herrería Abre A Las Seis",
    "un dot de aceite en el pasador evita el desgaste.",
    "Los Domingos Cerramos A La Una Y Media",
    "el muestrario en la barra cuesta 3,50 euros.",
    "Cada pieza del horno dot alfar lleva una marca al pie.",
)

NL_LINES = (
    "de werkplaats dot en dochters slijpt beitels sinds 1958.",
    "Mevrouw Dot Wolters staat woensdags achter de toonbank.",
    "Bezoek Ons Aan De Havenstr. 22 In De Oude Haven",
    "ONZE WINKEL AAN DE VESTINGWAL OPENT OM ZEVEN UUR",
    "de matrixprinter schuift het papier dot na dot door.",
    "Een Echo Dot Staat Op De Werkbank Voor De Radio",
    "PRIJZEN BEGINNEN BIJ 6,80 EURO AAN DE TOOG",
    "de gele dot op de greep betekent tweede keus.",
    "De Smederij Dot En Neven Werkt Alleen Op Afspraak",
    "Wij sluiten op zaterdag om drie uur.",
    "EEN DOT WAS OP ELKE SCHARNIER IS RUIM VOLDOENDE",
    "de bakkerij aan de kade dot ambacht bakt om vier uur.",
    "Meneer Dot komt de maat donderdag zelf opnemen.",
    "ONZE LOODS AAN DE VEERWEG 7 IS ELKE DAG OPEN",
    "Vraag Aan De Toonbank Naar De Dot Spaarkaart",
    "afspraken laat op de middag zijn vaak nog vrij.",
    "DE TIMMERWERKPLAATS AAN DE SLUIS DOT HOUTWERK LEVERT OP WOENSDAG",
    "Meneer Dot Kuiper zet zagen al vijfendertig jaar.",
    "onze prijzen beginnen bij 5,90 euro per meter.",
    "Het Kantoor Aan De Grachtstr. 11 Is Dinsdag Gesloten",
    "EEN GELE DOT OP DE RUG BETEKENT ALLEEN TER PLAATSE",
    "de bezorger vertrekt om zeven uur en is om drie uur terug.",
    "elke pot uit de oven dot pottenbakkerij krijgt een merk.",
)

IT_LINES = (
    "la bottega dot e figli restaura sedie dal 1962.",
    "la stampante ad aghi avanza dot dopo dot.",
    "La Signora Dot Ferrero tiene la cassa il giovedì mattina.",
    "Siamo In Via Del Fornaio 14, Aperti Dalle Sette",
    "L'ECHO DOT RESTA IN VETRINA FINO A DOMENICA",
    "I PREZZI PARTONO DA 8,30 EURO AL BANCO",
    "Il gatto della bottega dorme sul bancone di legno.",
    "Ogni ordine al deposito viene pesato alla consegna.",
    "la bottega dot e nipoti monta persiane dal 1971.",
    "IL NOSTRO DEPOSITO SI TROVA IN VIA DELLA FORNACE 9",
    "il piccolo dot dorato sul piede indica un pezzo unico.",
    "Prenotate Un Tavolo Alla Trattoria Dot Entro Le Venti",
    "I lavori in Piazza del Mercato finiscono a giugno.",
    "un dot di cera su ogni cerniera basta e avanza.",
    "L'APPUNTAMENTO AL SALONE DOT SI PRENDE AL BANCO",
    "Il signor Dot Rovere tiene la segheria dal 1984.",
    "i prezzi al chilo partono da 4,80 al mercato.",
    "Il Nostro Deposito In Via Dei Pioppi 6 Chiude All'Una",
    "LA FORGIA DELLA BOTTEGA DOT SCALDA DALLE SEI",
    "ogni piatto del forno dot ceramiche porta un segno al piede.",
    "Le Consegne Al Portone Laterale Sono Alle Nove",
    "un merlo canta sempre sopra la vetrata del cortile.",
    "il banco in Via Bassa 3 apre alle prime luci.",
)

CORPUS_GROUPS = {
    "en-lower": EN_LOWER, "en-title": EN_TITLE, "en-caps": EN_CAPS,
    "en-sentence": EN_SENTENCE, "fr": FR_LINES, "de": DE_LINES,
    "es": ES_LINES, "nl": NL_LINES, "it": IT_LINES,
}
PROSE_CORPUS = tuple(l for g in CORPUS_GROUPS.values() for l in g)

# `word at word dot tld` — the shape four rounds were spent killing. Asserted
# present so a later edit cannot quietly defang the corpus and keep the zero.
FABRICATING_SHAPE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9][A-Za-z0-9._%+\-]{0,62}"
    r"\s+at\s+(?:[A-Za-z0-9][A-Za-z0-9\-]{0,61}\s+dot\s+)+[A-Za-z]{2,24}"
    r"(?![A-Za-z0-9\-])", re.I)


def test_the_prose_corpus_is_large_multilingual_and_dangerous():
    assert len(PROSE_CORPUS) >= 200, len(PROSE_CORPUS)
    assert len(set(PROSE_CORPUS)) == len(PROSE_CORPUS), "duplicate lines"
    assert len(CORPUS_GROUPS) == 9
    for name, group in CORPUS_GROUPS.items():
        assert len(group) >= 20, (name, len(group))
    for line in PROSE_CORPUS:
        assert "@" not in line, line
    carrying = [l for l in PROSE_CORPUS if FABRICATING_SHAPE.search(l)]
    assert len(carrying) >= 6, carrying
    assert sum("dot" in l.lower() for l in PROSE_CORPUS) >= 110
    assert sum(re.search(r"\b(at|au|am|en|aan|al)\b", l, re.I) is not None
               for l in PROSE_CORPUS) >= 150
    print("prose corpus: OK (%d lines, 6 languages, %d carry the shape)"
          % (len(PROSE_CORPUS), len(carrying)))


def test_the_prose_corpus_mints_nothing_line_by_line():
    minted = {}
    for line in PROSE_CORPUS:
        found = E._scan_page("<p>%s</p>" % line, "https://dotbindery.works/")
        if found:
            minted[line] = found
    assert minted == {}, minted
    print("prose corpus mints nothing, line by line: OK (%d lines)"
          % len(PROSE_CORPUS))


def test_the_prose_corpus_mints_nothing_in_bulk():
    page = "<html><body>%s</body></html>" % "".join(
        "<p>%s</p>" % l for l in PROSE_CORPUS)
    assert E._scan_page(page, "https://dotbindery.works/") == {}
    assert E.extract_contacts(page, "https://dotbindery.works")["email"] == ""
    with _Pages({"https://dotbindery.works": page}):
        site = E.harvest_site("dotbindery.works", verify_dns=False)
    assert site["reachable"] is True
    assert site["best_email"] == "" and site["emails"] == [], site["emails"]
    print("prose corpus mints nothing in bulk: OK")


# ════════════════════════════════════════════════════════════════════════
# Battery 2 — prose that DOES state one address. Exactly that, nothing else.
# ════════════════════════════════════════════════════════════════════════
#
# The half no earlier round ran. Every line below is ordinary copy with the
# business's own address written out in the middle of it, the way a contact
# page or a footer actually reads. Anything returned besides the one address
# named in the tuple is a fabrication.

STATED_CORPUS = (
    ("en-1", "Write to orders@wrenfield.works. Deliveries leave on Fridays.",
     "orders@wrenfield.works"),
    ("en-2", "Trade enquiries go to sales@halloway.kitchen. Ask for the list.",
     "sales@halloway.kitchen"),
    ("en-3", "Email hello@marlow.gallery. Kiln bookings close on Tuesday.",
     "hello@marlow.gallery"),
    ("en-4", "Our office reads info@foulis.press. Proofs come back same day.",
     "info@foulis.press"),
    ("en-5", "Send drawings to studio@vellum.studio. Quotes take a day.",
     "studio@vellum.studio"),
    ("en-6", "Contact bookings@anchor.boats. Winter refits fill up early.",
     "bookings@anchor.boats"),
    ("fr-1", "Écrivez à contact@atelier-cheval.fr. Nous ouvrons dès sept heures.",
     "contact@atelier-cheval.fr"),
    ("fr-2", "Les commandes passent par devis@fournil-meule.fr. Merci de préciser.",
     "devis@fournil-meule.fr"),
    ("fr-3", "Notre comptoir répond à bonjour@salon-vasseur.fr. Réponse sous un jour.",
     "bonjour@salon-vasseur.fr"),
    ("fr-4", "Adressez vos plans à bureau@scierie-basse.fr. Devis gratuit.",
     "bureau@scierie-basse.fr"),
    ("de-1", "Schreiben Sie an kontakt@tischlerei-kellner.de. Wir melden uns rasch.",
     "kontakt@tischlerei-kellner.de"),
    ("de-2", "Bestellungen bitte an lager@schlosserei-neffen.de. Abholung ab acht.",
     "lager@schlosserei-neffen.de"),
    ("de-3", "Anfragen gehen an buero@drechslerei-steg.de. Termine am Vormittag.",
     "buero@drechslerei-steg.de"),
    ("de-4", "Unser Meister liest post@schmiede-wehr.de. Reparaturen dauern zwei Tage.",
     "post@schmiede-wehr.de"),
    ("es-1", "Escriba a pedidos@taller-pereira.es. Abrimos a las seis en punto.",
     "pedidos@taller-pereira.es"),
    ("es-2", "Las consultas llegan a info@cerrajeria-sobrinos.es. Copiamos llaves.",
     "info@cerrajeria-sobrinos.es"),
    ("es-3", "Mande su plano a taller@fragua-horno.es. Presupuesto sin coste.",
     "taller@fragua-horno.es"),
    ("es-4", "Reservas en mesa@taberna-dot.es. Cerramos los domingos.",
     "mesa@taberna-dot.es"),
    ("nl-1", "Mail naar bestellingen@smederij-neven.nl. Wij leveren op woensdag.",
     "bestellingen@smederij-neven.nl"),
    ("nl-2", "Vragen gaan naar info@houtwerk-sluis.nl. Afspraken in de ochtend.",
     "info@houtwerk-sluis.nl"),
    ("nl-3", "Stuur uw maten naar kantoor@wolters-toonbank.nl. Offerte binnen een dag.",
     "kantoor@wolters-toonbank.nl"),
    ("nl-4", "Onze bakkerij leest orders@kade-ambacht.nl. Brood gaat om vier uur.",
     "orders@kade-ambacht.nl"),
    ("it-1", "Scrivete a ordini@bottega-ferrero.it. Siamo aperti dalle sette.",
     "ordini@bottega-ferrero.it"),
    ("it-2", "Le richieste arrivano a info@segheria-rovere.it. Preventivo gratuito.",
     "info@segheria-rovere.it"),
    ("it-3", "Prenotate a tavoli@trattoria-pioppi.it. Chiudiamo il lunedì.",
     "tavoli@trattoria-pioppi.it"),
    ("it-4", "Il forno legge posta@ceramiche-fornace.it. Consegne alle nove.",
     "posta@ceramiche-fornace.it"),
)


def stated_corpus_fabrications():
    out = []
    for name, line, correct in STATED_CORPUS:
        got = set(E._scan_page("<p>%s</p>" % line, "https://x.test/"))
        for bad in sorted(got - {correct}):
            out.append(("stated/" + name, bad))
    return out


def test_stated_prose_returns_only_the_address_it_states():
    bad = stated_corpus_fabrications()
    assert bad == [], (
        "%d fabrications across %d stated-prose lines:\n%s"
        % (len(bad), len({n for n, _ in bad}),
           "\n".join("  %-14s -> %s" % (n, a) for n, a in bad)))
    print("stated prose returns only its own address: OK (%d lines)"
          % len(STATED_CORPUS))


def test_every_stated_address_is_still_found():
    """The mirror image: the zero must not be bought by refusing everything."""
    missing = [name for name, line, correct in STATED_CORPUS
               if correct not in E._scan_page("<p>%s</p>" % line, "https://x.test/")]
    assert missing == [], missing
    print("every stated address is still found: OK (%d lines)" % len(STATED_CORPUS))


# ════════════════════════════════════════════════════════════════════════
# Battery 3 — every surface that could invent one.
# ════════════════════════════════════════════════════════════════════════
#
# (name, html, {addresses the page genuinely states}). Anything returned that
# is not in that set is a fabrication and is counted and named.

ATTACK_SURFACES = [
    # ── `@` as ordinary shorthand for the word "at" ──
    ("handle-ig", '<p>Follow us @larkspur.garden for the weekly cut list</p>', set()),
    ("handle-tag-us", '<p>Tag us @tidewater.works and we will repost</p>', set()),
    ("handle-sub", '<p>DM us @shop.foulis.co.uk before six</p>', set()),
    ("handle-fr", '<p>Suivez-nous @atelier.paris tous les jours</p>', set()),
    ("handle-de", '<p>Folgen Sie uns @werkstatt.koeln</p>', set()),
    ("handle-es", '<p>S&iacute;guenos @fragua.madrid</p>', set()),
    ("handle-nl", '<p>Volg ons @smederij.amsterdam</p>', set()),
    ("handle-it", '<p>Seguiteci @bottega.milano</p>', set()),
    ("handle-nbsp", '<p>Follow us\u00a0@larkspur.garden</p>', set()),
    ("handle-newline", '<p>Follow us\n\n    @larkspur.garden</p>', set()),
    ("handle-tab", '<p>Follow us\t@larkspur.garden</p>', set()),
    ("handle-pct40", '<p>Follow us %40larkspur.garden</p>', set()),
    ("handle-entity-at", '<p>Follow us &#64;larkspur.garden</p>', set()),
    ("handle-entity-hex", '<p>Follow us &#x40;larkspur.garden</p>', set()),
    ("handle-entity-dot", '<p>Follow us @larkspur&#46;garden</p>', set()),
    ("handle-punycode", '<p>Besuchen Sie uns @xn--kln-sna.de</p>', set()),
    ("handle-mov", '<p>Watch the reel @cuttings.mov</p>', set()),
    ("handle-zip", '<p>Download the pack @press.zip</p>', set()),
    ("handle-plus", '<p>Follow us @larkspur.garden+seeds</p>', set()),
    ("credit-build", '<p>&copy; 2026 Halloway &amp; Dot. Built @ tidewater.works</p>', set()),
    ("credit-design", '<p>Design @ ferrule.studio &middot; Photos @ quayside.photo</p>', set()),
    ("credit-host", '<p>Hosted @ northgate.hosting since 2011</p>', set()),
    ("caption-shot", '<figcaption>Loaves cooling @ dawn, shot @ marlow.photo</figcaption>',
     set()),
    ("caption-venue", "<figcaption>Our stall @ Quayside Market, pitch 14</figcaption>",
     set()),
    ("venue-hall", "<p>Join us @ St. Botolph's Hall on the first Sunday</p>", set()),
    ("time-doors", '<p>Doors @ 6.45pm, first pour @ 7.00pm</p>', set()),
    ("price-loaf", '<p>Rye @ 4.20 a loaf, seeded @ 4.60</p>', set()),
    ("rate-hour", '<p>Billed @ 2.5 hours minimum on site</p>', set()),
    ("suite-addr", '<p>Unit 7 @ 40 Foundry Row, Gate Two</p>', set()),

    # ── business names containing Dot, and `at`/`dot` as ordinary words ──
    ("dot-brand-lower", '<p>the kiln at marlow dot pottery fires at dusk</p>', set()),
    ("dot-brand-caps", '<p>THE PRESS AT VELLUM DOT BINDERY RUNS AT DAWN</p>', set()),
    ("dot-person", '<p>Ask at the hatch and Dot will fetch it from the back.</p>', set()),
    ("dot-echo", '<p>An Echo Dot sits at the bench so the radio never stops.</p>', set()),
    ("dot-matrix", '<p>We keep ribbon at the desk for dot matrix dockets.</p>', set()),
    ("dot-name-before-at", '<p>Ask Dot orders@wrenfield.works for trade prices</p>',
     {"orders@wrenfield.works"}),
    ("dot-name-before-pct40", '<p>Ask Dot orders%40wrenfield.works for trade prices</p>',
     {"orders@wrenfield.works"}),
    ("dot-brand-before-at", '<p>Smederij Dot info@smederij-neven.nl</p>',
     {"info@smederij-neven.nl"}),
    ("dot-brand-before-at-fr", '<p>Atelier Dot contact@atelier-cheval.fr</p>',
     {"contact@atelier-cheval.fr"}),
    ("dot-bracket-before-at", '<p>Follow (dot) us@larkspur.garden</p>',
     {"us@larkspur.garden"}),

    # ── a sentence boundary immediately after a stated address ──
    ("sentence-period", '<p>Write to orders@wrenfield.works. Visit the yard any day.</p>',
     {"orders@wrenfield.works"}),
    ("sentence-newline", '<p>orders@wrenfield.works.\nVisit the yard any day.</p>',
     {"orders@wrenfield.works"}),
    ("sentence-nbsp", '<p>orders@wrenfield.works.\u00a0Visit the yard any day.</p>',
     {"orders@wrenfield.works"}),
    ("sentence-two-hops", '<p>orders@wrenfield.works. Visit. Thanks for reading.</p>',
     {"orders@wrenfield.works"}),
    ("sentence-caps", '<p>ORDERS@WRENFIELD.WORKS. VISIT THE YARD ANY DAY.</p>',
     {"orders@wrenfield.works"}),
    ("sentence-cctld", '<p>Write to info@halloway.co. UK orders ship free.</p>',
     {"info@halloway.co"}),
    ("sentence-gtld-tel", '<p>Email: sales@marlow.gallery. Tel: 0161 496 0122</p>',
     {"sales@marlow.gallery"}),
    ("sentence-punycode", '<p>Mail info@xn--kln-sna.de. Wir oeffnen um acht.</p>',
     {"info@xn--kln-sna.de"}),
    ("sentence-bracketed", '<p>Trade: orders (at) wrenfield (dot) works. Free delivery.</p>',
     {"orders@wrenfield.works"}),
    ("sentence-pct", '<p>Trade: orders%40wrenfield%2eworks%2e Free delivery.</p>',
     {"orders@wrenfield.works"}),

    # ── invisible characters inside a local part: refuse whole, never shorten ──
    ("zw-zwsp", '<p>or\u200bders@wrenfield.works</p>', set()),
    ("zw-zwnj", '<p>or\u200cders@wrenfield.works</p>', set()),
    ("zw-zwj", '<p>or\u200dders@wrenfield.works</p>', set()),
    ("zw-shy", '<p>or\u00adders@wrenfield.works</p>', set()),
    ("zw-bom", '<p>or\ufeffders@wrenfield.works</p>', set()),
    ("zw-wj", '<p>or\u2060ders@wrenfield.works</p>', set()),
    ("zw-lrm", '<p>or\u200eders@wrenfield.works</p>', set()),
    ("zw-rlm", '<p>or\u200fders@wrenfield.works</p>', set()),
    ("zw-nbsp", '<p>or\u00a0ders@wrenfield.works</p>', set()),
    ("zw-ent-zwnj", '<p>or&zwnj;ders@wrenfield.works</p>', set()),
    ("zw-ent-shy", '<p>or&shy;ders@wrenfield.works</p>', set()),
    ("zw-ent-dec", '<p>or&#8203;ders@wrenfield.works</p>', set()),
    ("zw-ent-hex", '<p>or&#x200b;ders@wrenfield.works</p>', set()),

    # ── hidden-element decoys and split tags ──
    ("decoy-span", '<p>or<span style="display:none">XX</span>ders@wrenfield.works</p>',
     set()),
    ("decoy-empty", '<p>or<b></b>ders@wrenfield.works</p>', set()),
    ("decoy-comment", '<p>or<!--strip-->ders@wrenfield.works</p>', set()),
    ("decoy-nospam", '<p>o<span class="nope">nospam</span>rders@wrenfield.works</p>',
     set()),
    ("decoy-aria-hidden",
     '<p>or<i aria-hidden="true">JUNK</i>ders@wrenfield.works</p>', set()),
    ("split-at-tag", '<p>orders<span>@</span>wrenfield.works</p>', set()),
    ("split-wbr", '<p>orders@<wbr>wrenfield.works</p>', set()),
    ("split-local-tail", '<p>order<em>s</em>@wrenfield.works</p>', set()),
    ("split-whole-local", '<p><span>orders</span>@wrenfield.works</p>', set()),
    ("split-table-cells", '<td>orders</td><td>@wrenfield.works</td>', set()),

    # ── CSS and JS assembly the scanner deliberately does not attempt ──
    ("css-two-rules",
     '<style>.a:after{content:"orders"}.b:after{content:"@wrenfield.works"}</style>',
     set()),
    ("css-unicode-escape",
     '<style>.a:after{content:"orders\\0040wrenfield.works"}</style>', set()),
    ("css-counter", '<style>.a::before{content:"orders" attr(data-d)}</style>', set()),
    ("js-concat", '<script>var u="orders",d="wrenfield.works",e=u+"@"+d;</script>',
     set()),
    ("js-concat-split", '<script>document.write("order"+"s@wrenfield"+".works")</script>',
     set()),
    ("js-atob", '<script>atob("b3JkZXJzQHdyZW5maWVsZC53b3Jrcw==")</script>', set()),
    ("js-reverse", '<script>var e="skrow.dleifnerw@sredro".split("").reverse().join("")</script>',
     set()),
    ("js-rot13", '<script>var e=rot13("beqref@jerasvryq.jbexf")</script>', set()),

    # ── srcset, data: URIs and asset filenames ──
    ("srcset-density", '<img srcset="/i/mark@2x.png 2x, /i/mark@3x.png 3x">', set()),
    ("srcset-spaced", '<img srcset="/i/still @wrenfield.mov 2x">', set()),
    ("srcset-jxl", '<img srcset="/i/hero@2x.jxl 2x, /i/hero@3x.jxl 3x">', set()),
    ("asset-svgz", '<img src="/i/mark@2x.svgz" alt="maker mark">', set()),
    ("asset-jpg", '<img src="/i/bench@wrenfield.jpg">', set()),
    ("asset-mov-density", '<video src="/clips/kiln@2x.mov"></video>', set()),
    ("asset-webp-spaced", '<img src="/i/hero @2x.webp">', set()),
    ("font-version", '<style>@font-face{font-family:Lark;src:url(/f/lark@v3.ttf)}</style>',
     set()),
    ("download-xlsx", '<a href="/downloads/orders@2x.xlsx">order form</a>', set()),
    ("datauri-png", '<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEA">',
     set()),
    ("datauri-svg-pct",
     '<img src="data:image/svg+xml,%3Csvg%3EFollow us @larkspur.garden%3C/svg%3E">',
     set()),
    ("datauri-svg-utf8",
     '<img src=\'data:image/svg+xml;utf8,<svg><text>Follow us @larkspur.garden</text></svg>\'>',
     set()),

    # ── percent encoding, punycode, JSON escapes ──
    ("pct-path", '<p>See /u/orders%40wrenfield.works/ for the trade page</p>',
     {"orders@wrenfield.works"}),
    ("pct-mailto-plus", '<a href="mailto:orders%2Btrade@wrenfield.works">mail</a>',
     {"orders+trade@wrenfield.works"}),
    ("pct-double", '<p>orders%2540wrenfield.works</p>', set()),
    ("pct-coupon", '<p>Use code SPRING%40TEN at the till</p>', set()),
    ("pct-query", '<p>?utm_campaign=spring%40list</p>', set()),
    ("puny-text", '<p>orders@xn--wrenfld-5za.works</p>', {"orders@xn--wrenfld-5za.works"}),
    ("puny-bracketed", '<p>orders (at) xn--wrenfld-5za (dot) works</p>',
     {"orders@xn--wrenfld-5za.works"}),
    ("puny-tld", '<p>orders@shop.xn--p1acf</p>', {"orders@shop.xn--p1acf"}),
    ("jsonesc-gt", '<script>var s="\\u003eorders@wrenfield.works\\u003c";</script>',
     {"orders@wrenfield.works"}),
    ("jsonesc-quot", '<script>{"e":"\\u0022sales@wrenfield.works\\u0022"}</script>',
     {"sales@wrenfield.works"}),
    ("jsonesc-amp", '<script>{"e":"\\u0026info@wrenfield.works"}</script>',
     {"info@wrenfield.works"}),

    # ── NEW SURFACES: never tested in any earlier round ──
    # <noscript>
    ("noscript-handle", '<noscript><p>Follow us @larkspur.garden</p></noscript>', set()),
    ("noscript-css-fallback",
     '<noscript><style>.m:after{content:"orders"}.n:after{content:"@wrenfield.works"}'
     '</style></noscript>', set()),
    ("noscript-real",
     '<noscript><a href="mailto:seeds@larkspur.garden">seeds@larkspur.garden</a></noscript>',
     {"seeds@larkspur.garden"}),
    ("noscript-sentence",
     '<noscript>Email seeds@larkspur.garden. Delivery is free on Fridays.</noscript>',
     {"seeds@larkspur.garden"}),

    # Open Graph / Twitter card meta
    ("og-handle",
     '<meta property="og:description" content="Follow us @larkspur.garden">', set()),
    ("og-title-at",
     '<meta property="og:title" content="Larkspur @ Quayside Market">', set()),
    ("og-site-handle", '<meta name="twitter:site" content="@larkspur.garden">', set()),
    ("og-url", '<meta property="og:url" content="https://larkspur.garden/contact/">',
     set()),
    ("og-real",
     '<meta property="og:description" content="Write to seeds@larkspur.garden">',
     {"seeds@larkspur.garden"}),
    ("og-sentence",
     '<meta property="og:description" '
     'content="Write to seeds@larkspur.garden. Cut flowers every Friday.">',
     {"seeds@larkspur.garden"}),

    # SVG <title>
    ("svgtitle-handle",
     '<svg role="img"><title>Follow us @larkspur.garden</title></svg>', set()),
    ("svgtitle-split",
     '<svg><title>seeds</title><desc>@larkspur.garden</desc></svg>', set()),
    ("svgtitle-real",
     '<svg role="img"><title>seeds@larkspur.garden</title></svg>',
     {"seeds@larkspur.garden"}),
    ("svgtitle-sentence",
     '<svg><title>Mail seeds@larkspur.garden. Open daily.</title></svg>',
     {"seeds@larkspur.garden"}),

    # RDFa
    ("rdfa-handle",
     '<div vocab="https://schema.org/" typeof="Florist">'
     '<span property="email">@larkspur.garden</span></div>', set()),
    ("rdfa-at-word",
     '<div typeof="Florist"><span property="name">Larkspur @ Quayside</span></div>',
     set()),
    ("rdfa-real",
     '<div vocab="https://schema.org/" typeof="Florist">'
     '<span property="email">seeds@larkspur.garden</span></div>',
     {"seeds@larkspur.garden"}),
    ("rdfa-resource",
     '<div typeof="Florist"><a property="email" '
     'resource="mailto:seeds@larkspur.garden">Email</a></div>',
     {"seeds@larkspur.garden"}),

    # base64 data attribute (a miss is the correct answer — nothing is stated)
    ("b64-data-attr",
     '<div data-contact="c2VlZHNAbGFya3NwdXIuZ2FyZGVu">Contact</div>', set()),
    ("b64-data-mailto",
     '<button data-href="bWFpbHRvOnNlZWRzQGxhcmtzcHVyLmdhcmRlbg==">Email</button>',
     set()),
    ("b64-data-handle",
     '<span data-bio="Rm9sbG93IHVzIEBsYXJrc3B1ci5nYXJkZW4=">bio</span>', set()),
    ("b64-datauri-html",
     '<iframe src="data:text/html;base64,PHA+c2VlZHNAbGFya3NwdXIuZ2FyZGVuPC9wPg=="></iframe>',
     set()),

    # <template>
    ("template-placeholder",
     '<template id="card"><a href="mailto:{{email}}">{{email}}</a></template>', set()),
    ("template-handle", '<template><p>Follow us @larkspur.garden</p></template>', set()),
    ("template-split",
     '<template><span>seeds</span><span>@larkspur.garden</span></template>', set()),
    ("template-real", '<template><p>seeds@larkspur.garden</p></template>',
     {"seeds@larkspur.garden"}),

    # ARIA label
    ("aria-handle",
     '<a href="/ig" aria-label="Follow us @larkspur.garden on Instagram">IG</a>', set()),
    ("aria-at-word",
     '<button aria-label="Book a table @ 7.30pm">Book</button>', set()),
    ("aria-describedby",
     '<a aria-describedby="h" href="/c">Contact</a><span id="h" hidden>@larkspur.garden</span>',
     set()),
    ("aria-real", '<a href="/c" aria-label="Email seeds@larkspur.garden">Contact</a>',
     {"seeds@larkspur.garden"}),
    ("aria-sentence",
     '<a aria-label="Email seeds@larkspur.garden. We reply within a day.">c</a>',
     {"seeds@larkspur.garden"}),

    # print stylesheet
    ("print-media-attr-handle",
     '<style media="print">.f:after{content:" Follow us @larkspur.garden"}</style>',
     set()),
    ("print-atmedia-split",
     '<style>@media print{.a:after{content:"seeds"}.b:after{content:"@larkspur.garden"}}'
     '</style>', set()),
    ("print-atmedia-asset",
     '<style>@media print{.logo{background:url(/i/mark@2x.png)}}</style>', set()),
    ("print-atmedia-href",
     '<style>@media print{a[href]:after{content:" (" attr(href) ")"}}</style>', set()),
    ("print-atmedia-real",
     '<style>@media print{.f:after{content:" seeds@larkspur.garden"}}</style>',
     {"seeds@larkspur.garden"}),

    # ── genuinely present, even if hidden: found is correct, not invented ──
    ("comment-real", '<!-- retired: sales@old-wrenfield.works -->',
     {"sales@old-wrenfield.works"}),
    ("hidden-real",
     '<div style="display:none"><a href="mailto:x@trap.wrenfield.works">x</a></div>',
     {"x@trap.wrenfield.works"}),
]


def surface_fabrications():
    out = []
    for name, html, correct in ATTACK_SURFACES:
        got = set(E._scan_page(html, "https://wrenfield.works/"))
        for bad in sorted(got - correct):
            out.append(("surface/" + name, bad))
    return out


def all_fabrications():
    return stated_corpus_fabrications() + surface_fabrications()


def test_no_surface_invents_an_address():
    bad = surface_fabrications()
    assert bad == [], (
        "%d fabricated addresses across %d of %d surfaces:\n%s"
        % (len(bad), len({n for n, _ in bad}), len(ATTACK_SURFACES),
           "\n".join("  %-28s -> %s" % (n, a) for n, a in bad)))
    print("no surface invents an address: OK (%d surfaces)" % len(ATTACK_SURFACES))


def test_every_surface_that_states_an_address_still_yields_it():
    missing = []
    for name, html, correct in ATTACK_SURFACES:
        got = set(E._scan_page(html, "https://wrenfield.works/"))
        for want in sorted(correct - got):
            missing.append((name, want))
    assert missing == [], missing
    print("every stated surface still yields its address: OK")


def test_a_fabrication_reaches_best_email_and_the_csv():
    """Why the bar is zero: a mint is not a stray dict key, it is the answer."""
    cases = (
        ("bracketed + sentence", "wrenfield.works",
         '<html><body><p>Trade: orders (at) wrenfield (dot) works. '
         'Free delivery on Fridays.</p></body></html>', "orders@wrenfield.works"),
        ("percent + sentence", "wrenfield.works",
         '<html><body><p>Trade: orders%40wrenfield%2eworks%2e '
         'Free delivery on Fridays.</p></body></html>', "orders@wrenfield.works"),
        ("Dot + percent", "wrenfield.works",
         '<html><body><p>Ask Dot orders%40wrenfield.works for trade prices.'
         '</p></body></html>', "orders@wrenfield.works"),
    )
    wrong = []
    for label, domain, page, correct in cases:
        got = E.extract_contacts(page, "https://%s" % domain)["email"]
        if got != correct:
            wrong.append((label, got or "(nothing)", correct))
    assert wrong == [], (
        "best_email is a fabricated address:\n"
        + "\n".join("  %-22s exported %-34s should be %s" % w for w in wrong))
    print("no fabricated best_email: OK")


def test_a_phantom_does_not_ride_beside_a_real_address_in_a_crawl():
    home = ('<html><body><h1>Wrenfield Ironworks</h1>'
            '<p>Follow us @wrenfield.works for the forge diary.</p>'
            '<a href="/contact/">Contact</a></body></html>')
    contact = ('<html><body><h1>Contact</h1>'
               '<p>Write to orders@wrenfield.works. The yard is open daily.</p>'
               '</body></html>')
    with _Pages({"https://wrenfield.works": home,
                 "https://wrenfield.works/contact/": contact}):
        site = E.harvest_site("wrenfield.works", max_pages=4, verify_dns=False)
    assert [r["email"] for r in site["emails"]] == ["orders@wrenfield.works"], \
        site["emails"]
    print("no phantom row in a real crawl: OK")


# ════════════════════════════════════════════════════════════════════════
# Battery 4 — nothing safe may be lost.
# ════════════════════════════════════════════════════════════════════════

SAFE_GTLDS = (
    "works", "kitchen", "gallery", "press", "studio", "boats", "florist",
    "gallery", "coffee", "wine", "farm", "fish", "toys", "bike", "plumbing",
    "construction", "tools", "glass", "kitchen", "haus",
)


def test_bracketed_obfuscation_across_twenty_modern_gtlds():
    for tld in SAFE_GTLDS:
        for raw, want in (
                ("orders (at) wrenfield (dot) %s" % tld, "orders@wrenfield.%s" % tld),
                ("orders [at] wrenfield [dot] %s" % tld, "orders@wrenfield.%s" % tld),
                ("orders {at} wrenfield {dot} %s" % tld, "orders@wrenfield.%s" % tld)):
            got = E._scan_page("<p>%s</p>" % raw, "x")
            assert got == {want: "deobfuscated"}, (raw, got)
    print("bracketed obfuscation across %d gTLDs: OK" % len(SAFE_GTLDS))


def test_every_ordinary_channel_still_yields_its_address():
    cases = {
        "mailto": ('<a href="mailto:orders@wrenfield.works">Order</a>',
                   {"orders@wrenfield.works": "mailto"}),
        "mailto subject": ('<a href="mailto:orders@wrenfield.works?subject=Trade">m</a>',
                           {"orders@wrenfield.works": "mailto"}),
        "mailto multi": ('<a href="mailto:orders@wrenfield.works,sales@wrenfield.works">m</a>',
                         {"orders@wrenfield.works": "mailto",
                          "sales@wrenfield.works": "mailto"}),
        "plain text": ("<p>Write to orders@wrenfield.works any weekday</p>",
                       {"orders@wrenfield.works": "text"}),
        "jsonld": ('<script type="application/ld+json">'
                   '{"@type":"HardwareStore","email":"orders@wrenfield.works"}</script>',
                   {"orders@wrenfield.works": "jsonld"}),
        "jsonld nested": ('<script type="application/ld+json">'
                          '{"@graph":[{"contactPoint":{"email":"orders@wrenfield.works"}}]}'
                          '</script>', {"orders@wrenfield.works": "jsonld"}),
        "jsonld malformed": ('<script type="application/ld+json">'
                             '{"email":"orders@wrenfield.works",}</script>',
                             {"orders@wrenfield.works": "jsonld"}),
        "microdata content": ('<meta itemprop="email" content="orders@wrenfield.works">',
                              {"orders@wrenfield.works": "jsonld"}),
        "microdata href": ('<a itemprop="email" href="mailto:orders@wrenfield.works">m</a>',
                           {"orders@wrenfield.works": "mailto"}),
        "microdata text": ('<span itemprop="email">orders@wrenfield.works</span>',
                           {"orders@wrenfield.works": "jsonld"}),
        "punycode": ("<p>orders@xn--wrenfld-5za.works</p>",
                     {"orders@xn--wrenfld-5za.works": "text"}),
        "plus addressing": ("<p>orders+trade@wrenfield.works</p>",
                            {"orders+trade@wrenfield.works": "text"}),
        "mov gTLD": ("<p>reels@wrenfield.mov</p>", {"reels@wrenfield.mov": "text"}),
        "zip gTLD": ("<p>packs@wrenfield.zip</p>", {"packs@wrenfield.zip": "text"}),
        "entities": ("<p>orders&#64;wrenfield&#46;works</p>",
                     {"orders@wrenfield.works": "text"}),
        "percent at": ("<p>orders%40wrenfield.works</p>",
                       {"orders@wrenfield.works": "deobfuscated"}),
        "noscript": ("<noscript>orders@wrenfield.works</noscript>",
                     {"orders@wrenfield.works": "text"}),
        "template": ("<template>orders@wrenfield.works</template>",
                     {"orders@wrenfield.works": "text"}),
        "aria-label": ('<a aria-label="orders@wrenfield.works" href="/c">c</a>',
                       {"orders@wrenfield.works": "text"}),
        "og meta": ('<meta property="og:description" content="orders@wrenfield.works">',
                    {"orders@wrenfield.works": "text"}),
        "svg title": ("<svg><title>orders@wrenfield.works</title></svg>",
                      {"orders@wrenfield.works": "text"}),
        "rdfa": ('<span property="email">orders@wrenfield.works</span>',
                 {"orders@wrenfield.works": "text"}),
        "print css": ('<style>@media print{.f:after{content:"orders@wrenfield.works"}}'
                      '</style>', {"orders@wrenfield.works": "text"}),
    }
    for name, (html, want) in cases.items():
        got = E._scan_page(html, "x")
        assert got == want, (name, got, want)
    print("ordinary channels still yield: OK (%d)" % len(cases))


def test_cloudflare_and_fromcharcode_still_decode():
    address = "orders@wrenfield.works"
    payload = ("%02x" % 0x5c) + "".join("%02x" % (ord(c) ^ 0x5c) for c in address)
    for html in ('<a class="__cf_email__" data-cfemail="%s">[protected]</a>' % payload,
                 '<a href="/cdn-cgi/l/email-protection#%s">email</a>' % payload):
        assert E._scan_page(html, "x") == {address: "cfemail"}, html
    codes = ",".join(str(ord(c)) for c in address)
    assert E._scan_page("<script>String.fromCharCode(%s)</script>" % codes, "x") == \
        {address: "js"}
    hexcodes = ",".join("0x%02x" % ord(c) for c in address)
    assert E._scan_page("<script>String.fromCharCode(%s)</script>" % hexcodes, "x") == \
        {address: "js"}
    print("cloudflare + fromCharCode still decode: OK")


def test_socials_and_phones_still_come_back():
    html = ('<a href="https://www.facebook.com/wrenfieldironworks">fb</a>'
            '<a href="https://instagram.com/wrenfield.works">ig</a>'
            '<a href="https://uk.linkedin.com/company/wrenfield">li</a>'
            '<a href="https://x.com/wrenfieldworks">x</a>'
            '<a href="https://www.youtube.com/@wrenfieldworks">yt</a>'
            '<a href="tel:+44 161 496 0122">call</a>')
    socials = E._scan_socials(html)
    assert all(socials[k] for k in ("facebook", "instagram", "linkedin",
                                   "twitter", "youtube")), socials
    assert E._scan_phones(html), "phone lost"
    print("socials and phones still come back: OK")


def test_a_multi_page_crawl_still_finds_the_contact_page_address():
    home = ('<html><body><h1>Wrenfield Ironworks</h1>'
            '<p>Gates forged at the old mill since 1954.</p>'
            '<a href="/about/">About</a> <a href="/contact/">Contact us</a>'
            '</body></html>')
    contact = ('<html><body><h1>Contact</h1>'
               '<p>Trade orders: orders (at) wrenfield (dot) works</p>'
               '<p>Our yard at 8 Foundry Row opens at seven.</p></body></html>')
    about = ('<html><body><p>Dot Rennie lit this forge at first light for '
             'thirty years. An Echo Dot plays the radio at the bench.</p></body></html>')
    with _Pages({"https://wrenfield.works": home,
                 "https://wrenfield.works/contact/": contact,
                 "https://wrenfield.works/about/": about}) as stub:
        site = E.harvest_site("wrenfield.works", max_pages=4, verify_dns=False)
    assert "https://wrenfield.works/contact/" in stub.asked, stub.asked
    assert site["best_email"] == "orders@wrenfield.works", site["emails"]
    assert [r["email"] for r in site["emails"]] == ["orders@wrenfield.works"], \
        site["emails"]
    assert site["emails"][0]["source"] == "https://wrenfield.works/contact/"
    print("multi-page crawl still finds /contact: OK")


def test_the_free_mail_fallback_still_reaches_the_owner():
    page = ('<html><body><p>Ring the shop or write to '
            'wrenfieldforge@gmail.com any day.</p></body></html>')
    ranked = E._rank_emails({"https://wrenfield.works/contact/": E._scan_page(page)},
                            "wrenfield.works")
    assert [r["email"] for r in ranked] == ["wrenfieldforge@gmail.com"], ranked
    print("free-mail fallback still reaches the owner: OK")


def test_junk_and_role_mailboxes_are_still_scored_as_before():
    page = ('<p>postmaster@wrenfield.works</p><p>careers@wrenfield.works</p>'
            '<p>privacy@wrenfield.works</p><p>orders@wrenfield.works</p>'
            '<p>dot.rennie@wrenfield.works</p>')
    ranked = E._rank_emails({"https://wrenfield.works/contact/": E._scan_page(page)},
                            "wrenfield.works")
    got = [r["email"] for r in ranked]
    assert "postmaster@wrenfield.works" not in got, got
    assert got[0] in ("dot.rennie@wrenfield.works", "orders@wrenfield.works"), got
    print("junk and role scoring unchanged: OK (%s)" % ", ".join(got))


if __name__ == "__main__":
    bad = all_fabrications()
    print("fabrications: %d" % len(bad))
    for name, address in bad:
        print("  %-28s -> %s" % (name, address))
