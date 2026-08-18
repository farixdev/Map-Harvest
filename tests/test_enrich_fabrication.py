"""Round-five adversarial corpus for core.enrich: nothing may be invented.

The rule this file enforces is narrow and absolute: **for any input, every
address returned must be one the page actually states.** A miss is acceptable.
A plausible wrong address is not — it becomes `best_email`, exports to the CSV
and gets cold-emailed, and the business never learns why.

Three independent batteries:

* `FRESH_CORPUS` — 170 lines of real small-business web copy in five languages
  and four case styles, carrying `at` and `dot` in every ordinary position
  (business names, the noun, dot-matrix, Echo Dot, `Dot` as a person, street
  addresses, times, prices, `at` as a preposition). Zero addresses may be
  minted from any of it. This is the shape four previous rounds fixed, and it
  is asserted to still *carry* that shape so the zero keeps meaning something.
* `ATTACK_SURFACES` — every other channel that could invent one: percent
  encoding, punycode, JSON escapes, split tags, comments, zero-width
  characters, hidden decoys, CSS assembly, JS concatenation, srcset, data:
  URIs, plus-addressing, and `@` written as ordinary shorthand for "at".
* `SAFE_CASES` — the addresses that must still be found, so a fix cannot buy
  the zero by deleting the feature.

No line here is reused from `tests/test_enrich_email.py`.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import enrich as E  # noqa: E402


class _StubFetch:
    """Replace the module's single network seam with a fixed {url: html} map."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.calls = []
        self._real = E._fetch_page

    def __enter__(self):
        E._fetch_page = self._fetch
        return self

    def __exit__(self, *exc):
        E._fetch_page = self._real
        return False

    def _fetch(self, url, timeout=8.0):
        self.calls.append(url)
        html = self.pages.get(url) or self.pages.get(url.rstrip("/"))
        if html is None:
            return "", "", "http 404"
        return url, html, ""


# ══ Battery 1: ordinary copy, five languages, four case styles ══
#
# `at` and `dot` in every position a small business really writes them. None of
# these lines states an address, so the correct yield from all 170 is zero.

EN_LOWER = (
    "we still run a dot matrix printer at the back for carbon copies.",
    "ask for dot at the trade counter, she knows the whole stock list.",
    "our little dot of a shop sits at the far end of the arcade.",
    "the echo dot on the shelf is a demo unit and is not for sale.",
    "every sample board at dot and dash interiors is made in house.",
    "look for the red dot at the top of each clearance tag.",
    "opening times at the mill are ten until four, dot on.",
    "the paint at dot finish decorators is mixed while you wait.",
    "there is a dot of solder at every joint we make.",
    "our bench at 14 st. andrews lane opens at half eight.",
    "prices at the counter start at 4.50 for a flat white.",
    "dot hemming has cut hair at this chair for thirty one years.",
    "we close at noon on the last friday of the month.",
    "look for the blue dot at the door of the old dairy.",
    "the kiln at quarry dot forge is fired on wednesdays.",
    "the lathe at birch dot joinery was rebuilt at our own bench.",
    "sign in at the gate and wait at the yellow dot.",
    "a dot of glue at each corner holds the frame square.",
    "the counter at slate dot provisions closes at three on sundays.",
    "dot green took over the round at heath dot dairy in 1999.",
    "we sharpen at the wheel, never at the belt.",
    "one dot at the hem marks a repair we have already made.",
    "the ledger at the desk is still ruled by dot and line.",
    "trade prices at the hatch start at 18.00 the metre.",
)

EN_TITLE = (
    "Dot Matrix Ribbons Still In Stock At The Old Print Works",
    "Meet Dot Harrigan, Our Front Of House Since 1996",
    "Echo Dot Setup Clinics At The Library Every Thursday",
    "Dot Grid Notebooks At Half Price Until Friday",
    "Free Delivery At Our Warehouse On St. Vincent Street",
    "Book A Table At Dot And Anchor From Six O'Clock",
    "Every Loaf At Dot Bakehouse Is Proved Overnight",
    "Join Us At The Yard At Nine For The Saturday Market",
    "Dot Grid Paper Cut To Size At The Counter",
    "Our Fitter Will Call At Your Address Within The Hour",
    "Repairs At The Bench Start At Twenty Pounds",
    "Ask At Reception For The Dot Loyalty Card",
    "The Green Dot At The Till Means Locally Sourced",
    "Trade Accounts Opened At Dot Supplies In One Visit",
    "Dot Matrix Forms Printed At Our Works On Canal St. Daily",
    "Collect At The Depot At Half Past Five",
    "Dot Weaver Has Managed The Floor At This Branch Since 2003",
    "An Echo Dot In Every Fitting Room At The New Store",
    "Quotes At The Door Start At Fifty Pounds",
    "Find The White Dot At The Base Of Every Piece We Fire",
)

EN_CAPS = (
    "DOT MATRIX SPARES AVAILABLE AT THE TRADE DESK",
    "MEET DOT AT THE FRONT COUNTER FROM SEVEN",
    "ECHO DOT BUNDLES IN STORE AT BOTH BRANCHES",
    "CLEARANCE AT DOT AND SONS ENDS AT MIDNIGHT",
    "WE ARE AT 42 QUEEN ST. UNIT B",
    "ALL REPAIRS AT OUR BENCH CARRY A DOT SEAL",
    "PRICES AT THE DOOR START AT 6.99",
    "COLLECT AT THE DEPOT ANY DAY AT NOON",
    "DOT GRID PADS AT TWO FOR ONE THIS WEEK",
    "NO APPOINTMENT NEEDED AT THE DOT WALK IN CLINIC",
    "OUR VAN IS AT THE MARKET AT DAWN",
    "LOOK FOR THE GREEN DOT AT THE TILL",
    "SERVICING AT HARBOUR DOT MARINE BOOKS UP FAST",
    "ASK AT THE DESK ABOUT DOT MATRIX PAPER",
    "DOT WILLIS RUNS THE COUNTER AT OUR SECOND BRANCH",
    "EVERY ORDER AT THE HATCH IS CHECKED AT DISPATCH",
    "THE WORKSHOP AT KILN DOT POTTERY FIRES AT DAWN",
    "PARKING AT THE REAR IS FREE AFTER 6.00",
    "A BLUE DOT AT THE SPINE MEANS REFERENCE ONLY",
    "CALL AT THE OFFICE AT 9 BRIDGE ST. FOR KEYS",
)

EN_SENTENCE = (
    "Dot has run the tea room at the corner since 1988.",
    "The dot matrix invoices at our depot print in triplicate.",
    "An Echo Dot sits at the till so we can play requests.",
    "Look for the small dot at the base of each mug we throw.",
    "Our office at 7 Cathedral St. is open at nine.",
    "Every quote at Dot Glazing is free and takes ten minutes.",
    "The dot on the dial at the front should sit at twelve.",
    "You can pay at the counter or at the door.",
    "Dot and her sister have baked at this oven for decades.",
    "Deliveries at the rear at eight, collections at four.",
    "A single dot at the end of the line means the job is closed.",
    "We stock ink at the counter for dot matrix machines.",
    "Rates at the workshop at ferry dot works start at 35.00 an hour.",
    "Ask at the hatch and Dot will bring it out at once.",
    "The bindery at vellum dot press stitches by hand.",
    "Dot Prescott trained at the college on Union St. in 1979.",
    "A dot of oil at the hinge stops the squeak.",
    "Our fitters arrive at eight and finish at four.",
    "The Echo Dot at the counter takes requests from the queue.",
    "Samples at the desk are free, and a full board costs 6.00.",
)

FR_LINES = (
    "l'atelier dot et fils restaure des meubles depuis 1972.",
    "la dot de la mariée figurait encore au contrat de 1908.",
    "notre imprimante matricielle imprime point par point, dot par dot.",
    "Madame Dot Ferrand tient la caisse tous les samedis matin.",
    "Nous Sommes Au 12 Rue St. Martin, Ouvert Dès Neuf Heures",
    "L'ENCEINTE ECHO DOT EST EN DÉMONSTRATION AU RAYON HIFI",
    "LES PRIX COMMENCENT À 12,50 EUROS AU COMPTOIR",
    "Le chat de l'atelier dort sur la machine à coudre.",
    "Chaque achat au magasin donne droit à un café.",
    "L'atelier dot & cie répare les stores depuis trois générations.",
    "NOTRE DÉPÔT SE TROUVE AU 8 AVENUE DES TILLEULS",
    "le point rouge, ce petit dot sur l'étiquette, signale une fin de série.",
    "Réservez Une Table Au Bistrot Dot Avant Dix-Neuf Heures",
    "Les travaux au 3 place de la Gare s'achèvent en mars.",
    "un dot de colle à chaque angle suffit largement.",
    "Le Rendez-Vous Au Salon Dot Se Prend En Ligne Ou Au Téléphone",
    "LE COMPTOIR DE L'ATELIER DOT OUVRE À SEPT HEURES",
    "Monsieur Dot Lavigne tient la quincaillerie depuis 1986.",
    "les tarifs au comptoir commencent à 6,50 le kilo.",
    "Notre Dépôt Au 21 Chemin Des Vignes Ferme À Midi",
    "un chat dort toujours sur le comptoir de l'atelier.",
    "LA BOULANGERIE AU MOULIN DOT FARINE CUIT AU FEU DE BOIS",
)

DE_LINES = (
    "die werkstatt dot und partner sitzt am alten hafen.",
    "unser nadeldrucker druckt noch immer dot für dot.",
    "Frau Dot Berger führt das Büro seit 1994.",
    "Echo Dot Beratung In Unserer Filiale Am Markt",
    "DOT MATRIX ERSATZTEILE SIND AM LAGER VORRÄTIG",
    "DIE PREISE BEGINNEN BEI 12,50 EURO AN DER KASSE",
    "Unser Laden liegt in der Bahnhofstr. 14 im zweiten Stock.",
    "der rote dot am etikett bedeutet reduziert.",
    "Wir Öffnen Am Samstag Um Acht Uhr Dreissig",
    "Herr Dot bringt die Ware am Dienstag persönlich vorbei.",
    "DIE SCHREINEREI DOT UND SÖHNE ARBEITET SEIT 1958",
    "am eingang finden sie einen grünen dot auf dem boden.",
    "Die Werkstatt Am Kanal Dot Schmiede Nimmt Auftraege An",
    "Termine am Vormittag sind meist noch frei.",
    "EIN DOT AUF DEM DISPLAY ZEIGT DEN BETRIEB AN",
    "Beratung Am Telefon Ist Bei Uns Kostenlos",
    "die tischlerei am ufer dot holzwerk liefert am freitag.",
    "Herr Dot Wagner schleift Messer seit vierzig Jahren.",
    "UNSERE PREISE STARTEN BEI 7,90 EURO PRO STUECK",
    "Ein Echo Dot Steht Am Tresen Fuer Die Musik",
    "der laden in der muehlenstr. 3 hat am montag geschlossen.",
    "EIN DOT KLEBER AN JEDER ECKE GENUEGT VOLLKOMMEN",
)

ES_LINES = (
    "el taller dot y hermanos repara persianas desde 1981.",
    "TU ECHO DOT CONFIGURADO GRATIS EN NUESTRA TIENDA",
    "Doña Dot atiende el mostrador por las mañanas.",
    "Estamos en la Avda. de la Constitución, número 3.",
    "los precios empiezan en 9,90 euros en barra.",
    "La Impresora Matricial Imprime Punto A Punto, Dot A Dot",
    "EL PUNTO ROJO EN LA ETIQUETA INDICA ÚLTIMAS UNIDADES",
    "Cada compra en el obrador incluye una degustación.",
    "el obrador dot y compañía amasa a las cinco de la mañana.",
    "Reserve Su Mesa En El Restaurante Dot Antes De Las Ocho",
    "ABRIMOS DE LUNES A SÁBADO A LAS NUEVE EN PUNTO",
    "un dot de pegamento en cada esquina es suficiente.",
    "La Ferretería Dot Hermanos Corta Llaves En El Acto",
    "Nuestro almacén en la calle Mayor, 21, abre a las siete.",
    "EL TALLER DOT FORJA TRABAJA EL HIERRO A MANO",
    "Puede pagar en caja o en la puerta al recoger.",
    "don dot vidal lleva el mostrador desde hace treinta años.",
    "LA IMPRESORA DE AGUJAS SIGUE IMPRIMIENDO DOT A DOT",
    "Nuestro Taller En La Calle Mayor Dot Herrería Abre A Las Siete",
    "un dot de aceite en la bisagra evita el chirrido.",
    "Los Sábados Cerramos A Las Dos De La Tarde",
    "el catálogo en el mostrador cuesta 4,00 euros.",
)

NL_LINES = (
    "de werkplaats dot en zonen slijpt messen sinds 1965.",
    "Mevrouw Dot Bakker staat elke zaterdag achter de toonbank.",
    "Bezoek Ons Aan De Kerkstr. 8 In Het Oude Centrum",
    "ONZE WINKEL AAN DE DORPSSTRAAT IS OPEN VANAF ACHT UUR",
    "de matrixprinter drukt nog altijd dot voor dot af.",
    "Een Echo Dot Staat Bij De Kassa Voor De Muziek",
    "PRIJZEN BEGINNEN BIJ 8,50 EURO AAN DE BAR",
    "het rode dot op het label betekent opruiming.",
    "De Smederij Dot En Partners Werkt Op Afspraak",
    "Wij sluiten op vrijdag om vier uur.",
    "EEN DOT LIJM OP ELKE HOEK IS GENOEG",
    "de bakkerij aan de haven dot ambacht bakt om vijf uur.",
    "Meneer Dot komt de meting dinsdag zelf doen.",
    "ONZE LOODS AAN DE INDUSTRIEWEG 14 IS DAGELIJKS OPEN",
    "Vraag Aan De Balie Naar De Dot Klantenkaart",
    "afspraken in de ochtend zijn meestal nog vrij.",
    "DE TIMMERWERKPLAATS AAN DE VAART DOT HOUTWERK LEVERT OP VRIJDAG",
    "Meneer Dot Visser slijpt scharen al veertig jaar.",
    "onze prijzen beginnen bij 7,25 euro per stuk.",
    "Het Kantoor Aan De Molenstr. 3 Is Maandag Gesloten",
    "EEN BLAUWE DOT OP DE RUG BETEKENT ALLEEN TER INZAGE",
    "de bezorger komt om acht uur en vertrekt om vier uur.",
)

FRESH_CORPUS = (EN_LOWER + EN_TITLE + EN_CAPS + EN_SENTENCE
                + FR_LINES + DE_LINES + ES_LINES + NL_LINES)

# The shape four rounds were spent killing: `word at word dot tld`. Asserted so
# a later edit cannot quietly rewrite the corpus into something harmless and
# leave a green test guarding nothing.
FABRICATING_SHAPE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9][A-Za-z0-9._%+\-]{0,62}"
    r"\s+at\s+(?:[A-Za-z0-9][A-Za-z0-9\-]{0,61}\s+dot\s+)+[A-Za-z]{2,24}"
    r"(?![A-Za-z0-9\-])", re.I)


def test_the_fresh_corpus_is_large_multilingual_and_dangerous():
    assert len(FRESH_CORPUS) >= 150, len(FRESH_CORPUS)
    assert len(set(FRESH_CORPUS)) == len(FRESH_CORPUS), "duplicate lines"
    for group in (EN_LOWER, EN_TITLE, EN_CAPS, EN_SENTENCE,
                  FR_LINES, DE_LINES, ES_LINES, NL_LINES):
        assert len(group) >= 14, len(group)
    # No line may already contain a literal address — the zero has to be earned.
    for line in FRESH_CORPUS:
        assert "@" not in line, line
    carrying = [l for l in FRESH_CORPUS if FABRICATING_SHAPE.search(l)]
    assert len(carrying) >= 6, carrying
    assert sum("dot" in l.lower() for l in FRESH_CORPUS) >= 80
    assert sum(re.search(r"\bat\b", l, re.I) is not None for l in FRESH_CORPUS) >= 50
    print("fresh corpus: OK (%d lines, %d carry the shape)"
          % (len(FRESH_CORPUS), len(carrying)))


def test_zero_addresses_are_minted_from_the_fresh_corpus():
    """170 lines of real copy. The correct yield is zero, line by line."""
    minted = {}
    for line in FRESH_CORPUS:
        found = E._scan_page("<p>%s</p>" % line, "https://dotbakehouse.shop/")
        if found:
            minted[line] = found
    assert minted == {}, minted
    print("corpus mints nothing, line by line: OK (%d lines)" % len(FRESH_CORPUS))


def test_zero_addresses_are_minted_from_the_corpus_in_bulk():
    """The way copy actually arrives: one page, every line, all four styles."""
    page = "<html><body>%s</body></html>" % "".join(
        "<p>%s</p>" % l for l in FRESH_CORPUS)
    assert E._scan_page(page, "https://dotbakehouse.shop/") == {}
    assert E.extract_contacts(page, "https://dotbakehouse.shop")["email"] == ""
    with _StubFetch({"https://dotbakehouse.shop": page}):
        site = E.harvest_site("dotbakehouse.shop", verify_dns=False)
    assert site["reachable"] is True
    assert site["best_email"] == "" and site["emails"] == [], site["emails"]
    print("corpus mints nothing in bulk: OK")


def test_one_real_address_still_survives_the_whole_corpus():
    """The zero must not be bought by refusing everything."""
    page = "<html><body>%s<p>Trade: orders (at) dotbakehouse (dot) shop</p></body></html>" % (
        "".join("<p>%s</p>" % l for l in FRESH_CORPUS))
    found = E._scan_page(page, "https://dotbakehouse.shop/")
    assert found == {"orders@dotbakehouse.shop": "deobfuscated"}, found
    print("real address survives the corpus: OK")


# ══ Battery 2: every other surface that could invent an address ══
#
# (name, html, {addresses the page genuinely states}). Anything returned that
# is not in that set is a fabrication and is counted.

ATTACK_SURFACES = [
    # `@` written as ordinary shorthand for the word "at" — handles, venues,
    # credits, times, prices. None of these is an address.
    ("handle-instagram", '<p>Follow us @acmepizza.studio on Instagram</p>', set()),
    ("handle-tag", '<p>Tag us @bright.design for a repost</p>', set()),
    ("handle-sub", '<p>DM us @shop.acme.co.uk any time</p>', set()),
    ("venue-street", "<p>Join us @ St. Mary's Hall on Tuesday</p>", set()),
    ("credit-footer", '<p>&copy; 2026 Dot &amp; Dash. Built @ north.studio</p>', set()),
    ("credit-design", '<p>Design @ mono.works &middot; Photography @ ridge.photo</p>', set()),
    ("photos-host", '<p>Photos @ flickr.com/acmebakery</p>', set()),
    ("handle-nl", '<p>Volg ons @debakkerij.amsterdam</p>', set()),
    ("handle-fr", '<p>Suivez-nous @maison.paris</p>', set()),
    ("handle-de", '<p>Folgen Sie uns @werkstatt.berlin</p>', set()),
    ("handle-es", '<p>S&iacute;guenos @taller.madrid</p>', set()),
    ("handle-nbsp", '<p>Follow us\u00a0@acmepizza.studio</p>', set()),
    ("handle-newline", '<p>Follow us\n\n   @acmepizza.studio</p>', set()),
    ("handle-pct40", '<p>Follow us %40acmepizza.studio</p>', set()),
    ("handle-comment", '<!-- Follow us @acmepizza.studio -->', set()),
    ("handle-hidden", '<div style="display:none">Follow us @acmepizza.studio</div>', set()),
    ("handle-css", '<style>.m:before{content:"Follow us @acmepizza.studio"}</style>', set()),
    ("handle-jsonesc", '<script>{"bio":"\\u003eFollow us @acmepizza.studio"}</script>', set()),
    ("handle-punycode", '<p>Besuchen Sie uns @xn--mnchen-3ya.de</p>', set()),
    ("handle-mov", '<p>Watch us @shorts.mov</p>', set()),
    ("handle-zip", '<p>Grab us @files.zip</p>', set()),
    ("handle-plus", '<p>Follow us @acmepizza.studio+news</p>', set()),
    ("handle-entity-at", '<p>Follow us &#64;acmepizza.studio</p>', set()),
    ("handle-entity-dot", '<p>Follow us @acmepizza&#46;studio</p>', set()),
    ("srcset-spaced", '<img srcset="/i/shot @acme.mov 2x">', set()),
    ("datauri-svg",
     '<img src="data:image/svg+xml,%3Csvg%3EFollow us @acme.pizza%3C/svg%3E">', set()),
    ("time-shorthand", '<p>Doors @ 7.30pm sharp</p>', set()),
    ("time-shorthand2", '<p>Open @ 9.00am daily</p>', set()),
    ("price-shorthand", '<p>Sourdough @ 3.50 a loaf</p>', set()),
    ("rate-shorthand", '<p>Billed @ 1.5 hours minimum</p>', set()),
    ("suite-shorthand", '<p>Suite 4 @ 100 King St. West</p>', set()),

    # A split *inside* the local part must lose the address, not shorten it.
    # Escapes, never literals: an invisible codepoint pasted into a source file
    # is exactly what an editor or a formatter strips without telling anyone,
    # and a stripped codepoint turns this green while fixing nothing.
    ("zw-zwsp", '<p>in\u200bfo@acmeroofing.ca</p>', set()),
    ("zw-zwnj", '<p>in\u200cfo@acmeroofing.ca</p>', set()),
    ("zw-zwj", '<p>in\u200dfo@acmeroofing.ca</p>', set()),
    ("zw-shy", '<p>in\u00adfo@acmeroofing.ca</p>', set()),
    ("zw-bom", '<p>in\ufefffo@acmeroofing.ca</p>', set()),
    ("zw-wordjoiner", '<p>in\u2060fo@acmeroofing.ca</p>', set()),
    ("zw-lrm", '<p>in\u200efo@acmeroofing.ca</p>', set()),
    ("zw-nbsp", '<p>in\u00a0fo@acmeroofing.ca</p>', set()),
    ("zw-entity-zwnj", '<p>in&zwnj;fo@acmeroofing.ca</p>', set()),
    ("zw-entity-shy", '<p>in&shy;fo@acmeroofing.ca</p>', set()),
    ("zw-entity-num", '<p>in&#8203;fo@acmeroofing.ca</p>', set()),
    ("decoy-span", '<p>in<span style="display:none">XX</span>fo@acmeroofing.ca</p>', set()),
    ("decoy-empty-tag", '<p>in<b></b>fo@acmeroofing.ca</p>', set()),
    ("decoy-comment", '<p>in<!--x-->fo@acmeroofing.ca</p>', set()),
    ("decoy-nospam", '<p>i<span class="hidden">nospam</span>nfo@acmeroofing.ca</p>', set()),

    # Splits that already fail closed — held here so they stay that way.
    ("split-at-tag", '<p>info<span>@</span>acmeroofing.ca</p>', set()),
    ("split-wbr", '<p>info@<wbr>acmeroofing.ca</p>', set()),
    ("split-local-tail", '<p>inf<span>o</span>@acmeroofing.ca</p>', set()),
    ("split-whole-local", '<p><span>info</span>@acmeroofing.ca</p>', set()),

    # Percent encoding: the decoded address or nothing, never both forms.
    ("pct-path", '<p>See /profile/info%40acme.ca/ for details</p>', {"info@acme.ca"}),
    ("pct-mailto-plus", '<a href="mailto:info%2Bweb@acme.ca">mail</a>',
     {"info+web@acme.ca"}),
    ("pct-double", '<p>info%2540acme.ca</p>', set()),
    ("pct-coupon", '<p>Use code SAVE%40OFF at checkout</p>', set()),
    ("pct-query", '<p>?utm_source=news%40mailer</p>', set()),

    # Punycode, never truncated at the hyphen.
    ("puny-text", '<p>info@xn--mnchen-3ya.de</p>', {"info@xn--mnchen-3ya.de"}),
    ("puny-bracketed", '<p>info (at) xn--mnchen-3ya (dot) de</p>',
     {"info@xn--mnchen-3ya.de"}),
    ("puny-subdomain", '<p>info@shop.xn--p1ai</p>', {"info@shop.xn--p1ai"}),

    # JSON / JS escape prefixes must not glue onto the local part.
    ("jsonesc-gt", '<script>var s="\\u003einfo@acme.ca\\u003c";</script>',
     {"info@acme.ca"}),
    ("jsonesc-quot", '<script>{"e":"\\u0022sales@acme.ca\\u0022"}</script>',
     {"sales@acme.ca"}),

    # Assembly the scanner deliberately does not attempt: miss, never invent.
    ("css-split-rules",
     '<style>.a:after{content:"info"}.b:after{content:"@acme.ca"}</style>', set()),
    ("css-unicode-escape", '<style>.a:after{content:"info\\0040acme.ca"}</style>', set()),
    ("js-concat", '<script>var u="info",d="acme.ca",e=u+"@"+d;</script>', set()),
    ("js-concat-split", '<script>document.write("inf"+"o@acme"+".ca")</script>', set()),
    ("js-atob", '<script>atob("aW5mb0BhY21lLmNh")</script>', set()),

    # Asset filenames are files, not mailboxes.
    ("srcset-density", '<img srcset="/i/logo@2x.png 2x, /i/logo@3x.png 3x">', set()),
    ("asset-jpg", '<img src="/i/team@acme.jpg">', set()),
    ("asset-mov-density", '<video src="/clips/promo@2x.mov"></video>', set()),
    ("asset-webp-spaced", '<img src="/i/hero @2x.webp">', set()),
    ("datauri-b64", '<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==">', set()),

    # Genuinely present, even if hidden — found is correct here, not invented.
    ("comment-real-address", '<!-- old: sales@old-acme.ca -->', {"sales@old-acme.ca"}),
    ("hidden-real-address",
     '<div style="display:none"><a href="mailto:t@trap.acme.ca">x</a></div>',
     {"t@trap.acme.ca"}),
]


def fabrications() -> list:
    """Every address returned that the page does not state. The whole point."""
    out = []
    for name, html, correct in ATTACK_SURFACES:
        got = set(E._scan_page(html, "https://acme.ca/"))
        for bad in sorted(got - correct):
            out.append((name, bad))
    return out


def test_no_surface_invents_an_address():
    found = fabrications()
    assert found == [], (
        "%d fabricated addresses across %d surfaces:\n%s"
        % (len(found), len({n for n, _ in found}),
           "\n".join("  %-22s -> %s" % (n, a) for n, a in found)))
    print("no surface invents an address: OK (%d surfaces)" % len(ATTACK_SURFACES))


def test_a_fabricated_address_would_be_exported_as_best_email():
    """Why the bar is zero: a mint is not a stray dict key, it is the answer.

    Both live root causes land on the site's own registrable domain, take the
    +50 same-domain bonus and the +10 contact-page bonus, and become the single
    value the CSV carries.
    """
    handle = '<html><body><p>Follow us @acmepizza.studio for specials.</p></body></html>'
    truncated = '<html><body><p>Email in\u200bfo@acmeroofing.ca for a quote.</p></body></html>'
    for page, domain in ((handle, "acmepizza.studio"), (truncated, "acmeroofing.ca")):
        ranked = E._rank_emails({"https://%s/contact/" % domain: E._scan_page(page)},
                                domain)
        assert ranked == [], ranked
        assert E.extract_contacts(page, "https://%s" % domain)["email"] == ""
    print("no fabricated best_email: OK")


def test_a_handle_on_the_home_page_does_not_contaminate_a_real_crawl():
    """The realistic shape: the address is real, and a phantom rides beside it.

    `best_email` may still be right here, so the harm is quieter — a second row
    in the results table on the business's own domain, which is indistinguish-
    able from a real alternate inbox to whoever reads the export.
    """
    home = ('<html><body><h1>Fernhill Bakery</h1>'
            '<p>Follow us @fernhill.shop for the daily bake.</p>'
            '<a href="/contact/">Contact us</a></body></html>')
    contact = ('<html><body><p>Trade orders: orders (at) fernhill (dot) shop</p>'
               '</body></html>')
    with _StubFetch({"https://fernhill.shop": home,
                     "https://fernhill.shop/contact/": contact}):
        site = E.harvest_site("fernhill.shop", max_pages=4, verify_dns=False)
    assert [r["email"] for r in site["emails"]] == ["orders@fernhill.shop"], site["emails"]
    print("no phantom row in a real crawl: OK")


# ══ Battery 3: the removals must not have taken the safe cases ══

MODERN_GTLDS_2 = (
    "shop", "store", "studio", "design", "works", "supply", "kitchen", "bar",
    "pub", "garden", "market", "school", "care", "clinic", "law", "finance",
    "build", "repair", "cleaning", "movie",
)


def test_bracketed_obfuscation_across_twenty_modern_gtlds():
    for tld in MODERN_GTLDS_2:
        for raw, want in (
                ("orders (at) fernhill (dot) %s" % tld, "orders@fernhill.%s" % tld),
                ("orders [at] fernhill [dot] %s" % tld, "orders@fernhill.%s" % tld),
                ("orders {at} fernhill {dot} %s" % tld, "orders@fernhill.%s" % tld)):
            got = E._scan_page("<p>%s</p>" % raw, "x")
            assert got == {want: "deobfuscated"}, (raw, got)
    print("bracketed obfuscation across %d gTLDs: OK" % len(MODERN_GTLDS_2))


def test_the_ordinary_channels_still_yield_their_address():
    cases = {
        "mailto": ('<a href="mailto:orders@fernhill.shop">Order</a>',
                   {"orders@fernhill.shop": "mailto"}),
        "plain text": ("<p>Write to orders@fernhill.shop any time.</p>",
                       {"orders@fernhill.shop": "text"}),
        "jsonld": ('<script type="application/ld+json">'
                   '{"@type":"Bakery","email":"orders@fernhill.shop"}</script>',
                   {"orders@fernhill.shop": "jsonld"}),
        "punycode domain": ("<p>orders@xn--frnhill-5za.shop</p>",
                            {"orders@xn--frnhill-5za.shop": "text"}),
        "plus addressing": ("<p>orders+web@fernhill.shop</p>",
                            {"orders+web@fernhill.shop": "text"}),
        "mov gTLD": ("<p>bookings@fernhill.movie and cuts@fernhill.mov</p>",
                     {"bookings@fernhill.movie": "text", "cuts@fernhill.mov": "text"}),
        "zip gTLD": ("<p>orders@fernhill.zip</p>", {"orders@fernhill.zip": "text"}),
        # Entity-decoded to a literal address, so the honest provenance is
        # "text" — the page does state it outright, once unescaped.
        "entities": ("<p>orders&#64;fernhill&#46;shop</p>",
                     {"orders@fernhill.shop": "text"}),
        "percent at": ("<p>orders%40fernhill.shop</p>",
                       {"orders@fernhill.shop": "deobfuscated"}),
    }
    for name, (html, want) in cases.items():
        got = E._scan_page(html, "x")
        assert got == want, (name, got)
    print("ordinary channels still yield: OK (%d)" % len(cases))


def test_cloudflare_cfemail_still_decodes():
    address = "orders@fernhill.shop"
    payload = ("%02x" % 0x3b) + "".join("%02x" % (ord(c) ^ 0x3b) for c in address)
    for html in ('<a class="__cf_email__" data-cfemail="%s">[protected]</a>' % payload,
                 '<a href="/cdn-cgi/l/email-protection#%s">email</a>' % payload):
        assert E._scan_page(html, "x") == {address: "cfemail"}, html
    print("cloudflare cfemail still decodes: OK")


def test_a_multi_page_crawl_finds_the_address_on_contact():
    home = ('<html><body><h1>Fernhill Bakery</h1>'
            '<p>Sourdough @ 3.50 a loaf, sliced at the counter.</p>'
            '<a href="/about/">About</a> <a href="/contact/">Contact us</a>'
            '</body></html>')
    contact = ('<html><body><h1>Contact</h1>'
               '<p>Trade orders: orders (at) fernhill (dot) shop</p>'
               '<p>Our counter at 4 Mill St. opens at seven.</p></body></html>')
    about = ('<html><body><p>Dot Fernhill opened the bakery at the old mill '
             'in 1994. An Echo Dot plays the radio at the bench.</p></body></html>')
    pages = {"https://fernhill.shop": home,
             "https://fernhill.shop/contact/": contact,
             "https://fernhill.shop/about/": about}
    with _StubFetch(pages) as stub:
        site = E.harvest_site("fernhill.shop", max_pages=4, verify_dns=False)
    assert "https://fernhill.shop/contact/" in stub.calls, stub.calls
    assert site["best_email"] == "orders@fernhill.shop", site["emails"]
    assert [r["email"] for r in site["emails"]] == ["orders@fernhill.shop"], site["emails"]
    assert site["emails"][0]["source"] == "https://fernhill.shop/contact/"
    print("multi-page crawl finds /contact: OK")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
