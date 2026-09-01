"""Local, deterministic, zero-token website audit — the evidence a cold email stands on.

Why this module exists: the model must never see raw HTML. A single business page
is 50-200k tokens of markup that buys nothing a pattern match cannot find, and a
500-lead campaign at that price is unsendable. So every fact in an outreach email
is discovered here, offline and for free, and the model only ever receives
`digest()` — five lines, under 1200 characters, roughly 300 tokens.

The output that earns its keep is `gaps`. Each detected gap carries the Auto Army
services that close it, resolved through `core.templates.AUTO_ARMY_SERVICES` so
the pitch can never name a capability the business does not actually sell. A gap
the catalogue has no answer for carries none, and that is what orders the list:
gaps with an offer behind them first, then severity, then catalogue order, so
`gaps[0]` is always a headline the email can follow with an offer.

Detection is deliberately conservative. A wrong "no online booking" in a live
cold email is worse than a missed gap: it tells the reader you did not look. So a
signal fires on a concrete marker — a script host, a link, a schema type, a
printed date — and staleness is never claimed without a date to point at.

`audit_from_html(pages, base_url)` is the pure core and holds every rule, which
keeps the whole catalogue testable from handwritten fixtures. `audit_site()` is a
thin network wrapper that reuses the HTML `core.enrich.harvest_site` already
fetched whenever it is handed any, because downloading the same six pages twice
per lead is the difference between a scrape that finishes and one that does not.

`pages` is a crawl and not a page, and the findings worth most are the ones that
say so: a button headed Book Online whose page turns out to be a contact form, a
quote form fourteen fields deep two clicks in, a services page listing twelve
things with one form under all of them, a price list published as a PDF nobody
has rebuilt since 2019. Every one of those reads HTML that was already fetched
and already parsed — the form blocks `_lead_forms` walks, the list items
`_listed_services` reads, the links `_signals` is looping over anyway — so depth
here costs no request and no second pass over the crawl.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import gzip
import html as _html
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from core.templates import AUTO_ARMY_SERVICES

# ── Service resolution ──

# lower() -> the catalogue's own spelling. Category names count here: a gap is
# sometimes closed by a whole line of work rather than by one item, and
# "CRM & Sales Automation" is how the seller names that line. It is a heading,
# though, so it is a fact about the gap and never copy — `core.templates`
# resolves it to an entry beneath it before a prospect reads the sentence.
_CATALOGUE_INDEX: dict[str, str] = {}
for _category, _names in AUTO_ARMY_SERVICES.items():
    _CATALOGUE_INDEX.setdefault(_category.lower(), _category)
    for _name in _names:
        _CATALOGUE_INDEX.setdefault(_name.lower(), _name)
del _category, _names, _name


def _svc(*names: str) -> list[str]:
    """Catalogue spellings for `names`; anything not in the catalogue is dropped.

    The gap table below *is* the sales pitch, so it resolves every service
    through the catalogue rather than restating it. A service renamed in
    core.templates either follows here or vanishes from the pitch — it can never
    quietly become an offer that is not on the list.
    """
    return [_CATALOGUE_INDEX[n.lower()] for n in names if n.lower() in _CATALOGUE_INDEX]


# ── Gap catalogue ──

# Insertion order is the tie-break for equal severities, so the gaps that convert
# best sit at the top. Titles are lower-case noun phrases because they are dropped
# straight into a sentence ("One thing stands out on acme.ca: no online booking").
#
# `subject_phrase` is the same gap named neutrally, and it is what the subject
# line gets. A title works in the body because the sentence around it does the
# explaining; alone at the top of a stranger's inbox, "a site nobody has touched
# in years at Acme Plumbing Ltd" is a jab and the email is dead before it opens.
# Phrases stay short — the business name follows them inside a 55-character cap.
GAP_CATALOGUE: dict[str, dict] = {
    "no_online_booking": {
        "title": "no online booking", "severity": 3,
        "subject_phrase": "online booking",
        "services": _svc("appointment booking", "Lead Automation"),
    },
    "no_crm_signals": {
        "title": "no CRM behind the form", "severity": 3,
        "subject_phrase": "leads from the contact form",
        "services": _svc("CRM & Sales Automation", "automatically add leads to CRM"),
    },
    "contact_form_only": {
        "title": "a contact form and nothing else", "severity": 3,
        "subject_phrase": "the contact form",
        "services": _svc("AI lead qualification", "automatic follow-ups"),
    },
    "no_lead_capture": {
        "title": "no way to capture a lead", "severity": 3,
        "subject_phrase": "capturing leads from the site",
        "services": _svc("Lead Generation", "Lead Automation"),
    },
    # The site does ask, and what it asks is "write to us". A published address
    # is a lead route, so `no_lead_capture` is only half the story about it: the
    # enquiries arrive, and they arrive as prose in a mailbox with nothing
    # reading them. That is the one shape the catalogue answers by name.
    "email_only_intake": {
        "title": "an inbox doing the intake", "severity": 2,
        # Not "how enquiries come in": the follow-up subject is "one more note
        # on {phrase}", and `_clean_subject` strips a trailing preposition, so
        # that phrase arrived in the inbox as "...on how enquiries come".
        "subject_phrase": "where enquiries land",
        "services": _svc("AI email processing", "AI lead qualification"),
    },
    "no_live_chat": {
        "title": "no live chat", "severity": 2,
        "subject_phrase": "live chat",
        "services": _svc("AI customer-support agents", "AI lead qualification"),
    },
    # A green button that opens a phone. It is a real lead route and the reader
    # is proud of it, so this is never phrased as a fault — it is the one gap in
    # the table whose evidence describes something that already works, and the
    # offer is to stop it needing a person on the other end at nine at night.
    "whatsapp_manual": {
        "title": "a WhatsApp button answered by hand", "severity": 2,
        "subject_phrase": "the WhatsApp button",
        "services": _svc("WhatsApp workflows", "AI customer-support agents"),
    },
    "quote_by_form": {
        "title": "quotes handled by hand", "severity": 2,
        "subject_phrase": "how quotes get handled",
        "services": _svc("AI lead scoring", "AI decision/triage systems"),
    },
    # These two and the two further down are the first findings in this table
    # that cannot be read off a home page. A quote form fourteen fields deep is
    # two clicks in; a services list with nothing under it is on the services
    # page; a price list nobody has reopened since 2019 is behind a download
    # link; a shop with nowhere to leave an address is a fact about every page
    # at once. All four read HTML `harvest_site` had already fetched.
    #
    # Titles here are short for a reason that is not taste: `core.whatsapp`
    # renders one into a sixty-word message with four words of margin, and a
    # nine-word title spent the margin and then the budget.
    "long_intake_form": {
        "title": "a long form before an enquiry", "severity": 2,
        "subject_phrase": "the enquiry form",
        "services": _svc("AI lead qualification", "lead categorization"),
    },
    "services_no_route": {
        "title": "one form under every service", "severity": 2,
        "subject_phrase": "the services list",
        "services": _svc("lead categorization", "lead assignment"),
    },
    "no_analytics": {
        "title": "nothing measuring the site", "severity": 2,
        "subject_phrase": "measuring the site",
        # Not "reporting" beside "automated reports": the pair is one offer said
        # twice, and the second word alone reads as a heading in a list.
        "services": _svc("automated reports", "competitor monitoring"),
    },
    "careers_manual": {
        "title": "careers handled manually", "severity": 2,
        "subject_phrase": "the careers page",
        # Not "HR processes" in front: it is a real catalogue entry, but in the
        # slot that reads "the fix is ___" it names a department rather than a
        # thing that gets built. What the gap is about is CVs arriving to be
        # read and sorted, and a hire that then has to be walked through a week
        # of paperwork by hand.
        "services": _svc("employee onboarding", "AI document/data extraction"),
    },
    "ecommerce_manual": {
        "title": "orders handled by hand", "severity": 2,
        "subject_phrase": "order handling",
        # The spec's "Order automation" is not a catalogue service; the closest
        # real ones are the order workflow item and its parent line.
        "services": _svc("purchase/order workflows", "Business Process Automation"),
    },
    # A shop is two findings, not one. The other is what happens to the basket
    # nobody finished: with no address anywhere on the site there is nothing to
    # send, and the sale is simply gone.
    "cart_no_recovery": {
        "title": "nothing brings a full cart back", "severity": 2,
        "subject_phrase": "shoppers who leave a cart",
        "services": _svc("email campaigns", "automatic follow-ups"),
    },
    "pdf_forms": {
        "title": "paperwork handed out as PDFs", "severity": 2,
        "subject_phrase": "the PDF forms",
        "services": _svc("Document Automation", "PDF/document data extraction"),
    },
    # Not the same finding as `price_opaque`, and the two can never both fire:
    # this business does publish its prices, in a file it last rebuilt years
    # ago. Which is why a linked price list silences `price_opaque` — "not a
    # rate, a range or a starting figure" is a false sentence to a reader whose
    # own home page links one.
    "dated_document": {
        "title": "prices published as an old PDF", "severity": 2,
        "subject_phrase": "the downloadable price list",
        "services": _svc("automatic document generation", "PDF/document data extraction"),
    },
    "multi_location": {
        "title": "several locations, one inbox", "severity": 2,
        "subject_phrase": "running several locations",
        # Same rule as no_analytics, and the same reason: "reporting" beside
        # "automated reports" is one offer said twice. The second slot goes to
        # what the gap is actually about — a message arriving for one branch and
        # having to find its way to that branch.
        "services": _svc("automated reports", "lead assignment"),
    },
    "no_mobile": {
        "title": "no mobile layout", "severity": 2,
        "subject_phrase": "the site on phones",
        # Empty on purpose. The catalogue automates the work behind a business;
        # it does not build websites, and nothing in it makes a desktop layout
        # fit a phone. An offer here would have to be borrowed from an unrelated
        # line, and the reader parses "no mobile layout, so we build approval
        # systems" as a broken mail merge. `_gaps` sorts a gap carrying no
        # services behind every gap that carries one, so this is evidence and a
        # score, and it never becomes the sentence an offer has to follow.
        "services": [],
    },
    # Empty for the reason above: a certificate is hosting work. It earns a row
    # anyway because `has_ssl` was already being measured and no rule read it,
    # and because the operator sorting this list wants to know which of these
    # sites a browser is putting a warning in front of.
    "no_ssl": {
        "title": "a site browsers mark as not secure", "severity": 2,
        "subject_phrase": "how the site is served",
        "services": [],
    },
    "stale_blog": {
        "title": "a blog that has gone quiet", "severity": 1,
        "subject_phrase": "the blog",
        "services": _svc("AI content generation", "content pipelines"),
    },
    "no_social_presence": {
        "title": "no social profiles linked", "severity": 1,
        "subject_phrase": "social profiles",
        "services": _svc("social media workflows", "Marketing Automation"),
    },
    # Only for a site that already prints praise. "You have no reviews" is a
    # guess about a Google listing this module has never seen; "the quotes on
    # your own page were typed in by hand and nothing sends a finished customer
    # to leave a real one" is a fact about the page, and the offer follows from
    # it without anybody having to be told their reputation is thin.
    "no_review_capture": {
        "title": "reviews that never get asked for", "severity": 1,
        "subject_phrase": "asking customers for reviews",
        "services": _svc("automatic follow-ups", "Marketing Automation"),
    },
    "stale_site": {
        "title": "a site nobody has touched in years", "severity": 1,
        "subject_phrase": "keeping the site current",
        # This one does map, and to the same work as `stale_blog`: the finding is
        # that nobody remembers to put anything new on the site, and what the
        # catalogue sells against that is content that arrives without anybody
        # remembering. The subject phrase already says it — "keeping the site
        # current".
        "services": _svc("AI content generation", "content pipelines"),
    },
    "slow_site": {
        "title": "a slow site", "severity": 1,
        "subject_phrase": "the site speed",
        # Empty for the reason `no_mobile` is empty: page speed is hosting,
        # images and front-end work, and the catalogue sells none of it.
        "services": [],
    },
    "no_schema": {
        "title": "nothing search engines can read", "severity": 1,
        "subject_phrase": "search engine markup",
        "services": _svc("SEO automation"),
    },
    "price_opaque": {
        "title": "no pricing anywhere", "severity": 1,
        "subject_phrase": "pricing on the site",
        "services": _svc("AI lead qualification"),
    },
}

# A blog is only worth mentioning as stale once it is well past "we have been
# busy". Fourteen months clears an annual post and a slow winter both.
STALE_BLOG_DAYS = 425

# ── Fingerprints ──

# Structural markers only: a script host, a build path, a generator tag. The bare
# vendor name is deliberately absent from most lists — "proudly built with
# WordPress" in a footer is not the same as running it.
_CMS_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wordpress", ("/wp-content/", "/wp-includes/", "/wp-json", "wp-emoji-release",
                   'content="wordpress', "content='wordpress")),
    ("wix", ("static.wixstatic.com", "wix-code", "wixstatic.com", "_wixcssimports",
             "static.parastorage.com", 'content="wix.com')),
    ("squarespace", ("squarespace-cdn.com", "static.squarespace.com",
                     "static.squarespace_context", "squarespace.com/universal",
                     'content="squarespace')),
    ("shopify", ("cdn.shopify.com", "shopify.theme", "myshopify.com",
                 "shopify-features", "/cdn/shop/")),
    ("webflow", ("assets.website-files.com", "assets-global.website-files.com",
                 "data-wf-page", "data-wf-site", "webflow.js")),
    ("duda", ("irp.cdn-website.com", "static.cdn-website.com", "dudamobile.com",
              "d1yrv3vfvibqsm.cloudfront.net")),
    ("godaddy", ("img1.wsimg.com", "wsimg.com/blobby", "godaddy website builder")),
    ("weebly", ("editmysite.com", "weeblycloud", "weebly.com/uploads",
                'content="weebly')),
    ("joomla", ("/media/jui/", "/media/system/js/", "joomla-script-options",
                'content="joomla')),
    ("drupal", ("drupal.settings", "/sites/default/files", "drupal-settings-json",
                'content="drupal')),
    # A Framer site matched nothing here and came back "custom", which is the
    # one answer this table has that is never wrong and never useful: it is the
    # word for a hand-built site, and it went to the model about a page the
    # owner assembled by dragging boxes.
    ("framer", ("framerusercontent.com", "data-framer-name", "framerstatic.com",
                'content="framer', "__framer__")),
)

# This table is read twice and the second read is the one that bites: a shop is
# excused from `price_opaque`, because a storefront prints its prices next to
# the buttons. A missing platform therefore costs two claims at once — the shop
# is told nobody automates its orders, and told it publishes no prices.
#
# `woocommerce` is why the bare vendor name is gone from this table. It matched
# a web agency's own services list — "WooCommerce development, Shopify theme
# builds" — and the email told a business that builds shops for other people
# that its own orders need picking and invoicing, then suppressed `price_opaque`
# on the way past, because a shop is excused from publishing prices. One word in
# a list cost two claims. What replaced it is what a shop actually emits: a cart
# endpoint, the plugin's own asset path, the JS config object it writes.
_ECOMMERCE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("shopify", ("cdn.shopify.com", "shopify.theme", "myshopify.com", "/cdn/shop/",
                 "shopify-buy", "/cart/add")),
    ("woocommerce", ("wc-ajax", "/plugins/woocommerce", "wc-add-to-cart",
                     "woocommerce-page", "wc_add_to_cart_params", "woocommerce_params",
                     "/woocommerce/assets/", "wc-blocks", "add-to-cart=")),
    ("bigcommerce", ("cdn11.bigcommerce.com", "bigcommerce.com/stencil", "bigcommerce.js")),
    ("magento", ("/static/version", "mage/cookies", "magento_", "/pub/static/frontend")),
    ("ecwid", ("app.ecwid.com", "ecwid.com/script.js", "ecwid_script", "ec-store")),
    ("squarespace_commerce", ("sqs-add-to-cart", "static/commerce/scripts",
                              "squarespace.com/api/commerce")),
    ("wix_stores", ("wixstores", "stores.wix", "wix-stores")),
    ("square_online", ("square.site/shop", "squareup.com/store", "weebly-commerce")),
    ("prestashop", ("prestashop", "/modules/ps_", "prestashop.com")),
    ("opencart", ("index.php?route=product", "catalog/view/theme")),
    ("lightspeed", ("cdn.shoplightspeed.com", "shoplightspeed.com/assets")),
    ("shopware", ("shopware.com", "/bundles/storefront")),
    ("volusion", ("volusion.com", "/v/vspfiles/")),
    ("snipcart", ("cdn.snipcart.com", "snipcart.js", "snipcart-add-item")),
    ("foxycart", ("cdn.foxycart.com", "foxycart.com/cart")),
    ("gumroad", ("gumroad.com/l/", "gumroad.js")),
    ("podia", ("podia.com/embed", "assets.podia.com")),
    ("sendowl", ("transactions.sendowl.com",)),
    ("commercejs", ("assets.chec.io", "commercejs.com")),
    ("swell", ("cdn.swell.store", "swell.js")),
    ("shift4shop", ("shift4shop.com", "3dcart.com", "/assets/templates/")),
    ("zencart", ("index.php?main_page=product_info", "includes/templates/")),
    ("jtl", ("jtl-shop", "/asset/plugin/jtl_")),
    ("oxid", ("oxid-esales", "/out/azure/src/")),
    ("drupal_commerce", ("commerce_cart", "/commerce/cart-block")),
    # Deliberately not a link to an Etsy or Amazon storefront. That is a shop,
    # but it is somebody else's page: reading it as one here would excuse this
    # site from `price_opaque` on the strength of prices it does not publish.
)

# Every table below ends where its list of vendors ends, and each of these rules
# says "there is no such thing on this site" when it finds nothing. So a vendor
# the table has never heard of does not read as an unknown — it reads as an
# absence, and goes into a live email as a claim about the reader's own website.
# That is why these lists are long and why a marker is added on sight of the
# product rather than on evidence that it is popular: the cost of a missing row
# is a false sentence, and the cost of a spare one is a gap that stays quiet.
_ANALYTICS_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ga4", ("googletagmanager.com/gtag/js", "gtag/js?id=g-", "gtag('config'", 'gtag("config"')),
    ("gtm", ("googletagmanager.com/gtm.js", "googletagmanager.com/ns.html", "gtm-")),
    ("universal_analytics", ("google-analytics.com/analytics.js", "ga('create'", 'ga("create"')),
    ("google_ads", ("googleadservices.com/pagead", "googleads.g.doubleclick.net/pagead",
                    "gtag('event', 'conversion'")),
    ("meta_pixel", ("connect.facebook.net", "fbevents.js", "fbq('init'", 'fbq("init"')),
    ("bing_uet", ("bat.bing.com", "uetq")),
    ("hotjar", ("static.hotjar.com", "hotjar.com/c/hotjar", "_hjsettings")),
    ("clarity", ("clarity.ms",)),
    ("linkedin_insight", ("snap.licdn.com", "_linkedin_partner_id")),
    ("tiktok_pixel", ("analytics.tiktok.com",)),
    ("pinterest_tag", ("s.pinimg.com/ct", "pintrk(")),
    ("snap_pixel", ("sc-static.net/scevent", "snaptr(")),
    ("x_pixel", ("static.ads-twitter.com", "twq('config'", 'twq("config"')),
    ("hubspot", ("js.hs-scripts.com", "hs-analytics.net", "js.hs-analytics.net")),
    ("segment", ("cdn.segment.com",)),
    ("mixpanel", ("cdn.mxpnl.com", "mixpanel.init")),
    ("amplitude", ("cdn.amplitude.com", "api.amplitude.com", "api2.amplitude.com")),
    ("heap", ("cdn.heapanalytics.com", "heap.load(")),
    ("adobe_analytics", ("assets.adobedtm.com", "omtrdc.net", "s_code.js")),
    ("yandex_metrica", ("mc.yandex.ru", "yandex_metrika")),
    ("statcounter", ("statcounter.com/counter",)),
    ("crazyegg", ("script.crazyegg.com",)),
    ("woopra", ("static.woopra.com",)),
    ("plausible", ("plausible.io/js",)),
    ("matomo", ("matomo.js", "piwik.js")),
    ("piwik_pro", ("containers.piwik.pro",)),
    ("umami", ("umami.is/script.js", "/umami.js")),
    ("simple_analytics", ("scripts.simpleanalyticscdn.com",)),
    ("goatcounter", ("gc.zgo.at/count.js",)),
    ("fathom", ("cdn.usefathom.com",)),
    # A site behind Cloudflare gets a page-view counter whether it asked for one
    # or not, and the WordPress plugins below inject Google's tag without ever
    # printing a gtag call the old table could see. Both were read as a site
    # nobody measures.
    ("cloudflare_insights", ("static.cloudflareinsights.com", "cf-beacon")),
    ("monsterinsights", ("monsterinsights", "/plugins/google-analytics-for-wordpress")),
    ("site_kit", ("googlesitekit", "/plugins/google-site-kit")),
    ("fullstory", ("fullstory.com/s/fs.js", "edge.fullstory.com")),
    ("mouseflow", ("cdn.mouseflow.com",)),
    ("smartlook", ("web-sdk.smartlook.com", "manager.smartlook.com")),
    ("luckyorange", ("cdn.luckyorange.com", "luckyorange.net")),
    ("posthog", ("posthog.com/static/array.js", "app.posthog.com", "posthog.init")),
    ("pendo", ("cdn.pendo.io",)),
    ("kissmetrics", ("scripts.kissmetrics.com", "kissmetrics.io")),
    ("chartbeat", ("static.chartbeat.com",)),
    ("quantcast", ("secure.quantserve.com", "quantcast.mgr")),
    ("vercel_analytics", ("/_vercel/insights", "va.vercel-scripts.com")),
    ("splitbee", ("cdn.splitbee.io",)),
    ("panelbear", ("cdn.panelbear.com",)),
    ("usermaven", ("t.usermaven.com",)),
    ("wix_analytics", ("tag-manager-client", "wix-analytics")),
    ("shopify_analytics", ("shopifycloud/web-pixels-manager", "trekkie.storefront")),
    # An AMP page never writes a gtag call: the whole point of the format is
    # that a component does it, and the vendor's own name is inside a JSON
    # block this table cannot see. Every marker above missed it, so a restaurant
    # measuring every visit was told nobody counted last month's.
    ("amp_analytics", ("<amp-analytics", "amp-analytics-0.1.js",
                       "amp-pixel", "amp-ad-exit")),
)

# Eleven vendors were missing here and every one of them draws a box in the
# bottom-right corner of the page, so `no_live_chat` was telling businesses with
# a chat box that they have no chat box. HubSpot's loader is the clearest case
# of what the tables were getting wrong: one script is the chat widget, the CRM
# behind the form and the page-view counter all at once, and it was held in the
# CRM table alone — so two of the three sentences it disproves went out anyway.
_CHAT_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("intercom", ("widget.intercom.io", "intercomsettings", "js.intercomcdn.com")),
    ("tawk", ("embed.tawk.to", "tawk.to/chat", "tawk_api")),
    ("drift", ("js.driftt.com", "drift.load", "driftt.com")),
    ("crisp", ("client.crisp.chat", "$crisp", "crisp_website_id")),
    ("tidio", ("code.tidio.co", "tidiochat")),
    ("livechat", ("cdn.livechatinc.com", "livechatinc.com", "__lc.license")),
    ("messenger", ("fb-customerchat", "customerchat.js", "fb-customer-chat")),
    ("zendesk", ("static.zdassets.com", "zdassets.com/ekr", "zopim", "ze-snippet",
                 "zendesk.com/embeddable")),
    ("freshchat", ("wchat.freshchat.com", "fw-cdn.com", "freshchat.com/js", "fcwidget")),
    ("olark", ("static.olark.com", "olark.com/jsclient", "olark.identify")),
    ("smartsupp", ("smartsuppchat.com", "smartsupp.com/loader", "_smartsupp")),
    ("zoho_salesiq", ("salesiq.zoho", "zsiqchat", "zsiqwidget")),
    ("hubspot", ("js.hs-scripts.com", "js.usemessages.com", "hubspot-messages-iframe",
                 "api.hubspot.com/livechat")),
    ("userlike", ("userlike-cdn", "widget.userlike", "userlike.com/api")),
    ("chatra", ("call.chatra.io", "chatra.io/widget", "chatraid")),
    ("jivosite", ("code.jivosite.com", "jivosite.com/widget", "jivo_api")),
    ("purechat", ("app.purechat.com", "purechat.com/visitorwidget", "purechat-id")),
    ("helpcrunch", ("widget.helpcrunch.com", "helpcrunch.com/sdk", "helpcrunchsettings")),
    ("salesforce_chat", ("salesforceliveagent", "service.force.com/embeddedservice",
                         "liveagent.js", "embeddedservice_bootstrap")),
    # Round two of the same lesson. A site builder that ships its own chat is
    # the worst case of all — the owner did not install anything, the box is
    # simply there, and being told it is not is being told the writer never
    # opened the page. Wix, Shopify and Squarespace all ship one.
    ("wix_chat", ("wix-visitor-chat", "wixchat", "chat-widget-app")),
    ("shopify_inbox", ("shopifycloud/chat", "shopify-chat", "chat.shopify.com")),
    ("squarespace_chat", ("squarespace.com/api/chat", "sqs-chat")),
    ("gohighlevel_chat", ("leadconnectorhq.com/chat-widget", "widgets.leadconnectorhq.com",
                          "chat-widget/loader")),
    ("helpscout", ("beacon-v2.helpscout.net", "beaconapi.helpscout", "window.beacon")),
    ("gorgias", ("config.gorgias.chat", "gorgias.chat", "gorgias-chat")),
    ("reamaze", ("cdn.reamaze.com", "reamaze.js")),
    ("kustomer", ("cdn.kustomerapp.com", "kustomer.start")),
    ("front", ("chat-assets.frontapp.com", "frontchat")),
    ("trengo", ("static.widget.trengo.eu", "trengo.key")),
    ("podium", ("connect.podium.com", "podium.com/widget", "podium-widget")),
    ("birdeye", ("birdeye.com/embed", "cdn.birdeye.com", "birdeye-webchat")),
    ("chaport", ("app.chaport.com", "chaport.com/javascripts")),
    ("chatway", ("cdn.chatway.app", "chatway.app/widget")),
    ("comm100", ("vue.comm100.com", "comm100.com/livechat")),
    ("livehelpnow", ("livehelpnow.net", "lhnjquery")),
    ("snapengage", ("snapengage.com",)),
    ("gist", ("getgist.com",)),
    ("manychat", ("mccdn.me", "manychat.com/widget")),
    ("landbot", ("cdn.landbot.io", "landbot.io/v3")),
    ("tars", ("tars.chat", "chatbot.tars")),
    ("ada", ("static.ada.support", "ada.embed")),
    ("verloop", ("cdn.verloop.io",)),
    ("wati", ("wati-integration", "wati.io/widget")),
    ("joinchat", ("joinchat", "creame.io/joinchat")),
    ("getbutton", ("getbutton.io",)),
)

_BOOKING_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("calendly", ("calendly.com",)),
    ("acuity", ("acuityscheduling.com", "squarespacescheduling.com")),
    ("setmore", ("setmore.com", "my.setmore")),
    ("square", ("squareup.com/appointments", "book.squareup.com", "square.site/book")),
    ("opentable", ("opentable.com", "opentable.ca")),
    ("resy", ("resy.com",)),
    ("housecallpro", ("housecallpro.com", "book.housecallpro")),
    ("servicetitan", ("servicetitan.com", "st-booking", "servicetitan.io")),
    ("mindbody", ("mindbodyonline.com", "mindbody.io", "healcode")),
    ("vagaro", ("vagaro.com",)),
    ("booksy", ("booksy.com",)),
    ("fresha", ("fresha.com",)),
    ("janeapp", ("janeapp.com", "jane.app")),
    ("cliniko", ("cliniko.com/bookings",)),
    ("schedulicity", ("schedulicity.com",)),
    ("simplybook", ("simplybook.me", "simplybook.it")),
    ("timely", ("gettimely.com", "book.timelyapp")),
    ("zenoti", ("zenoti.com",)),
    ("phorest", ("phorest.com/book", "phorestsalonsoftware")),
    ("jobber", ("getjobber.com", "clienthub.getjobber")),
    # Not "cal.com/": that string is inside "local.com/", and a link to a local
    # directory would have read as a calendar.
    ("calcom", ("//cal.com/", "app.cal.com", "cal.com/book")),
    ("youcanbookme", ("youcanbook.me",)),
    ("chilipiper", ("chilipiper.com",)),
    ("hubspot_meetings", ("meetings.hubspot.com", "meetings-na1.hubspot.com")),
    ("google_appointments", ("calendar.app.google",
                             "calendar.google.com/calendar/appointments")),
    ("microsoft_bookings", ("outlook.office365.com/owa/calendar", "bookings.office.com",
                            "outlook.office.com/bookwithme")),
    ("tock", ("exploretock.com",)),
    ("sevenrooms", ("sevenrooms.com",)),
    ("thefork", ("thefork.com", "lafourchette.com")),
    # Everything below books a time in a market this list had never left. The
    # cost of the omission is the catalogue's highest-severity gap going out to
    # a practice whose home page has a Book button on it: `no_online_booking` is
    # usually `gaps[0]`, and `gaps[0]` is the first sentence of the email.
    ("doctolib", ("doctolib.fr", "doctolib.de", "doctolib.it", "doctolib.com")),
    ("zocdoc", ("zocdoc.com",)),
    ("nexhealth", ("nexhealth.com", "nexhealth.io")),
    ("weave", ("getweave.com", "weavehelp.com")),
    ("boulevard", ("boulevard.io", "joinblvd.com")),
    ("glossgenius", ("glossgenius.com",)),
    ("appointy", ("appointy.com",)),
    ("bookeo", ("bookeo.com",)),
    ("checkfront", ("checkfront.com",)),
    ("fareharbor", ("fareharbor.com",)),
    ("peek", ("peekpro.com", "book.peek.com")),
    ("rezdy", ("rezdy.com",)),
    ("xola", ("xola.com",)),
    ("bookwhen", ("bookwhen.com",)),
    ("tidycal", ("tidycal.com",)),
    ("savvycal", ("savvycal.com",)),
    ("oncehub", ("oncehub.com", "scheduleonce.com", "go.oncehub")),
    ("picktime", ("picktime.com",)),
    ("timify", ("timify.com",)),
    ("terminland", ("terminland.de",)),
    ("etermin", ("etermin.net",)),
    ("jameda", ("jameda.de/termine", "jameda.de/booking")),
    ("treatwell", ("treatwell.co.uk", "treatwell.nl", "treatwell.de")),
    ("planity", ("planity.com",)),
    ("shore", ("shore.com/book", "app.shore.com")),
    ("salonized", ("salonized.com",)),
    ("bokadirekt", ("bokadirekt.se",)),
    ("resurva", ("resurva.com",)),
    ("gohighlevel_booking", ("leadconnectorhq.com/widget/booking",
                             "msgsndr.com/widget/booking")),
    ("wix_bookings", ("wixbookings", "wix-bookings", "bookings.wixapps.net")),
    ("quandoo", ("quandoo.com", "quandoo.de")),
    ("formitable", ("formitable.com",)),
    ("bookatable", ("bookatable.com", "bookatable.co.uk")),
)

_CRM_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hubspot", ("js.hs-scripts.com", "js.hsforms.net", "hs-analytics.net",
                 "hubspot.com/cta", "js.hscollectedforms.net")),
    # Not the bare "force.com": it is the tail of workforce.com, and an HR
    # consultancy linking to a rota product was read as running Salesforce, so
    # the true finding — nothing files the enquiries this form collects — never
    # fired. `_present` now refuses a host-shaped marker glued to a longer
    # label, and the marker is written the way it appears anyway.
    ("salesforce", ("pardot.com", "pi.pardot.com", "webto.salesforce.com",
                    "salesforceliveagent", ".force.com", "//force.com")),
    ("zoho", ("salesiq.zoho", "zohopublic", "crm.zoho", "zohoforms")),
    ("pipedrive", ("pipedrive.com", "pipedriveassets.com", "leadbooster")),
    ("activecampaign", ("activehosted.com", "prism.app-us1.com", "activecampaign.com")),
    ("mailchimp", ("chimpstatic.com", "list-manage.com", "mailchi.mp",
                   "mc.us.list-manage")),
    ("klaviyo", ("static.klaviyo.com", "klaviyo.com/onsite", "static-tracking.klaviyo")),
    # Mailchimp was already here, and these file a name and an address exactly
    # the way it does. Holding one of them and not the others meant the same
    # site read as having a CRM or as having nothing depending on which mailing
    # tool it happened to buy.
    ("keap", ("infusionsoft.com", "keap.com", "infusionsoft.app")),
    ("constantcontact", ("ctctcdn.com", "constantcontact.com")),
    ("brevo", ("sibforms.com", "sendinblue.com", "brevo.com")),
    ("convertkit", ("convertkit.com", "ck.page", "f.convertkit")),
    ("mailerlite", ("mailerlite.com", "ml-form", "static.mailerlite")),
    ("aweber", ("aweber.com", "forms.aweber")),
    ("gohighlevel", ("msgsndr.com", "leadconnectorhq.com", "gohighlevel.com")),
    ("marketo", ("munchkin.js", "mktoresp.com", "marketo.net")),
    ("dynamics", ("crm.dynamics.com", "dynamics.com/uclick")),
    ("freshworks", ("freshsales.io", "freshworks.com/crm", "fwcrm")),
    ("omnisend", ("omnisend.com", "omnisnippet")),
    ("drip", ("getdrip.com", "dripstatic.com")),
    ("campaignmonitor", ("createsend.com", "campaignmonitor.com")),
    ("getresponse", ("getresponse.com", "gr-wcm")),
    ("mailjet", ("mailjet.com", "mjt.lu")),
    ("moosend", ("moosend.com",)),
    ("emailoctopus", ("emailoctopus.com", "eocampaign1.com")),
    ("sharpspring", ("sharpspring.com", "marketingautomation.services")),
    ("ontraport", ("ontraport.com", "ontraport.net")),
    ("close", ("close.com/forms",)),
    ("copper", ("copper.com/forms",)),
    ("insightly", ("insightly.com", "insight.ly")),
    ("nutshell", ("nutshell.com/forms",)),
    ("engagebay", ("engagebay.com",)),
    ("agilecrm", ("agilecrm.com",)),
    ("salesflare", ("salesflare.com",)),
    ("teamleader", ("teamleader.eu", "teamleader.io")),
    ("monday", ("monday.com/embeds", "forms.monday.com")),
    ("zendesk_sell", ("getbase.com", "zendesk.com/sell")),
)

_FRAMEWORK_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("nextjs", ("__next_data__", "/_next/static")),
    ("nuxt", ("__nuxt__", "/_nuxt/")),
    ("react", ("data-reactroot", "react-dom", "_reactlistening", "react.production.min")),
    ("vue", ("vue.runtime", "vue.min.js", "data-v-app", "__vue__")),
    ("angular", ("ng-version", "angular.min.js", "ng-app=")),
    ("svelte", ("svelte-", "/_app/immutable")),
    ("jquery", ("jquery.min.js", "jquery-3", "jquery/1.", "jquery/2.")),
)

# What the page was assembled in. Two jobs: the digest says "wordpress+elementor"
# instead of "wordpress", which is a truer sentence about who maintains the site;
# and a builder is proof of its host CMS, so a WordPress install serving its
# assets from a CDN — where `/wp-content/` never appears in the markup — stops
# being reported as a hand-built site.
_BUILDER_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("elementor", ("/plugins/elementor/", "elementor-page", "elementor-widget",
                   "elementor-frontend")),
    ("divi", ("/themes/divi/", "et_pb_", "et-core-", "divi-builder")),
    ("wpbakery", ("js_composer", "vc_row", "wpb_wrapper")),
    ("beaver", ("/plugins/bb-plugin/", "fl-builder")),
    ("oxygen", ("/plugins/oxygen/", "ct-section", "oxy-header")),
    ("bricks", ("/themes/bricks/", "brxe-")),
    ("avada", ("/themes/avada/", "fusion-builder", "fusion-column")),
    ("astra", ("/themes/astra/", "ast-container")),
    ("kadence", ("/themes/kadence/", "kadence-blocks")),
    ("enfold", ("/themes/enfold/", "avia-builder", "av_")),
    ("brizy", ("/plugins/brizy/", "brz-")),
    ("siteorigin", ("siteorigin-panels", "/plugins/so-widgets")),
    ("thrive", ("/plugins/thrive-visual-editor/", "tve-leads")),
    ("breakdance", ("/plugins/breakdance/", "breakdance-")),
    ("gutenberg", ("wp-block-", "/css/dist/block-library")),
)
_WORDPRESS_BUILDERS = frozenset({
    "elementor", "divi", "wpbakery", "beaver", "oxygen", "bricks", "avada", "astra",
    "kadence", "enfold", "brizy", "siteorigin", "thrive", "breakdance", "gutenberg",
})

# Where a finished customer is sent to say so. Read for `no_review_capture`,
# which only ever fires on a site that already prints praise: the claim is that
# nothing on the page routes anyone to a live review, and one of these on the
# page makes that claim false.
_REVIEW_ROUTES: tuple[str, ...] = (
    "g.page/r/", "/review?placeid", "search.google.com/local/writereview",
    "google.com/maps/place", "goo.gl/maps", "maps.app.goo.gl",
    "trustpilot.com", "widget.trustpilot.com", "yelp.com/biz", "yelp.ca/biz",
    "birdeye.com", "podium.com", "nicejob.com",
    "trustindex.io", "reviews.io", "feefo.com", "yotpo.com", "judge.me",
    "stamped.io", "shopperapproved.com", "bazaarvoice.com", "provenexpert.com",
    "trustedshops.com", "kiyoh.com", "avis-verifies.com", "tripadvisor.com",
    "checkatrade.com", "trustatrader.com", "houzz.com/professionals",
)

# A green button that opens WhatsApp. Only the click-to-chat endpoints, never
# the bare brand: "follow us on WhatsApp" in a footer is not a button, and the
# gap's whole claim is that there is one and a person is behind it.
_WHATSAPP_ROUTES: tuple[str, ...] = (
    "wa.me/", "api.whatsapp.com/send", "web.whatsapp.com/send",
    "chat.whatsapp.com", "whatsapp://send",
)

# Form embeds that render no <form> of their own but are still a lead route.
_FORM_EMBEDS = ("jotform", "typeform", "formstack", "wufoo", "docs.google.com/forms",
                "forms.gle", "gravityforms", "hsforms.net", "formidable", "cognitoforms",
                "tally.so", "fillout.com", "paperform.co", "123formbuilder",
                "surveymonkey.com", "airtable.com/emb", "forms.zohopublic",
                "formspree.io", "getform.io", "involve.me")

# Mailing tools, which put a signup on the page whether or not the markup shows
# a field: most of them render the box in JavaScript or in a pop-up. Constant
# Contact serves its widget from ctctcdn.com and never spells its own name, so
# the entry that was meant to hold it never matched a page it was on.
_MAILING_LIST_MARKERS = ("list-manage.com", "mailchi.mp", "chimpstatic.com", "klaviyo",
                         "activehosted.com", "constantcontact", "ctctcdn.com",
                         "sendinblue", "sibforms.com", "brevo.com", "convertkit",
                         "ck.page", "mailerlite", "aweber", "omnisend", "getdrip",
                         "emailoctopus", "campaign-archive.com")
_EMAIL_FIELD_RE = re.compile(
    r"""(?is)<input\b[^>]*(?:type\s*=\s*["']?email|name\s*=\s*["'][^"']*e?mail)""")
_URL_ATTR_RE = re.compile(r"""(?is)(?:src|href|action)\s*=\s*["']([^"']{4,300})["']""")

# schema.org types that mean "a place customers walk into".
_LOCALBUSINESS_TYPES = (
    "localbusiness", "restaurant", "plumber", "electrician", "dentist", "hvacbusiness",
    "generalcontractor", "roofingcontractor", "homeandconstructionbusiness", "store",
    "medicalbusiness", "healthandbeautybusiness", "beautysalon", "hairsalon", "spa",
    "automotivebusiness", "autorepair", "legalservice", "attorney", "accountingservice",
    "veterinarycare", "childcare", "professionalservice", "foodestablishment",
    "lodgingbusiness", "realestateagent", "financialservice", "movingcompany",
    "cleaningservice", "locksmith", "pestcontrol", "landscaper", "daycare",
)

# ── HTML helpers ──

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Third-party pages are unbounded; everything past this is boilerplate or data.
_SCAN_CAP = 600_000
_READ_CAP = 900_000

# The typography a page is written in, and a phrase list never is. `we’re
# hiring` off a word processor is a different string from `we're hiring`, and a
# barber shop that types `walk-ins welcome — first-come, first-served` is saying
# the same thing as one that types it flat. Matching phrases against raw text
# made the punctuation part of the claim, so a site advertising a job read as
# one that was not, and a shop that books nothing was told to buy a calendar.
#
# Only the quotes are folded, because an apostrophe is a letter's neighbour
# and is the one mark kept: `_PUNCT_RE` takes every other one out on its own,
# and it would otherwise turn "we’re" into "we re" and leave the phrase
# unfindable in the other direction.
_TYPOGRAPHY = str.maketrans({"‘": "'", "’": "'", "‚": "'",
                             "‛": "'", "′": "'", "“": '"', "”": '"'})
# A word keeps its accents — `réserver` and `verstärkung` are words in the lists
# below — so this is `\w` and not `[a-z]`, and the underscore is punctuation
# here rather than a letter, because `/book_now/` is a path and not a word.
_WORD_RE = re.compile(r"[\w']+")
_PUNCT_RE = re.compile(r"[^\w' ]+|_+")

_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript|svg|template)\b[^>]*>.*?</\1\s*>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"\s+")
_A_RE = re.compile(r"""(?is)<a\b[^>]*?href\s*=\s*["']([^"']*)["'][^>]*>(.*?)</a>""")
_HEAD_RE = re.compile(r"(?is)<h([1-3])\b[^>]*>(.*?)</h\1\s*>")
_TITLE_RE = re.compile(r"(?is)<title\b[^>]*>(.*?)</title\s*>")
_META_RE = re.compile(r"(?is)<meta\b[^>]*>")
_ATTR_RE = re.compile(r"""(?is)([a-z0-9_:\-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")
_SCHEMA_TYPE_RE = re.compile(r'(?is)"@type"\s*:\s*"([^"]{2,40})"')
_ITEMTYPE_RE = re.compile(r"""(?is)itemtype\s*=\s*["'][^"']*schema\.org/([A-Za-z]+)""")
_VIEWPORT_RE = re.compile(r"""(?is)<meta[^>]+name\s*=\s*["']viewport["'][^>]*>""")
_FORM_RE = re.compile(r"(?is)<form\b.*?</form\s*>")
_FORM_OPEN_RE = re.compile(r"(?i)<form\b")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Four shapes, because `has_phone` is a claim and not a nicety: the email says
# "no number to tap" out loud, and a footer reading `04 78 55 44 33` makes that
# sentence false. North America groups 3-3-4 and the rest of the world does not,
# so a NANP-only pattern reported "no phone" for most of Europe. Erring towards
# seeing a number is the safe direction here — a missed one puts a wrong claim
# in a live email, a spurious one only holds a gap back.
_PHONE_RES = (
    re.compile(r"(?:\(\d{3}\)|\b\d{3})[\s.\-]\d{3}[\s.\-]\d{4}\b"),
    re.compile(r"\+\d{1,3}[\s.\-]?\d{2,4}[\s.\-]?\d{3,4}[\s.\-]?\d{2,4}"),
    # A national trunk prefix: the leading zero Europe dials before its own area
    # codes, and nothing else on a business page opens with one and runs on.
    re.compile(r"\b0\d(?:[\s.\-]?\d){7,12}\b"),
    # Grouped in twos and threes — `920 55 10 40`. Later groups are held to two
    # or three digits so a run of years (`2026 2025 2024`) cannot qualify.
    re.compile(r"\b\d{2,4}(?:[\s.\-]\d{2,3}){2,4}\b"),
    # A bracketed trunk or country code, which is how most of the world outside
    # North America prints an area code: `(07) 3555 0177`, `(0161) 555 0134`.
    # None of the four patterns above survives the bracket — the first wants a
    # three-digit code inside it, and the loose two run into the `)` and stop —
    # so an Australian vet printing its number in the footer was read as a site
    # with no number on it, and `contact_form_only` told it "no number to tap".
    re.compile(r"\(\+?\d{1,5}\)[\s.\-]?\d{1,4}(?:[\s.\-]?\d{2,4}){1,3}"),
)
_DIGIT_RE = re.compile(r"\d")
# Nine digits is the shortest national number in use and fifteen is the E.164
# ceiling. The bound is what keeps an ISO date (eight) out of the loose arms.
_PHONE_DIGITS = (9, 15)

# The symbol leads in English and trails across most of Europe, and the decimal
# separator swaps with it: `89,00 €` is a price and the prefix-only pattern read
# a page of them as having none, which put "not a rate, a range or a starting
# figure on any page" in front of a business whose home page is a price list.
_MONEY_RE = re.compile(
    r"[$£€¥]\s?\d{1,3}(?:[,.\s]\d{3})*(?:[.,]\d{2})?\b"
    r"|\b\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{2})?\s?[$£€¥]")
_COPYRIGHT_RE = re.compile(r"(?:©|&copy;|copyright)[^0-9]{0,20}(?:(\d{4})\s*[-–—]\s*)?(\d{4})", re.I)
_ASSET_RE = re.compile(
    r"\.(?:png|jpe?g|gif|webp|svg|ico|css|js|json|xml|zip|rar|mp4|mp3|avi|mov|"
    r"woff2?|ttf|eot|doc|docx|xls|xlsx|csv)(?:$|[?#])", re.I)

# Matched against the parsed host, never against the href: "x.com" is inside
# "simplex.com", so a link to a supplier counted as a social profile and the
# finding it suppressed was one this business really did have.
_SOCIAL_HOSTS = ("facebook.com", "fb.com", "instagram.com", "linkedin.com",
                 "twitter.com", "x.com", "youtube.com", "tiktok.com",
                 "pinterest.com", "threads.net", "threads.com", "snapchat.com",
                 "vimeo.com", "nextdoor.com", "xing.com")
_SOCIAL_JUNK_RE = re.compile(
    r"(?:/sharer|/share\.php|/intent/|/plugins/|/dialog/|/tr\?|/hashtag/)", re.I)
# Feed widgets render their profile links in JavaScript, so the markup shows the
# plugin and no <a>. Telling a business with an Instagram feed on the home page
# that it has no social presence is exactly the kind of miss that kills a reply.
_SOCIAL_EMBEDS = ("instagram-feed", "smashballoon", "sbi_", "facebook.com/plugins/page",
                  "fb-page", "elfsight", "juicer.io", "curator.io", "taggbox",
                  "twitter-timeline", "lightwidget")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATETIME_ATTR_RE = re.compile(r'(?is)datetime\s*=\s*["\']([^"\']{4,40})["\']')
_DATE_JSON_RE = re.compile(
    r'(?is)"date(?:published|modified|created)"\s*:\s*"([^"]{4,40})"')
_ARTICLE_TIME_RE = re.compile(
    r'(?is)<meta[^>]+article:(?:published|modified)_time[^>]+'
    r'content\s*=\s*["\']([^"\']+)["\']')
_ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_MDY_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b",
    re.I)
_DMY_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(20\d{2})\b",
    re.I)

_POSTAL_RES = (
    re.compile(r"\b[A-Z]\d[A-Z][ \-]?\d[A-Z]\d\b"),                    # Canada
    re.compile(r",\s*[A-Z]{2}\s+(\d{5})(?:-\d{4})?\b"),                # US, needs a state
    re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b"),              # UK
)

# Nav labels that describe the site, not what the business sells.
_NAV_STOPWORDS = frozenset({
    "home", "about", "about us", "contact", "contact us", "blog", "news", "careers",
    "jobs", "gallery", "portfolio", "testimonials", "reviews", "faq", "faqs", "privacy",
    "privacy policy", "terms", "terms of service", "sitemap", "login", "log in",
    "sign in", "sign up", "register", "cart", "checkout", "search", "menu", "shop",
    "store", "book now", "book online", "get a quote", "request a quote", "free quote",
    "call us", "email us", "locations", "our team", "team", "services", "our services",
    "what we do", "pricing", "prices", "français", "english", "espanol", "español",
    "more", "read more", "learn more", "next", "previous", "skip to content", "close",
})

# Label openings that mark a button rather than a thing the business sells.
_CTA_PREFIXES = ("get a", "get your", "get in", "request a", "book a", "book your",
                 "call ", "schedule a", "claim your", "start your", "contact ",
                 "learn ", "view ", "see our", "read ", "download ", "click ")

_SERVICE_WORDS = (
    "repair", "install", "service", "cleaning", "maintenance", "inspection",
    "consultation", "treatment", "appointment", "booking", "estimate", "quote",
    "emergency", "replacement", "removal", "design", "training", "therapy", "grooming",
    "detailing", "renovation", "landscaping", "roofing", "plumbing", "heating",
    "cooling", "electrical", "dental", "legal", "accounting", "tutoring", "catering",
    # A driving school heads its list "Driving lessons" and sells nothing this
    # tuple had ever heard of, so nothing about that business reached `services`
    # — and `_bookable` reads `services`, so the one trade whose whole product
    # is an hour in a diary looked like a business that books nothing.
    # Not "session": this tuple is matched against every link label on the site,
    # and a footer link reading "Session cookies" would have entered the list of
    # what the business sells and then read as an hour somebody books.
    "lesson", "tuition", "massage", "tattoo", "photography",
    "assessment", "valuation", "coaching",
)

# Language that means a customer arranges a time. Not "emergency", "estimate",
# "free quote" or "same day service": those are how a job gets priced and how
# fast it gets done, and a quote arriving in a form is `quote_by_form` — a
# different finding, with a different offer behind it. Reading them as booking
# put the catalogue's highest-severity gap on top of businesses that book
# nothing, which is the one place a wrong claim is unrecoverable.
#
# The bare noun "booking" is gone for that reason. It is the first word of
# "Booking terms and cancellation policy", which is the small print under an
# order form and appears on printers, couriers and hire companies that arrange
# no times at all. Every other entry here is a verb with its object attached, so
# it can only be somebody arranging one.
_APPOINTMENT_WORDS = (
    "appointment", "appointments", "book a", "book an", "book your", "schedule a",
    "schedule an", "schedule your", "consultation", "consultations",
    "call us to book", "call to book", "call to schedule", "arrange a time",
    "reservation", "reservations", "reserve a table", "terminvereinbarung",
    "termin vereinbaren", "termin buchen", "cita previa", "rendez vous",
)

# The page saying outright that it does not book times. "Walk-ins only, no
# appointments" contains the word the rule was matching on, so the email opened
# by contradicting the page it was quoting. Written flat, because `_phrase_text`
# has already taken the hyphens and the comma out of the page: one entry here
# now matches "first-come, first-served" and "first come first served" both,
# which is the pair that got through and put a barber shop on the list.
_NO_APPOINTMENT_PHRASES = (
    "walk ins only", "walk in only", "walk ins welcome", "walk in welcome",
    "no appointment", "no appointments", "without an appointment",
    "first come first served", "drop ins welcome",
    "no booking required", "no bookings required",
    "no reservation required", "no reservations required",
    "ohne termin", "sin cita previa",
)

# Work that is delivered at a time somebody has to agree to. A business whose
# own list of what it sells reads like this books times whether or not the page
# ever says so — and one whose list reads "CNC machining, powder coating, next
# day delivery" does not. That distinction is the whole rule: before it, any
# services list at all was enough, so every fabricator and wholesaler with a
# contact form was told it was missing a booking system it has no use for.
#
# The second block is trades that book a chair, a couch or a slot and never use
# the word: a tattoo studio, an osteopath, an estate agent showing a house. Not
# "class", "course" or "tour", which are inside "first class postage", "golf
# course" and "contour", and would put the headline gap on a courier.
#
# Not the bare "servicing" any more. It is a whole word in "site servicing",
# which is a civil contractor laying pipe and drainage — nobody books a time for
# it — and that one word put the catalogue's headline gap, the first sentence of
# the email, on a concrete company. The trades that genuinely book a service
# already reach this list through "repair", "install", "inspection" and
# "maintenance", which is how their own pages word it.
_BOOKABLE_WORDS = (
    "appointment", "consultation", "consult", "treatment", "therapy", "massage",
    "cleaning", "repair", "install", "inspection", "service call",
    "maintenance", "checkup", "check up", "exam", "grooming", "detailing",
    "haircut", "styling", "manicure", "pedicure", "facial", "tune up",
    "test drive", "fitting", "lesson", "tutoring", "valuation", "assessment",
    "tattoo", "piercing", "waxing", "acupuncture", "osteopath", "physiotherapy",
    "chiropractic", "adjustment", "counselling", "counseling", "coaching",
    "session", "sitting", "viewing", "survey", "hearing test", "eye test",
    "sight test", "mot testing", "boarding", "daycare", "dog walking",
)

# A site that calls itself a dentist in its own JSON-LD books times, and says so
# in the one place on the page that cannot be a turn of phrase.
_APPOINTMENT_SCHEMA_TYPES = (
    "dentist", "medicalbusiness", "medicalclinic", "physician", "veterinarycare",
    "healthandbeautybusiness", "beautysalon", "hairsalon", "spa", "autorepair",
    "childcare", "daycare", "restaurant", "lodgingbusiness",
    # A trade whose own markup names it, in the languages this crawl meets. The
    # words a German optician writes on the page — Sehtest, Brillenanpassung —
    # are in no phrase list here and never will be; the schema type is the same
    # fact said in a way that does not need translating.
    "optician", "opticalstore", "physiotherapy", "physiotherapist", "psychologist",
    "dayspa", "nailsalon", "tattooparlor", "hospital", "emergencyservice",
    "healthclub", "sportsactivitylocation", "drivingschool", "hairdresser",
)

_QUOTE_PHRASES = (
    "request a quote", "get a quote", "free quote", "request an estimate",
    "free estimate", "get an estimate", "request a proposal", "request pricing",
    "get a free quote", "book an estimate",
)

# The other languages carry their own spellings, and a page that only says
# "Stellenangebote" was read as a business that is not hiring. Phrases, not bare
# nouns, for the same reason the English list uses them: "recrutement" is what a
# recruitment agency calls its whole business, "nous recrutons" is a vacancy.
#
# A path ends where the word ends, exactly as the booking link does. Without the
# terminator "/careers" was inside "/blog/careers-in-the-trades/", so an article
# about apprentice wages was read as a vacancy.
_CAREER_LINK_RE = re.compile(
    r"/(?:careers?|jobs?|join-us|join-our-team|work-with-us|employment|vacancies|hiring|"
    r"karriere|stellenangebote|offene-stellen|stellen|"
    r"recrutement|nous-rejoindre|offres-d-emploi|"
    r"empleo|trabaja-con-nosotros|unete-al-equipo)(?:/|$|[?#.])", re.I)

# Sentences that are a vacancy however they are worded. The bare words are not
# here: "careers" and "apply now" are a heading and a button, and matched
# anywhere in the visible text they found "Careers: jobs@clearviewhvac.ca" in a
# contact block and "Apply now" over a credit application, then told both
# businesses they were sifting CVs by hand.
_HIRING_PHRASES = ("we're hiring", "we are hiring", "now hiring", "join our team",
                   "current openings", "job openings", "send us your resume",
                   "stellenangebote", "offene stellen", "wir stellen ein",
                   "wir suchen verstärkung", "bewerbungen an",
                   "nous recrutons", "offres d'emploi", "postes à pourvoir",
                   "rejoignez notre équipe", "ofertas de empleo",
                   "estamos contratando", "trabaja con nosotros")

# The bare words earn their place when they *head* a section, which is the
# structure a job listing actually has. Matched whole, so "Careers in the
# trades: what an apprenticeship really pays" is still a headline about a trade.
_CAREER_HEADINGS = frozenset({
    "careers", "career", "careers and jobs", "jobs", "job opportunities",
    "employment", "employment opportunities", "vacancies", "current vacancies",
    "current openings", "open positions", "job openings", "join our team",
    "join the team", "work with us", "work for us", "we're hiring", "we are hiring",
    "karriere", "stellenangebote", "offene stellen", "jobs & karriere",
    "recrutement", "offres d'emploi", "nous rejoindre",
    "empleo", "ofertas de empleo", "trabaja con nosotros",
})

# A small business almost never links each service or gives it a heading. It
# writes "Services" once and lists them underneath, which is the one place the
# site says outright what it sells — and the only pass that reads it. Headings
# are matched by fragment so "Unsere Leistungen" and "Nos compétences" land here
# too; the list is scoped to the heading, so a footer or a nav cannot leak in.
_SERVICE_HEADINGS = ("service", "servic", "what we do", "our work", "treatment",
                     "solution", "capabilit", "specialt", "specialit",
                     "leistungen", "angebot", "unsere arbeit",
                     "compétences", "competences", "prestations", "nos produits",
                     "productos", "nuestros servicios", "que hacemos")
_SERVICE_PATH_RE = re.compile(
    r"(?i)/(?:services|service|treatments|solutions|what-we-do)/[a-z0-9\-]{3,}")
_LIST_RE = re.compile(r"(?is)<(?:ul|ol)\b[^>]*>(.*?)</(?:ul|ol)\s*>")
_LI_RE = re.compile(r"(?is)<li\b[^>]*>(.*?)</li\s*>")

_BLOG_HREF_RE = re.compile(r"/(?:blog|news|articles|insights|updates|posts|stories)(?:/|$|\?)", re.I)
_BLOG_WORDS = frozenset({"blog", "news", "articles", "insights", "stories"})

# Whole words, and a slug read as words. "form" inside "uniform-catalogue.pdf"
# is not paperwork, and the sentence it produced — "the uniform catalogue is a
# PDF to print, fill in and send back" — is nonsense to the one reader who
# knows what that file is.
_PDF_FORM_RE = re.compile(
    r"(?i)(?<![a-z])(?:forms?|applications?|intake|waivers?|agreements?|contracts?|"
    r"registration|questionnaires?|checklists?|new patient|credit app|"
    r"quote request|estimate request)(?![a-z])")
_SLUG_RE = re.compile(r"[\-_+%/.]+")

# A document handed out instead of a price on a page. Deliberately not
# "catalogue": a uniform catalogue is a list of what a trade supplier stocks,
# and reading one as a rate card would silence `price_opaque` on exactly the
# sites that gap was written for. Only the nouns that mean "what it costs", in
# the languages this crawl meets.
_PRICE_DOC_RE = re.compile(
    r"(?i)(?<![a-z])(?:price ?lists?|pricelists?|price guide|price sheet|rate card|"
    r"rates|tariffs?|tarifs?|fee schedule|fees|menu|preisliste|preise|"
    r"precios|tarifas|tarieven|prijslijst)(?![a-z])")
# 20xx only. A version number that happens to have four digits is not a year,
# and next year's price list is this year's news.
_DOC_YEAR_RE = re.compile(r"(?<!\d)(20[0-4]\d)(?!\d)")
# Two years, because one is a business that reprints every January and has not
# got round to it. Two price seasons back is a document nobody has reopened.
STALE_DOCUMENT_YEARS = 2

# A form is long once it asks for ten separate things. Nine is a quote request
# for a roof — address, area, when — and ten is the point where a stranger with
# a question closes the tab. Measured on questions, not on `<input>` tags: see
# `_form_fields`.
LONG_FORM_FIELDS = 10
# And a services list is long once the site is selling eight distinct things
# through one form. Below that, "which service is this about" is a sentence the
# person reading the inbox does not have to ask.
MANY_SERVICES = 8

# Where a job application is sent when there is no form to send it through.
_JOB_MAIL_RE = re.compile(
    r"(?i)jobs?|careers?|recruit|vacanc|hiring|resume|cv@|bewerbung|"
    r"emploi|empleo|personal@|hr@")

# Verbs a link label opens with before it gets to the name of the thing.
_DOC_LEAD_RE = re.compile(
    r"(?i)^(?:download|get|view|open|print|complete|fill\s+(?:in|out))\b\s*(?:the|our|your|a)?\s*")
_DOC_NAME_RE = re.compile(r"[a-z][a-z' ]+[a-z]")

# A path segment read as the words in it, rather than as one of twenty-two
# spellings somebody thought of. The old list demanded the segment be exactly
# `/booking`, so `/online-booking/` and `/book-a-table/` — the two plainest ways
# a site writes it — were not a booking system, and `no_online_booking` went out
# to businesses with a Book button on the home page.
#
# Only the other languages' *verbs* for booking are here, never their nouns for
# an appointment: `buchen` is a calendar, and `/cita` and `/reservas` are as
# often a page with a phone number on it. The English list already carries that
# ambiguity in `appointment` and `reserve`; nothing is gained by importing it.
_BOOKING_PATH_WORDS = frozenset({
    "book", "booking", "bookings", "schedule", "scheduling", "appointment",
    "appointments", "reserve", "reservation", "reservations",
    "buchen", "terminbuchung", "terminvereinbarung", "reserver", "reservar",
    # Dutch, Italian and Portuguese, on the same rule the German and Spanish
    # entries follow: the verb for arranging a time, never the noun for the
    # appointment itself. `/afspraak-maken/` is the plainest Dutch booking path
    # there is and no spelling in this set came close to it.
    "afspraak", "afspraken", "reserveren", "prenota", "prenotazione",
    "agendar", "agendamento",
})
# And the words that turn a booking word into the small print about one. This is
# the whole reason the segment is read as words: "booking-terms" and "booking"
# differ by a noun, and only one of them is a calendar.
_BOOKING_PATH_STOP = frozenset({
    "terms", "term", "policy", "policies", "conditions", "cancellation",
    "cancel", "fee", "fees", "faq", "faqs", "guide", "guides", "tips",
    "blog", "news", "article", "articles", "club", "clubs", "review",
    "reviews", "shop", "store", "keeping", "software", "system", "systems",
    # "/e-book/" is a lead magnet and its segment is two words, one of which is
    # "book". A downloadable PDF is not a calendar.
    "e", "ebook", "free", "download", "downloads", "pdf", "brochure",
    "catalogue", "catalog", "recipe", "recipes", "sample", "samples",
})
# A verb and its object, in the order a button prints them. "Book Appointment",
# "Book online", "Make a booking" and "Schedule your visit" are one control with
# four spellings, and the old phrase list held two of them.
_BOOKING_VERBS = frozenset({
    "book", "booking", "bookings", "schedule", "reserve", "reservation",
    "reservations", "buchen", "reserver", "réserver", "reservar",
    "afspraak", "reserveren", "prenota", "agendar",
})
_BOOKING_OBJECTS = frozenset({
    "online", "now", "today", "appointment", "appointments", "table", "visit",
    "time", "times", "slot", "consultation", "session", "here", "us", "in",
    "a", "an", "your", "my", "the", "ligne", "termin", "maken", "ora", "hora",
})
_BOOKING_LEADS = frozenset({"make", "request", "online", "instant", "easy",
                            "quick", "click", "to", "termin"})
# The phrases the verb-and-object rule cannot express, because the verb is not
# in them at all.
_BOOKING_LABELS = ("pedir cita online", "cita online", "cita previa online",
                   "rendez vous en ligne", "prendre rendez vous")

def _phone_present(text: str) -> bool:
    """True when `text` prints something a reader would dial."""
    for pattern in _PHONE_RES:
        for match in pattern.finditer(text):
            low, high = _PHONE_DIGITS
            if low <= len(_DIGIT_RE.findall(match.group(0))) <= high:
                return True
    return False


def _clean_text(fragment: str) -> str:
    """Tag-free, entity-decoded, whitespace-collapsed text from a markup fragment."""
    out = _TAG_RE.sub(" ", fragment or "")
    out = _html.unescape(out)
    return _WS_RE.sub(" ", out).strip()


def _phrase_text(text: str) -> str:
    """`text` flattened to the shape the phrase lists below are written in.

    Lower case, ASCII quotes, and every dash, comma and stop turned into a
    space, so a phrase is matched on its words and not on how the page
    punctuated them. One entry now covers `walk-ins only`, `walk ins only` and
    `walk‑ins only`, which is three fewer strings to remember and one fewer way
    to miss the sentence in which a business says outright that it books nothing.

    Letters keep their accents — `réserver` and `verstärkung` are words in the
    lists — so this strips punctuation rather than everything but ASCII.
    """
    flat = str(text or "").lower().translate(_TYPOGRAPHY)
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", flat)).strip()


def _says(texts, phrases) -> str:
    """The first of `phrases` that one of `texts` says as whole words, or "".

    Whole words, because "appointment" sits inside "disappointment" and "quote"
    inside "quotes" — and this is the last place in the module where a phrase
    list is matched against a page, so it is the last place the substring
    mistake can still be made.
    """
    if isinstance(texts, str):
        texts = (texts,)
    for text in texts:
        padded = " %s " % text
        for phrase in phrases:
            if " %s " % phrase in padded:
                return phrase
    return ""


def _visible_text(html: str) -> str:
    body = _COMMENT_RE.sub(" ", html)
    body = _SCRIPT_RE.sub(" ", body)
    return _clean_text(body)


def _attrs(tag: str) -> dict:
    out = {}
    for name, dq, sq, bare in _ATTR_RE.findall(tag):
        out[name.lower()] = dq or sq or bare
    return out


def _host(url: str) -> str:
    host = urllib.parse.urlsplit(url if "//" in url else "//" + url).netloc.lower()
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _path(url: str) -> str:
    return urllib.parse.urlsplit(url).path or "/"


class _Page:
    """One fetched page, pre-chewed into the views detection needs.

    `heads` and `listed` are here because they were being recomputed. `_HEAD_RE`
    ran over every page three times — once in `_headings`, once in `_services`,
    once inside `_listed_services` — and `_listed_services` itself ran twice per
    page, because `_signals` wants the site's own list of what it sells and
    `_services` wants the same list filtered. Both are lazy: a page whose
    headings nobody asks for never pays for them.
    """

    __slots__ = ("url", "path", "html", "low", "text", "links", "_heads", "_listed",
                 "_listed_linked", "_forms")

    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.path = _path(url)
        self.html = html[:_SCAN_CAP]
        self.low = self.html.lower()
        self.text = _visible_text(self.html)
        self.links = [(href.strip(), _clean_text(label)[:80])
                      for href, label in _A_RE.findall(self.html)]
        self._heads = None
        self._listed = None
        self._listed_linked = 0
        self._forms = None

    @property
    def heads(self) -> list:
        """[(level, cleaned text)] for every h1-h3 on the page, in order."""
        if self._heads is None:
            self._heads = [(level, _clean_text(body))
                           for level, body in _HEAD_RE.findall(self.html)]
        return self._heads

    @property
    def forms(self) -> list:
        """Every `<form>` block on the page, found once.

        `.*?` across a page of markup is the most expensive pattern in this
        module, and two rules want the same blocks: one counts the forms and
        one measures the longest. Running it twice per page doubled that cost
        for a list already sitting in memory.
        """
        if self._forms is None:
            self._forms = _FORM_RE.findall(self.html)
        return self._forms


# A marker that opens with a host label — `force.com`, `resy.com`, `clarity.ms`.
# Everything else in the tables is a path, an attribute or a JavaScript
# identifier, and those are matched plainly.
_HOSTISH_RE = re.compile(r"[a-z0-9][a-z0-9\-]*\.")


def _present(low: str, marker: str) -> bool:
    """Is `marker` on the page, as itself rather than as the tail of a word?

    The tables are read as structural facts and then reported as absences, so
    both directions of a wrong answer end up in a live email. The shape that
    kept biting is a host-shaped marker landing inside a longer host:
    `force.com` inside `workforce.com` said Salesforce, `resy.com` would say
    Resy inside `pressy.com`, and each one silently deleted a true finding. So a
    marker that begins with a host label has to begin where a host begins —
    after a slash, a dot, a quote, an equals sign, anything that is not another
    label character. A path or an identifier marker is unaffected.
    """
    # The search runs first and the shape test only on a hit. Six hundred
    # markers are checked against every crawl and all but a dozen of them are
    # absent, so the miss path is the hot one and it now costs exactly what the
    # plain `in` it replaced cost: one `find` that returns -1.
    at = low.find(marker)
    if at < 0:
        return False
    if not _HOSTISH_RE.match(marker):
        return True
    while at != -1:
        before = low[at - 1] if at else ""
        if not (before.isalnum() or before == "-"):
            return True
        at = low.find(marker, at + 1)
    return False


def _hit(low: str, markers) -> int:
    return sum(1 for m in markers if _present(low, m))


def _any_hit(low: str, markers) -> bool:
    return any(_present(low, m) for m in markers)


def _best_vendor(low: str, table) -> str:
    """Highest-scoring vendor in `table`; ties go to catalogue order."""
    best, best_score = "", 0
    for name, markers in table:
        score = _hit(low, markers)
        if score > best_score:
            best, best_score = name, score
    return best


def _all_vendors(low: str, table) -> list[str]:
    # `any`, not a tally: nothing reads how *many* markers a vendor matched, and
    # the absent case — the one that becomes a sentence — costs the same either
    # way. Only the vendors that are there get to stop early, which is most of
    # the scanning on a site that runs a normal stack.
    return [name for name, markers in table if _any_hit(low, markers)]


# ── Why a site has nothing to say ──

# The operator asked the plain question — which site is not reachable? — and the
# audit's answer was a boolean with the reason thrown away, so a lead that had
# moved to a new domain, one whose certificate expired on Sunday and one that
# simply times out all read the same and none of them could be acted on. Each
# sentence here is written for the person looking at the Leads table, says what
# went wrong in their words, and is short enough for a column.
UNREACHABLE_REASONS: dict[str, str] = {
    "no_url": "there is no website on the record",
    "dns": "the domain name does not resolve",
    "refused": "the server refused the connection",
    "timeout": "the server did not answer in time",
    "tls": "the security certificate could not be verified",
    "reset": "the server dropped the connection",
    "redirect_loop": "the address redirects in a loop",
    "http_401": "the site asks for a password",
    "http_403": "the server turned the request away",
    "http_404": "the home page is gone",
    "http_410": "the home page has been taken down",
    "http_429": "the server is turning away requests for now",
    "http_500": "the site is returning a server error",
    "http_503": "the site is temporarily unavailable",
    "http_error": "the server answered with an error",
    "not_html": "the address does not serve a web page",
    "empty": "the page came back empty",
    "parked": "the domain is parked and has no site on it",
    "under_construction": "the site is a coming-soon placeholder",
    "challenge": "a bot check stands in front of the site",
    "cookie_wall": "a cookie notice stands in front of the site",
    "js_only": "the page carries no readable text without JavaScript",
    "unreachable": "the site could not be reached",
}

_HTTP_REASONS: dict[int, str] = {
    401: "http_401", 403: "http_403", 404: "http_404", 410: "http_410",
    429: "http_429", 500: "http_500", 502: "http_500", 503: "http_503",
    504: "timeout",
}

# Read against whatever a fetcher put in `error`. `core.enrich._short_error`
# writes one lowercase word, `_fetch` below writes an exception class name, and
# both land here rather than each growing its own vocabulary.
_ERROR_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("no url",), "no_url"),
    (("gaierror", "getaddrinfo", "name or service", "nodename", "dns"), "dns"),
    (("timed out", "timeout"), "timeout"),
    (("certificate", "sslcert", "sslerror", "ssl", "tls"), "tls"),
    (("refused",), "refused"),
    (("reset", "remotedisconnected", "incompleteread", "badstatusline"), "reset"),
    (("redirect", "infinite loop"), "redirect_loop"),
    (("content-type", "not html"), "not_html"),
    (("empty response", "empty"), "empty"),
)


def unreachable_reason(error: str = "", status: int = 0) -> str:
    """A `UNREACHABLE_REASONS` code for a failed fetch. "" when nothing failed.

    Never raises and never invents: an error nobody has a word for comes back
    as "unreachable", which is exactly what the audit used to say about all of
    them.
    """
    text = str(error or "").strip().lower()
    match = re.search(r"http\D{0,3}(\d{3})", text)
    code = int(match.group(1)) if match else int(status or 0)
    if code >= 400:
        return _HTTP_REASONS.get(code, "http_error")
    for words, reason in _ERROR_WORDS:
        if any(word in text for word in words):
            return reason
    return "unreachable" if text else ""


def unreachable_detail(reason: str) -> str:
    """`reason` as the sentence the Leads table shows. Unknown codes read blank."""
    return UNREACHABLE_REASONS.get(str(reason or ""), "")


# A page can answer with 200 and still tell you nothing: a bot check, a consent
# wall, a parked domain, a coming-soon splash, a framework shell that renders
# everything after the crawler has gone. Every rule in the catalogue asks "is
# there a chat script / a form / any markup here", so all five answer "no" to
# all of it, and a lead whose site was never actually read gets an email listing
# seven things wrong with it. That is the worst output this module can produce,
# and it produced it on six of thirty-six real page shapes.
_CHALLENGE_MARKERS: tuple[str, ...] = (
    "cdn-cgi/challenge-platform", "cf-browser-verification", "cf_chl_", "__cf_chl",
    "just a moment", "checking your browser", "verifying you are human",
    "attention required! | cloudflare", "ddos protection by cloudflare",
    "_incapsula_resource", "incapsula incident", "distil_r_captcha",
    "perimeterx", "px-captcha", "/_sec/cp_challenge",
    "enable javascript and cookies to continue", "please verify you are a human",
)
_PARKED_MARKERS: tuple[str, ...] = (
    "parkingcrew.net", "sedoparking.com", "bodis.com", "afternic.com",
    "hugedomains.com", "domainmarket.com", "undeveloped.com", "above.com/park",
    "parklogic",
)
_PARKED_PHRASES: tuple[str, ...] = (
    "this domain is for sale", "buy this domain", "domain is for sale",
    "the domain is parked", "this domain name is for sale",
    "future home of something quite cool", "welcome to nginx",
    "apache2 ubuntu default page", "index of",
)
_CONSTRUCTION_PHRASES: tuple[str, ...] = (
    "coming soon", "under construction", "launching soon", "opening soon",
    "new website coming", "we are working on", "en construction",
    "im aufbau", "demnächst", "próximamente", "proximamente", "en construcción",
    "binnenkort online", "in aanbouw",
)
_CONSENT_PHRASES: tuple[str, ...] = (
    "we use cookies", "this website uses cookies", "this site uses cookies",
    "accept all cookies", "accept cookies", "cookie settings", "manage cookies",
    "cookie policy", "cookie preferences",
    "deze website gebruikt cookies", "wij gebruiken cookies", "alles accepteren",
    "diese website verwendet cookies", "wir verwenden cookies", "alle akzeptieren",
    "ce site utilise des cookies", "nous utilisons des cookies", "tout accepter",
    "este sitio utiliza cookies", "utilizamos cookies", "aceptar todo",
)
_SHELL_MARKERS: tuple[str, ...] = (
    "__next_data__", "/_next/static", "__nuxt__", "/_nuxt/", "/_app/immutable",
    'id="root"', "id='root'", 'id="app"', "id='app'", "ng-app", "ng-version",
    "data-reactroot", "window.__initial_state__", "__remix_context__",
    "ember-app", "gatsby-focus-wrapper",
)

# Two thresholds about how much a person could read off the page: a splash is
# short, and a consent wall is shorter. The third is deliberately not a length
# on its own — see `_has_substance`.
_SPLASH_MAX = 1500
_WALL_MAX = 900
_MIN_READABLE = 400


def _has_substance(pages, low: str) -> bool:
    """Does the crawl carry structure a person could read, however few words?

    Length alone was the first attempt at this and it was wrong in the
    expensive direction: a one-page brochure for a solicitor is a heading, three
    services and a phone number, and it can be under two hundred characters of
    visible text while being a completely real site. Treating it as an empty
    shell threw away every finding on it. What separates a real page from a
    framework's leftovers is not how much it says but that it says anything at
    all in a structure — a heading, a way to get in touch, a menu.
    """
    if any(body.strip() for page in pages for _level, body in page.heads):
        return True
    if "mailto:" in low or "tel:" in low or _FORM_OPEN_RE.search(low):
        return True
    if sum(1 for page in pages for _href, label in page.links
           if len(label.strip()) > 2) >= 3:
        return True
    # A printed number or a street address is the whole content of plenty of
    # small sites, and it is the part a person would act on.
    text = " ".join(page.text for page in pages)
    return _phone_present(text) or any(p.search(text) for p in _POSTAL_RES)


def _headline(pages) -> str:
    """The home page's title and its top headings, flattened for phrase matching.

    "Coming soon" belongs in the title or over the page. Half way down a real
    site it is a note about a new location opening, and reading that as a dead
    site would drop a live lead.
    """
    if not pages:
        return ""
    match = _TITLE_RE.search(pages[0].html)
    parts = [_clean_text(match.group(1))] if match else []
    parts.extend(body for _level, body in pages[0].heads[:4])
    return _phrase_text(" ".join(parts))


def _unreadable(pages, low: str, text: str) -> str:
    """"" when the crawl carries a page worth auditing, else why it does not."""
    if not pages:
        return "empty"
    size = len(text.strip())
    flat = _phrase_text(text)

    if size < _SPLASH_MAX and _any_hit(low, _CHALLENGE_MARKERS):
        return "challenge"
    if _any_hit(low, _PARKED_MARKERS) or (size < _SPLASH_MAX
                                          and _says(flat, _PARKED_PHRASES)):
        return "parked"
    if size < _SPLASH_MAX and _says(_headline(pages), _CONSTRUCTION_PHRASES):
        return "under_construction"
    if (size < _WALL_MAX and _says(flat, _CONSENT_PHRASES)
            and "mailto:" not in low and "tel:" not in low
            and not _FORM_OPEN_RE.search(low)):
        return "cookie_wall"
    if size < _MIN_READABLE and not _has_substance(pages, low):
        return "js_only" if _any_hit(low, _SHELL_MARKERS) else "empty"
    return ""


# ── Dates ──


def _parse_dates(blob: str, today: datetime.date) -> list[datetime.date]:
    """Every plausible past date in `blob`. Future and pre-2000 dates are noise."""
    found: list[datetime.date] = []

    def _keep(year: int, month: int, day: int) -> None:
        try:
            when = datetime.date(year, month, day)
        except ValueError:
            return
        if datetime.date(2000, 1, 1) <= when <= today:
            found.append(when)

    for year, month, day in _ISO_DATE_RE.findall(blob):
        _keep(int(year), int(month), int(day))
    for month, day, year in _MDY_RE.findall(blob):
        _keep(int(year), _MONTHS[month[:3].lower()], int(day))
    for day, month, year in _DMY_RE.findall(blob):
        _keep(int(year), _MONTHS[month[:3].lower()], int(day))
    return found


def _latest_content_date(pages, today: datetime.date):
    """Newest date the site shows for its own content, or None.

    Machine-readable dates (`<time datetime>`, `article:published_time`,
    JSON-LD `datePublished`) are trusted on their own. Dates in visible text are
    only read from pages that look like a blog or article, because "call before
    31 December" on a promo page is not a publishing date.
    """
    dates: list[datetime.date] = []
    for page in pages:
        machine = " ".join(_DATETIME_ATTR_RE.findall(page.html))
        machine += " " + " ".join(_DATE_JSON_RE.findall(page.html))
        machine += " " + " ".join(_ARTICLE_TIME_RE.findall(page.html))
        dates.extend(_parse_dates(machine, today))

        article_like = bool(_BLOG_HREF_RE.search(page.path)) or "<article" in page.low
        if article_like:
            dates.extend(_parse_dates(page.text[:40_000], today))
    return max(dates) if dates else None


# ── Forms ──


# "search" where a word starts, not wherever the five letters land. The plain
# substring is inside "research", so a form posting to `/research-request` was
# thrown away as the site's search box — and a site whose only form is thrown
# away is told that nothing on it asks a visitor for a name. Bounded on the left
# only, because `searchform` and `search-form` are both the box this rejects.
_SEARCHY_RE = re.compile(r"(?<![a-z])search")


def _is_contact_form(block: str) -> bool:
    """A form a prospect writes to, as opposed to the site's search box."""
    low = block.lower()
    opening = low[:low.find(">") + 1] if ">" in low else low
    if _SEARCHY_RE.search(opening) or 'role="search"' in low:
        return False
    if 'type="search"' in low or "type='search'" in low:
        return False
    if "<textarea" in low or 'type="email"' in low or "type='email'" in low:
        return True
    return bool(re.search(r"""name\s*=\s*["'][^"']*(?:email|message|enquiry|inquiry|comment|phone)""", low))


def _embedded_forms(pages) -> bool:
    """A third-party form embed, read from the URLs that load it and not from the copy.

    `formidable` is a plugin directory and an ordinary English adjective both, so
    a page that said "a formidable reputation" was credited with a lead form:
    `no_crm_signals` went out about a form nobody can see, and `no_lead_capture`
    — the finding that was actually true of that site — stayed silent.
    """
    for page in pages:
        urls = " ".join(_URL_ATTR_RE.findall(page.low))
        if any(embed in urls for embed in _FORM_EMBEDS):
            return True
    return False


def _lead_forms(page) -> int:
    """How many forms on `page` ask a visitor for something. Search boxes are not forms.

    One filter, counted once, because two rules read this number against each
    other: `no_lead_capture` needs it to be zero and `no_crm_signals` needs it to
    be more. Counting raw `<form` tags here while `_is_contact_form` rejected the
    search box made a site with nothing but a search box fire both — it was told
    its contact form had no CRM behind it, and the true finding, that nothing on
    the site asks for a name, was suppressed by the same box.
    """
    blocks = page.forms
    if blocks:
        return sum(1 for block in blocks if _is_contact_form(block))
    # Some builders never close the <form>, so fall back to the whole page.
    if _FORM_OPEN_RE.search(page.html) and _is_contact_form(page.html):
        return len(_FORM_OPEN_RE.findall(page.html))
    return 0


# What a visitor actually has to answer, as opposed to what a builder puts in a
# <form>. Hidden fields, the submit button and the nonce are the form talking to
# the server; a row of radio buttons sharing one name is one question asked
# once; and a honeypot is a field the visitor is never shown and a bot fills in.
# Counting raw <input> tags reads a four-question contact form as an eleven-part
# interrogation on any builder that ships a nonce and two hidden ids, which is
# most of them, and the gap below is a sentence about how much the reader is
# asking of a stranger.
_FIELD_RE = re.compile(r"(?is)<(input|select|textarea)\b([^>]*)>")
_FIELD_SKIP_TYPES = frozenset({"hidden", "submit", "button", "image", "reset", "search"})
_HONEYPOT_RE = re.compile(r"(?i)honey|hpot|_hp\b|\bhp_|botfield|nickname|leaveblank|"
                          r"leave_blank|antispam|spamtrap")
_HIDDEN_STYLE_RE = re.compile(r"(?i)display\s*:\s*none|visibility\s*:\s*hidden")

# Spelt out, because a digit in the bracket of a live email is crawler state
# wearing a sentence. Past twenty the exact number stops being the point.
_COUNT_WORDS: dict[int, str] = {
    8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
    14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
    19: "nineteen", 20: "twenty",
}


def _count_word(total: int) -> str:
    return _COUNT_WORDS.get(int(total), "more than twenty")


def _form_fields(block: str) -> int:
    """How many separate things `block` asks a visitor to answer."""
    groups: set[str] = set()
    total = 0
    for tag, attrs_text in _FIELD_RE.findall(block):
        attrs = _attrs("<" + tag + " " + attrs_text + ">")
        kind = str(attrs.get("type") or "").strip().lower()
        if tag.lower() == "input" and kind in _FIELD_SKIP_TYPES:
            continue
        name = str(attrs.get("name") or attrs.get("id") or "").strip()
        if _HONEYPOT_RE.search(name) or _HIDDEN_STYLE_RE.search(str(attrs.get("style") or "")):
            continue
        if kind in ("radio", "checkbox"):
            key = name.lower() or ("%s#%d" % (kind, total))
            if key in groups:
                continue
            groups.add(key)
        total += 1
    return total


def _longest_form(pages) -> int:
    """The most questions any one lead form on the site asks.

    Reads `page.forms`, which `_lead_forms` has already built for every page in
    the crawl, so this pass costs the fields of a handful of small blocks and
    not a second `<form>...</form>` scan over the whole site.
    """
    longest = 0
    for page in pages:
        for block in page.forms:
            if _is_contact_form(block):
                longest = max(longest, _form_fields(block))
    return longest


# ── Technology ──


def _tech(pages, low: str) -> dict:
    """The stack, read from one lowercased copy of the crawl.

    `low` is passed in rather than joined here: `_signals` needs the same string
    and used to build its own, which walked every byte of every page a second
    time to produce a value already sitting in memory.
    """
    forms = sum(_lead_forms(p) for p in pages)

    cms = _best_vendor(low, _CMS_MARKERS)
    ecommerce = _best_vendor(low, _ECOMMERCE_MARKERS)
    builder = _best_vendor(low, _BUILDER_MARKERS)
    # A Shopify storefront is its own CMS; a WooCommerce shop is still WordPress.
    if not cms and ecommerce == "shopify":
        cms = "shopify"
    # And a page built in Elementor is a WordPress page whatever the markup says
    # about where its assets are served from.
    if not cms and builder in _WORDPRESS_BUILDERS:
        cms = "wordpress"
    if not cms and pages:
        cms = "custom"

    return {
        "cms": cms,
        "builder": builder,
        "ecommerce": ecommerce,
        "analytics": _all_vendors(low, _ANALYTICS_MARKERS),
        "chat": _best_vendor(low, _CHAT_MARKERS),
        "booking": _best_vendor(low, _BOOKING_MARKERS),
        "crm": _best_vendor(low, _CRM_MARKERS),
        "forms": forms,
        "frameworks": _all_vendors(low, _FRAMEWORK_MARKERS),
    }


# ── Page facts ──


def _document_name(label: str, href: str) -> str:
    """What a downloadable form is called, in the words its owner uses for it.

    Their own link text first, then the file name with the slug taken out of it.
    Anything still shaped like machinery is no name at all and "" comes back, so
    the sentence can fall to "the paperwork" — which beats handing the reader
    employment-application-form.pdf and calling it evidence.
    """
    stem = href.split("?")[0].split("#")[0].rsplit("/", 1)[-1]
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    stem = _WS_RE.sub(" ", re.sub(r"[\-_+%]+", " ", stem))
    for candidate in (label, stem):
        name = _DOC_LEAD_RE.sub("", str(candidate or "").strip()).strip().lower()
        if not (8 <= len(name) <= 40) or len(name.split()) < 2:
            continue
        if not _DOC_NAME_RE.fullmatch(name):
            continue
        if not _PDF_FORM_RE.search(name):
            continue
        return name if name.startswith("the ") else "the " + name
    return ""


def _headings(pages) -> list[str]:
    """Every h1-h3 the site prints, cleaned down to the words in it."""
    return [body.lower().strip(" :·•-–—")
            for page in pages for _level, body in page.heads]


def _bookable(services) -> bool:
    """True when the site's own list of what it sells is work booked for a time."""
    return any(w in _phrase_text(name) for name in services for w in _BOOKABLE_WORDS)


def _controls(pages, headings) -> list[str]:
    """Every phrase on the site a visitor can click or is led by, and nothing else.

    Headings, link labels and button text, each kept whole so a phrase cannot
    form across the join between two of them. A rule that reads this is asking
    what the site *offers*; a rule that reads the visible text is asking what
    the site happens to mention, and the two answers differ most on the pages
    where being wrong costs the most — "no need to request a quote by email" is
    a broker whose whole pitch is that you never have to, read as a business
    that quotes by hand.
    """
    parts: list[str] = list(headings)
    for page in pages:
        parts.extend(label for _href, label in page.links)
        parts.extend(_clean_text(body) for body in _BUTTON_RE.findall(page.html))
        for tag in _SUBMIT_RE.findall(page.html):
            parts.append(_attrs(tag).get("value", ""))
    return [_phrase_text(p) for p in parts if p]


def _booking_control(href: str, label: str) -> bool:
    """A link a visitor clicks to arrange a time, read as words rather than spellings.

    The path is read one segment at a time and the segment is read as the words
    in it, so `/online-booking/` and `/book-a-table/` are calendars and
    `/booking-terms/` is the small print under an order form. The label is read
    as a verb and its object, so "Book Appointment" and "Make a booking" are the
    same control that "Book online" was — and "Booking terms", which has the
    verb and no object, is not a control at all.
    """
    for segment in _path(href).split("/"):
        words = set(_WORD_RE.findall(_phrase_text(segment)))
        if (words & _BOOKING_PATH_WORDS) and not (words & _BOOKING_PATH_STOP):
            return True

    flat = _phrase_text(label)
    if _says(flat, _BOOKING_LABELS):
        return True
    words = _WORD_RE.findall(flat)
    for index, word in enumerate(words):
        if word not in _BOOKING_VERBS:
            continue
        if set(words[index + 1:index + 4]) & _BOOKING_OBJECTS:
            return True
        if set(words[max(0, index - 2):index]) & _BOOKING_LEADS:
            return True
    return False


# Anything a visitor could pick a time out of. Read only to *disprove* a
# booking system, so the list is deliberately loose and every entry that is also
# an ordinary English word — "calendar", "availability" — costs a finding rather
# than buying a false one: a page that merely mentions availability keeps the
# benefit of the doubt and the gap stays quiet.
_CALENDAR_MARKERS: tuple[str, ...] = (
    "datepicker", "date-picker", "flatpickr", "fullcalendar", "timeslot",
    "time-slot", "timekit", "calendar", "availability", "verfügbarkeit",
    "disponibilit", "beschikbaar", "<iframe",
)
_DATE_INPUT_RE = re.compile(
    r"""(?is)<input\b[^>]*type\s*=\s*["']?(?:date|time|datetime-local)""")


def _shows_a_calendar(page) -> bool:
    """Could a visitor choose a time on this page, by any means at all?

    Deliberately does not re-scan for a booking vendor: the only caller asks
    this when `tech["booking"]` is empty, and `tech` was read over the whole
    crawl, this page included. Running the eighty-vendor table again here was
    a hundred and seventy string searches for an answer already on the table.
    """
    if _DATE_INPUT_RE.search(page.html):
        return True
    return _any_hit(page.low, _CALENDAR_MARKERS)


# ── Prices ──

# What a published price looks like when it is one. A figure on its own is not:
# the same pattern finds "$50 off your first service" and "no deposit, $0 down",
# and three of those read as a rate card. So a price is a figure the page
# attaches to something it sells — a table cell, a list item, a "from", a "per
# hour", a section headed Fees — and a figure inside an offer is not a price at
# all. The old rule asked only for three matches anywhere in the whole crawl,
# which is the wrong question twice over: it read a discount sheet as a price
# list, and it read a page publishing two prices as a page publishing none.
_PRICE_LEAD_RE = re.compile(
    r"(?i)\b(?:from|starting|starts|start|as low as|only|just|priced|price[ds]?|"
    r"fee|fees|rate|rates|cost[s]?|charge[ds]?|ab|desde|[àa] partir de)"
    r"\b[^.;:!?]{0,20}$")
_PRICE_TRAIL_RE = re.compile(
    r"(?i)^\s*(?:\+\s*(?:hst|gst|pst|vat|tax)\b|(?:/|\s+)(?:per\s+|pro\s+|par\s+)?"
    r"(?:hr|hour|day|night|week|month|year|session|visit|treatment|person|head|"
    r"adult|child|guest|unit|item|sq\s?ft|m2|each|ea|pp|mo|yr)\b)")
# Both anchored to the figure, because a discount word merely *near* one is a
# different sentence: "we accept credit cards. Haircuts $25" has "credit" eleven
# words away, and reading that as a coupon would delete a published price.
_DISCOUNT_LEAD_RE = re.compile(
    r"(?i)\b(?:save|saves|saving|savings|discount|discounted|coupon|voucher|"
    r"rebate|cashback|win|won|prize|worth|valued|raised|donat\w*|financ\w*|"
    r"no\s+obligation)\b[^.;:!?]{0,20}$")
_DISCOUNT_TRAIL_RE = re.compile(
    r"(?i)^\s*(?:off|back|credit|discount|discounted|rebate|voucher|cashback|"
    r"down|deposit|value)\b")
# Whole words, not fragments: "fee" is inside "coffee", and a heading reading
# "Coffee and cake" would otherwise have counted as a price list.
_PRICE_HEADING_WORDS = frozenset({
    "price", "prices", "pricing", "rate", "rates", "fee", "fees", "tariff",
    "tariffs", "preis", "preise", "preisliste", "tarif", "tarifs", "precios",
    "prezzi",
})
# Everything else a pricing section is called already carries one of the words
# above; this is the one that does not.
_PRICE_HEADING_PHRASES = ("how much",)
_PRICE_CELL_RE = re.compile(r"(?is)<(li|td|dd|h2|h3|h4)\b[^>]*>(.*?)</\1\s*>")
_BUTTON_RE = re.compile(r"(?is)<button\b[^>]*>(.*?)</button\s*>")
_SUBMIT_RE = re.compile(r"""(?is)<input\b[^>]*type\s*=\s*["']?submit[^>]*>""")


def _offer_shaped(text: str, start: int, end: int) -> bool:
    """True when the figure is what a page takes off a price rather than the price."""
    return bool(_DISCOUNT_LEAD_RE.search(text[max(0, start - 40):start])
                or _DISCOUNT_TRAIL_RE.match(text[end:end + 24]))


def _price_spans(text: str) -> list:
    """(figure, start, end) for money on the page, offers taken back out."""
    return [(m.group(0), m.start(), m.end()) for m in _MONEY_RE.finditer(text)
            if not _offer_shaped(text, m.start(), m.end())]


def _real_prices(text: str) -> list[str]:
    """Money on the page with the offers taken back out of it."""
    return [figure for figure, _start, _end in _price_spans(text)]


def _price_heading(headings) -> bool:
    for heading in headings:
        flat = _phrase_text(heading)
        if set(_WORD_RE.findall(flat)) & _PRICE_HEADING_WORDS:
            return True
        if _says(flat, _PRICE_HEADING_PHRASES):
            return True
    return False


def _publishes_prices(text: str, html: str, headings, pricing_link: bool) -> bool:
    """True when the site puts a figure next to something a customer can buy."""
    if pricing_link:
        return True
    # One pass over the text, not two. The old shape walked every character of
    # the crawl with `_MONEY_RE` to collect the figures and then walked it again
    # to look at their surroundings, and on a six-page crawl that is the single
    # most expensive regex in the module run twice for one answer.
    spans = _price_spans(text)
    if not spans:
        return False
    if _price_heading(headings):
        return True
    for _tag, body in _PRICE_CELL_RE.findall(html):
        cell = _clean_text(body)
        if cell and _real_prices(cell):
            return True
    for _figure, start, end in spans:
        if (_PRICE_LEAD_RE.search(text[max(0, start - 40):start])
                or _PRICE_TRAIL_RE.match(text[end:end + 24])):
            return True
    priced = [figure for figure, _s, _e in spans]
    # The loose arm the rule started as, kept at its old threshold so nothing
    # that used to read as a price list stops reading as one: three *different*
    # figures, none of them an offer. Counting repeats let one price printed
    # three times in a sticky header stand in for three prices.
    return len({p.replace(" ", "") for p in priced}) >= 3


def _social_links(pages) -> list[str]:
    out = []
    for page in pages:
        for href, _label in page.links:
            low = href.lower()
            host = _host(low)
            if not any(host == h or host.endswith("." + h) for h in _SOCIAL_HOSTS):
                continue
            if _SOCIAL_JUNK_RE.search(low):
                continue
            # A bare host with no profile path is a broken footer icon.
            if not urllib.parse.urlsplit(low if "//" in low else "//" + low).path.strip("/"):
                continue
            out.append(href)
    return out


_ADDRESS_TAG_RE = re.compile(r"(?i)<address\b")
# The branch list a chain publishes for Google, in the one place it is written
# down as data. The printed patterns above are Canada, the US and the UK and
# nothing else, so a four-branch optician in Berlin and a Dutch chain read as
# single-site businesses and `multi_location` — a gap about a message arriving
# for one branch and having to be walked to it — could not fire outside three
# countries. A postal code inside a PostalAddress needs no country pattern: the
# markup has already said what it is.
_SCHEMA_POSTCODE_RE = re.compile(
    r'(?is)"postal_?code"\s*:\s*"([^"]{2,12})"'
    r"""|itemprop\s*=\s*["']postalCode["'][^>]*>\s*([^<]{2,12})""")
_SCHEMA_STREET_RE = re.compile(r'(?is)"street_?address"\s*:\s*"([^"]{4,90})"')


def _location_count(pages) -> int:
    codes: set[str] = set()
    for page in pages:
        text = page.text[:60_000]
        for pattern in _POSTAL_RES:
            for match in pattern.findall(text):
                codes.add(re.sub(r"[\s\-]", "", match).upper())
        for first, second in _SCHEMA_POSTCODE_RE.findall(page.html):
            code = (first or second).strip()
            if code:
                codes.add(re.sub(r"[\s\-]", "", code).upper())
    if len(codes) >= 2:
        return len(codes)

    streets = set()
    for page in pages:
        streets.update(_WS_RE.sub(" ", s).strip().lower()
                       for s in _SCHEMA_STREET_RE.findall(page.html))
    if len(streets) >= 2:
        return len(streets)

    addresses = sum(len(_ADDRESS_TAG_RE.findall(p.html)) for p in pages)
    return max(addresses, 1 if (codes or streets) else 0)


def _copyright_year(pages, today: datetime.date) -> int:
    best = 0
    for page in pages:
        # The notice lives at the bottom; searching the tail avoids picking up a
        # 1998 "established in" line from the body copy.
        for start, end in _COPYRIGHT_RE.findall(page.text[-4000:] or page.text):
            for year in (end, start):
                if not year:
                    continue
                value = int(year)
                if 1990 <= value <= today.year + 1:
                    best = max(best, value)
    return best


def _signals(pages, tech: dict, base_url: str, load_ms: int,
             low: str, text: str) -> tuple[dict, dict]:
    """Every boolean the gap table reads, plus the evidence bits it quotes.

    `low` and `text` arrive from `_audit`, which already built them for the
    stack scan and the readability check. Rebuilding them here joined every page
    twice more per lead for two strings that were sitting in the caller's frame.
    """
    today = datetime.date.today()
    html_all = "\n".join(p.html for p in pages)
    text_low = text.lower()
    # The same text with the page's typography flattened out of it, which is what
    # every phrase list below is matched against. See `_phrase_text`.
    flat = _phrase_text(text)
    headings = _headings(pages)
    controls = _controls(pages, headings)
    facts: dict = {}

    hrefs = [(href.lower(), label.lower()) for page in pages for href, label in page.links]

    # ── lead routes ──
    # The boolean and the tally are the same fact counted once, so no two rules
    # can read the same form differently.
    contact_form = tech["forms"] > 0 or _embedded_forms(pages)

    booking_link = any(_booking_control(href, label) for href, label in hrefs)
    # A vendor script or a link to a booking page is a booking system. The same
    # words in running prose are not: "Request an appointment" is the heading
    # over the contact form on every site that has no booking at all, and
    # reading it as proof of one deleted the finding those sites exist to
    # produce. Prose survives only as an anchor label, where it is a control.
    # And a link is a booking system only until the crawl opens the page behind
    # it. A page the site itself names for booking, carrying no vendor, no
    # calendar and no time to pick, is a contact form with a hopeful label on
    # it — and the business it belongs to is precisely the one this gap was
    # written for, which until now was the one business it could never fire on.
    #
    # Judged only when that page is in the crawl. A link out to somebody else's
    # calendar is never second-guessed from here.
    booking_pages = [p for p in pages if _booking_control(p.url, "")]
    booking_is_a_form = bool(booking_pages) and not tech["booking"] and not any(
        _shows_a_calendar(p) for p in booking_pages)
    has_booking = bool(tech["booking"]) or (booking_link and not booking_is_a_form)
    facts["booking_is_a_form"] = bool(booking_link and booking_is_a_form)
    if tech["booking"]:
        facts["booking_vendor"] = tech["booking"]

    has_phone = "tel:" in low or _phone_present(text)
    has_email = "mailto:" in low or "data-cfemail" in low or bool(_EMAIL_RE.search(text))

    # A newsletter is a lead route when a visitor can join it: an address field
    # or the mailing tool's own script. The word by itself is a promise in a
    # paragraph — "ask us to add you when you call" — and reading it as capture
    # hid the finding on sites that have no form at all.
    newsletter = _any_hit(low, _MAILING_LIST_MARKERS) or (
        any(w in text_low for w in ("newsletter", "mailing list", "subscribe to our"))
        and bool(_EMAIL_FIELD_RE.search(html_all)))

    # A control the visitor clicks or a section headed by one, on the same
    # reasoning as booking: the phrase in running prose says only that the page
    # mentions quoting, and a broker whose pitch is "no need to request a quote
    # by email" was told its quotes are handled by hand. Scoped to a control it
    # is also better evidence — the sentence quotes something the owner can
    # point at on their own home page.
    quote_phrase = _says(controls, _QUOTE_PHRASES)
    has_quote_form = bool(quote_phrase) and contact_form
    if quote_phrase:
        facts["quote_phrase"] = quote_phrase

    # How much the longest form on the site asks of a stranger. A home page
    # cannot answer this: the fourteen-field intake form is two clicks in, on
    # the page the visitor reaches after deciding to get in touch.
    facts["form_fields"] = _longest_form(pages) if contact_form else 0

    # ── content ──
    # Word-level matching, because "Newsletter" is not a news section.
    has_blog = any(_BLOG_HREF_RE.search(href) or (set(re.findall(r"[a-z]+", label)) & _BLOG_WORDS)
                   for href, label in hrefs)
    has_blog = has_blog or any(_BLOG_HREF_RE.search(p.path) for p in pages)
    # Only asked once there is a blog to date. `blog_year` reaches the model's
    # brief as "blog 2019", and a `datePublished` in the home page's JSON-LD is
    # not a blog — a machine shop with no news section anywhere was being
    # described to the model as having one, four years stale. Not looking is
    # also the cheap answer: this is three regexes over every page's full
    # markup, and most sites have no blog at all.
    latest = _latest_content_date(pages, today) if has_blog else None
    blog_year = latest.year if latest else 0
    blog_stale = bool(latest and (today - latest).days > STALE_BLOG_DAYS)
    if latest:
        facts["latest_date"] = latest.strftime("%B %Y")

    # A link to a jobs page, a section headed by one, or a sentence that is a
    # vacancy however it is phrased. An address labelled "Careers" is a way to
    # reach somebody, not a listing, so the mail links are stepped over.
    careers = any(_CAREER_LINK_RE.search(href) for href, _ in hrefs
                  if not href.startswith(("mailto:", "tel:")))
    careers = careers or any(_CAREER_LINK_RE.search(p.path) for p in pages)
    careers = careers or any(h in _CAREER_HEADINGS for h in headings)
    careers = careers or bool(_says(flat, _HIRING_PHRASES))
    # Where the CVs go when the vacancy has no form under it. The gap is the
    # same one either way; the sentence is better when it can say what the
    # reader will see on their own careers page.
    facts["careers_mailbox"] = careers and any(
        href.startswith("mailto:") and (_JOB_MAIL_RE.search(href) or _JOB_MAIL_RE.search(label))
        for href, label in hrefs)

    pdf_form = ""
    # One pass over the downloads for two findings: paperwork to fill in, and a
    # price list the business publishes instead of printing a rate on a page.
    # The second is why `price_opaque` can no longer fire here — "not a rate on
    # any page" is a false sentence to a reader whose home page links one.
    price_document = False
    price_doc_year = 0
    for href, label in hrefs:
        if ".pdf" not in href:
            continue
        slug = _SLUG_RE.sub(" ", href)
        if not pdf_form and (_PDF_FORM_RE.search(slug) or _PDF_FORM_RE.search(label)):
            pdf_form = href
            facts["pdf_form"] = _document_name(label, href)
        if not (_PRICE_DOC_RE.search(slug) or _PRICE_DOC_RE.search(label)):
            continue
        price_document = True
        for year in _DOC_YEAR_RE.findall(slug) + _DOC_YEAR_RE.findall(label):
            if int(year) <= today.year - STALE_DOCUMENT_YEARS:
                price_doc_year = max(price_doc_year, int(year))
    facts["price_document"] = price_document
    facts["price_doc_year"] = price_doc_year

    pricing_link = any("/pricing" in href or "/prices" in href or "/rates" in href
                       or label in ("pricing", "prices", "our prices", "rates")
                       for href, label in hrefs)
    has_pricing = _publishes_prices(text, html_all, headings, pricing_link)

    testimonials = any(w in text_low for w in (
        "testimonial", "what our clients say", "what our customers say", "customer reviews",
        "google reviews", "trustpilot", "5-star", "five star"))
    gallery = any("/gallery" in href or "/portfolio" in href or "/our-work" in href
                  or "/projects" in href or label in ("gallery", "portfolio", "our work")
                  for href, label in hrefs)
    gallery = gallery or any(w in low for w in ("fancybox", "photoswipe", "lightbox"))

    # Click-to-chat endpoints only, read off the hrefs. A business that put a
    # green button on its site is proud of it and answers it personally, which
    # is the whole finding — not that WhatsApp is missing, that a person is
    # behind it at nine at night.
    whatsapp = any(_present(href, route) for href, _label in hrefs
                   for route in _WHATSAPP_ROUTES)
    # Read against the whole page rather than the hrefs, because half of these
    # are a script that draws the stars and never renders an anchor at all. A
    # Trustpilot widget on the page makes "nothing points a customer at a review"
    # false whether or not the markup contains a link to trustpilot.com.
    review_route = _any_hit(low, _REVIEW_ROUTES)

    locations = _location_count(pages)

    # ── schema ──
    has_schema = "application/ld+json" in low or "schema.org" in low
    schema_types = " ".join(_SCHEMA_TYPE_RE.findall(html_all)).lower()
    schema_types += " " + " ".join(_ITEMTYPE_RE.findall(html_all)).lower()
    has_local_schema = any(t in schema_types for t in _LOCALBUSINESS_TYPES)
    facts["appointment_trade"] = any(t in schema_types for t in _APPOINTMENT_SCHEMA_TYPES)

    viewport = bool(_VIEWPORT_RE.search(html_all))
    copyright_year = _copyright_year(pages, today)

    total_kb = sum(len(p.html) for p in pages) // 1024
    avg_kb = int(total_kb / len(pages)) if pages else 0

    signals = {
        "has_ssl": base_url.lower().startswith("https://"),
        "mobile_viewport": viewport,
        "has_schema": has_schema,
        "has_localbusiness_schema": has_local_schema,
        "has_blog": has_blog,
        "blog_stale": blog_stale,
        "blog_year": blog_year,
        "has_online_booking": has_booking,
        "has_live_chat": bool(tech["chat"]),
        "has_contact_form": contact_form,
        "has_phone": has_phone,
        "has_email": has_email,
        "has_social": bool(_social_links(pages)) or any(e in low for e in _SOCIAL_EMBEDS),
        "has_pricing": has_pricing,
        "has_testimonials": testimonials,
        "has_gallery": gallery,
        "has_careers": careers,
        "has_newsletter": newsletter,
        "has_pdf_forms": bool(pdf_form),
        "has_multiple_locations": locations >= 2,
        "location_count": locations,
        "has_quote_form": has_quote_form,
        "has_whatsapp": whatsapp,
        "has_review_route": review_route,
        "copyright_year": copyright_year,
        "stale_copyright": bool(copyright_year and copyright_year < today.year - 1),
        "avg_page_kb": avg_kb,
        "slow": load_ms > 3000,
    }
    # Not "not has_ssl": `audit_from_html` is handed whatever key the caller
    # used, and a page dict keyed by a bare domain would have every lead told
    # its site is insecure. The claim is made only where the site itself said
    # so, by being served over plain http.
    facts["plain_http"] = str(base_url or "").lower().startswith("http://")
    facts["appointment_shaped"] = bool(_says(flat, _APPOINTMENT_WORDS))
    facts["takes_no_appointments"] = bool(_says(flat, _NO_APPOINTMENT_PHRASES))
    # The site's own list of what it sells, before `_services` filters it down to
    # twelve labels short enough for a brief. `_bookable` reads that filtered
    # list, so a trade whose work never made the cut — a driving school, a tattoo
    # studio — looked like a business that arranges no times at all.
    listing = [item for page in pages for item in _listed_services(page)]
    facts["service_listing"] = listing
    # Twelve things to buy and nothing under any of them but one general form.
    # `_listed_services` counted the items that are links on the same pass, and
    # a nav giving each service its own page counts as a route too: either way
    # an enquiry about one service can arrive as an enquiry about that service.
    #
    # Both halves are asked only when the answer can matter: a regex over every
    # href on the crawl is real work, and on a site listing four services
    # nothing reads the result.
    facts["listed_services"] = len(listing)
    facts["services_routed"] = len(listing) >= MANY_SERVICES and bool(
        sum(page._listed_linked for page in pages)
        or any(_SERVICE_PATH_RE.search(href) for href, _label in hrefs))
    # A checkout with nowhere on the site to leave an address. Deliberately not
    # "no abandoned-cart email": nothing here can see what a shop sends after
    # the fact. What it can see is that there is nothing to send it to.
    # Trivially true where there is no shop, which is where nothing reads it and
    # where the scan below would be paid for an answer nobody wants.
    facts["shop_email_route"] = not tech["ecommerce"] or bool(
        newsletter or _EMAIL_FIELD_RE.search(html_all))
    return signals, facts


# ── Services offered ──


def _listed_services(page) -> list[str]:
    """Items of the list a services heading introduces, in the site's own words.

    Cached on the page: `_signals` reads the raw list to decide whether the
    business books times, and `_services` reads it again to build the brief.

    `page._listed_linked` is counted on the same pass. A services list where
    every item is a link and one where none of them is are the same list of
    words and two different businesses to write to, and the difference is
    visible only here, where the `<li>` still has its markup.
    """
    if page._listed is not None:
        return page._listed
    out: list[str] = []
    linked = 0
    for heading in _HEAD_RE.finditer(page.html):
        if heading.group(1) == "1":
            continue
        if not any(w in _clean_text(heading.group(2)).lower() for w in _SERVICE_HEADINGS):
            continue
        # Only as far as the next heading: past it the list belongs to a
        # different section and the words stop being what the heading promised.
        tail = page.html[heading.end():heading.end() + 4000]
        nxt = _HEAD_RE.search(tail)
        if nxt:
            tail = tail[:nxt.start()]
        listing = _LIST_RE.search(tail)
        if listing:
            for item in _LI_RE.findall(listing.group(1)):
                out.append(_clean_text(item))
                if _A_RE.search(item):
                    linked += 1
    page._listed = out
    page._listed_linked = linked
    return out


def _services(pages, title: str, brand: str) -> list[str]:
    """Up to 12 things the business sells, in the words the site uses."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(label: str) -> None:
        label = _WS_RE.sub(" ", str(label or "")).strip(" -–—|·•,:")
        if not (2 < len(label) <= 40) or len(label.split()) > 6:
            return
        key = label.lower()
        if key in _NAV_STOPWORDS or key in seen or (brand and key == brand.lower()):
            return
        if key.startswith(_CTA_PREFIXES):
            return
        if not re.search(r"[A-Za-z]{3}", label) or key.startswith(("http", "©")):
            return
        seen.add(key)
        out.append(label)

    # A list under a services heading is the site telling you outright.
    for page in pages:
        for label in _listed_services(page):
            _add(label)
    for page in pages:
        for href, label in page.links:
            if _SERVICE_PATH_RE.search(href):
                _add(label)
    for page in pages:
        for href, label in page.links:
            low = label.lower()
            if any(w in low for w in _SERVICE_WORDS) and not href.lower().startswith(("mailto:", "tel:")):
                _add(label)
    for page in pages:
        for level, label in page.heads:
            if level == "1":
                continue
            if any(w in label.lower() for w in _SERVICE_WORDS):
                _add(label)
    if len(out) < 3 and title:
        for part in re.split(r"[|•·\-–—]", title):
            if any(w in part.lower() for w in _SERVICE_WORDS):
                _add(part)
    return out[:12]


def _meta(pages, base_url: str) -> tuple[str, str, str, str]:
    """(title, description, h1, brand) read from the home page."""
    if not pages:
        return "", "", "", ""
    home = pages[0]

    title_match = _TITLE_RE.search(home.html)
    title = _clean_text(title_match.group(1))[:160] if title_match else ""

    description = ""
    site_name = ""
    for tag in _META_RE.findall(home.html):
        attrs = _attrs(tag)
        key = (attrs.get("name") or attrs.get("property") or "").lower()
        content = _clean_text(attrs.get("content") or "")
        if key in ("description", "og:description") and content and not description:
            description = content[:300]
        elif key == "og:site_name" and content and not site_name:
            site_name = content[:80]

    h1 = ""
    for level, body in home.heads:
        if level == "1":
            h1 = body[:160]
            break

    brand = site_name
    if not brand:
        schema_name = re.search(r'(?is)"name"\s*:\s*"([^"]{2,80})"', home.html)
        if schema_name:
            brand = _clean_text(schema_name.group(1))
    if not brand and title:
        # "Acme Plumbing | Toronto Plumbers" -> the shorter, name-shaped half.
        parts = [p.strip() for p in re.split(r"[|•·]|\s[-–—]\s", title) if p.strip()]
        brand = parts[0] if parts else title
    if not brand:
        root = _host(base_url).split(".")[0]
        brand = root.replace("-", " ").title()
    return title, description, h1, brand[:80]


# ── Gaps ──


def _gap(code: str, evidence: str) -> dict:
    entry = GAP_CATALOGUE[code]
    return {
        "code": code,
        "title": entry["title"],
        "subject_phrase": entry["subject_phrase"],
        "severity": entry["severity"],
        "evidence": evidence[:120],
        "services": list(entry["services"]),
    }


def _gaps(tech: dict, signals: dict, facts: dict) -> list[dict]:
    """Fire the catalogue against the signals, with evidence a human could check.

    Evidence is read by the owner of the site, in a sentence that offers to help,
    so it says what *they* would find if they looked: a page to open, a phone to
    try it on, a line in the footer. It never reports what the crawler did.
    Nobody warms to a stranger who says he fetched six of their pages.

    Which is why almost none of these sentences is assembled. A value spliced in
    from crawler state arrives in the reader's own words only by luck — a path is
    "/" for a home-page form, a tally of `<form` tags counts the search box, a
    vendor key is spelled "woocommerce". Three values survive because the owner
    can walk to them: their own link text, the month on their newest post, and
    the year in their footer. Everything else is written out as a sentence.
    """
    fired: list[dict] = []

    # This gap is usually the headline, and the headline is the first sentence a
    # stranger reads about their own website, so it asks for a business that
    # books times and not merely for one that sells something. A page that says
    # it takes no appointments settles the question by itself.
    books_times = (facts.get("appointment_shaped") or facts.get("bookable_services")
                   or facts.get("appointment_trade")) and not facts.get("takes_no_appointments")

    if (not signals["has_online_booking"] and signals["has_contact_form"]
            and books_times):
        # The site has a Book button and the page behind it is a form. Say that,
        # rather than the sentence for a site with no button at all — the reader
        # of the first one is about to look at their own Book page and agree.
        fired.append(_gap("no_online_booking",
                          "clicking through to book opens a form and waits for somebody "
                          "to answer" if facts.get("booking_is_a_form") else
                          "asking for a time means filling in the form and waiting "
                          "for someone to answer"))
    if not tech["crm"] and signals["has_contact_form"]:
        fired.append(_gap("no_crm_signals",
                          "nothing is hooked up to file a name and a number after the "
                          "form is sent"))
    # `has_phone`, not a call-to-action: the sentence claims there is no number
    # on the site, and a footer that prints one without saying "call now" makes
    # it false. Gating on the marketing phrase asked whether the business is
    # *promoting* the phone, which is a different question and the wrong one.
    if (signals["has_contact_form"] and not signals["has_live_chat"]
            and not signals["has_online_booking"] and not signals["has_phone"]):
        fired.append(_gap("contact_form_only",
                          "the form is the only way in: no chat, no booking, no number to tap"))
    # A checkout asks for a name, an email and a card, so a storefront is never
    # a site where "nothing asks a visitor for a name". The gap fired on a wine
    # merchant whose whole home page is an Ecwid store.
    if (not signals["has_contact_form"] and not signals["has_newsletter"]
            and not tech["ecommerce"]):
        fired.append(_gap("no_lead_capture",
                          "nothing on the site asks a visitor for a name or an email"))
    # The address is a lead route, so this is not the same finding as the one
    # above and both are true of a brochure site at once: one says nobody is
    # collecting anything, the other says what happens to what does arrive.
    if (not signals["has_contact_form"] and not signals["has_online_booking"]
            and signals["has_email"]):
        fired.append(_gap("email_only_intake",
                          "every enquiry arrives as an email that somebody opens, reads "
                          "and answers by hand"))
    if not tech["chat"]:
        fired.append(_gap("no_live_chat",
                          "no chat box on the site, so a question waits for the phone or a form"))
    if signals["has_whatsapp"]:
        fired.append(_gap("whatsapp_manual",
                          "every message that comes in through WhatsApp waits for "
                          "somebody to pick up the phone"))
    if signals["has_quote_form"]:
        fired.append(_gap("quote_by_form",
                          f'"{facts.get("quote_phrase", "request a quote")}" on the site '
                          "ends in a form somebody has to read"))
    if signals["has_contact_form"] and facts.get("form_fields", 0) >= LONG_FORM_FIELDS:
        fired.append(_gap("long_intake_form",
                          f"the form asks for {_count_word(facts['form_fields'])} separate "
                          "things before anybody can press send"))
    if (signals["has_contact_form"] and not facts.get("services_routed")
            and facts.get("listed_services", 0) >= MANY_SERVICES):
        fired.append(_gap("services_no_route",
                          f"{_count_word(facts['listed_services'])} services are listed and "
                          "every enquiry about them lands in the same place"))
    if not tech["analytics"]:
        fired.append(_gap("no_analytics",
                          "no analytics on any page, so last month's visits went uncounted"))
    if signals["has_careers"]:
        fired.append(_gap("careers_manual",
                          "the job advert points at an inbox, so every CV arrives as an "
                          "email to be read" if facts.get("careers_mailbox") else
                          "the site is advertising jobs, so CVs are arriving to be read and sorted"))
    if tech["ecommerce"]:
        fired.append(_gap("ecommerce_manual",
                          "there is a shop on the site, so every order needs picking, "
                          "invoicing and chasing"))
    if tech["ecommerce"] and not facts.get("shop_email_route"):
        fired.append(_gap("cart_no_recovery",
                          "there is a checkout on the site but nowhere at all for a shopper "
                          "to leave an email"))
    if signals["has_pdf_forms"]:
        fired.append(_gap("pdf_forms",
                          f"{facts.get('pdf_form') or 'the paperwork'} is a PDF to print, "
                          "fill in and send back"))
    if facts.get("price_doc_year"):
        fired.append(_gap("dated_document",
                          "the price list customers download was put together in "
                          f"{facts['price_doc_year']}"))
    if signals["has_multiple_locations"]:
        fired.append(_gap("multi_location",
                          "every address on the site takes its own calls and answers "
                          "its own messages"))
    if not signals["mobile_viewport"]:
        fired.append(_gap("no_mobile",
                          "open the site on a phone and it loads the full desktop layout"))
    if facts.get("plain_http"):
        fired.append(_gap("no_ssl",
                          "browsers put a Not secure warning next to the address before "
                          "the page opens"))
    if signals["has_blog"] and signals["blog_stale"]:
        fired.append(_gap("stale_blog",
                          f"the newest post on the blog is dated {facts.get('latest_date', '')}"))
    if not signals["has_social"]:
        fired.append(_gap("no_social_presence",
                          "nothing on the site links out to Facebook, Instagram or LinkedIn"))
    if signals["has_testimonials"] and not signals["has_review_route"]:
        fired.append(_gap("no_review_capture",
                          "nothing on the site points a happy customer at a page where "
                          "they can leave a review"))
    if signals["stale_copyright"]:
        fired.append(_gap("stale_site", f"the footer still reads {signals['copyright_year']}"))
    if signals["slow"]:
        fired.append(_gap("slow_site",
                          "the home page takes a few seconds before anything shows up "
                          "on screen"))
    if not signals["has_schema"]:
        fired.append(_gap("no_schema",
                          "Google gets no hours, no prices and no reviews from the pages"))
    # A linked price list silences this one for the same reason a storefront
    # does: the business publishes what it charges, and being told it publishes
    # nothing is a sentence its owner can disprove from their own home page.
    # What is true about that business is `dated_document`, above.
    if (not signals["has_pricing"] and not tech["ecommerce"]
            and not facts.get("price_document")):
        fired.append(_gap("price_opaque",
                          "not a rate, a range or a starting figure on any page, so every "
                          "enquiry has to ask"))

    order = list(GAP_CATALOGUE)
    # A gap with nothing in the catalogue behind it sorts last whatever its
    # severity. The email names `gaps[0]` and then makes an offer, so a headline
    # with no offer behind it either borrows an unrelated one or leaves the
    # reader with a sentence that answers nothing. It still fires, still scores
    # and still reaches the model's brief, where it is an observation and not a
    # promise.
    fired.sort(key=lambda g: (not g["services"], -g["severity"], order.index(g["code"])))
    return fired


# Straight addition put every site with a contact form over the cap, so the
# column the operator sorts leads by read 100 the whole way down and ordered
# nothing. A per-gap taper fixed the pinning and cost the ordering instead:
# under it the seventh gap is worth two points and the tenth is worth one, so
# the findings that mean recurring manual work — several locations, a careers
# pipeline, paperwork handed out as PDFs, a shop — are all severity 2, all land
# late, and together move the number by less than a rounding error. Measured
# over a thirty-site corpus the tapered score ranked those leads *worse* than
# counting the gaps and ignoring severity altogether.
#
# So the taper is gone and the cap is enforced by the shape of the curve rather
# than by clipping: the score rises with the total severity fired and flattens
# as it climbs, which is monotone in "how much is there to fix here" and can
# never reach 100 however long the list gets. `_GAP_SCALE` sets only how quickly
# the curve flattens — the ordering is identical for every value of it — so it
# is chosen to spread real sites across a readable range and nothing else.
_GAP_CEILING = 83.0
_GAP_SCALE = 12.0


def _score(gaps, reachable: bool, has_email: bool) -> int:
    severity = sum(int(gap["severity"]) for gap in gaps)
    total = _GAP_CEILING * (1.0 - math.exp(-severity / _GAP_SCALE))
    if reachable:
        total += 10
    if has_email:
        total += 5
    return max(0, min(100, round(total)))


# ── Result shape ──


def _blank(url: str, error: str = "", *, status: int = 0, reason: str = "") -> dict:
    reason = reason or unreachable_reason(error, status)
    return {
        "url": url, "final_url": "", "reachable": False, "status": 0,
        "load_ms": 0, "pages": [], "page_count": 0,
        "title": "", "description": "", "h1": "", "brand": "",
        "tech": {"cms": "", "builder": "", "ecommerce": "", "analytics": [], "chat": "",
                 "booking": "", "crm": "", "forms": 0, "frameworks": []},
        "services": [],
        "signals": {
            "has_ssl": False, "mobile_viewport": False, "has_schema": False,
            "has_localbusiness_schema": False, "has_blog": False, "blog_stale": False,
            "blog_year": 0, "has_online_booking": False, "has_live_chat": False,
            "has_contact_form": False, "has_phone": False, "has_email": False,
            "has_social": False, "has_pricing": False, "has_testimonials": False,
            "has_gallery": False, "has_careers": False, "has_newsletter": False,
            "has_pdf_forms": False, "has_multiple_locations": False, "location_count": 0,
            "has_quote_form": False, "has_whatsapp": False, "has_review_route": False,
            "copyright_year": 0, "stale_copyright": False,
            "avg_page_kb": 0, "slow": False,
        },
        "gaps": [], "opportunity_score": 0, "error": error,
        # The two keys the UI reads to answer "which site is not reachable, and
        # why". Always present and always a pair: a code to branch on and the
        # sentence to print. Both are "" exactly when `reachable` is True.
        "unreachable_reason": reason,
        "unreachable_detail": unreachable_detail(reason),
    }


def _order_pages(pages: dict, base_url: str) -> list[_Page]:
    """Real pages, home first — every later step treats pages[0] as the home page."""
    items = []
    for url, html in (pages or {}).items():
        if isinstance(url, str) and isinstance(html, str) and html.strip():
            items.append((url, html))
    if not items:
        return []

    base_host = _host(base_url)

    def _rank(item) -> tuple:
        url = item[0]
        path = _path(url).rstrip("/")
        same_host = _host(url) == base_host if base_host else True
        return (0 if (same_host and path in ("", "/")) else 1, len(path), url)

    items.sort(key=_rank)
    return [_Page(url, html) for url, html in items]


def _audit(pages: dict, base_url: str, *, final_url: str = "", status: int = 0,
           load_ms: int = 0, error: str = "") -> dict:
    """The whole audit. Both public entry points land here."""
    ordered = _order_pages(pages, base_url)
    result = _blank(base_url, error, status=status)
    if not ordered:
        result["final_url"] = final_url or base_url
        result["status"] = status
        return result

    home_url = final_url or ordered[0].url or base_url
    # Built once, here, and handed down. `_tech`, `_unreadable` and `_signals`
    # each used to join the whole crawl for itself.
    low = "\n".join(p.low for p in ordered)
    text = " ".join(p.text for p in ordered)

    tech = _tech(ordered, low)
    title, description, h1, brand = _meta(ordered, home_url)

    # Before a single absence is claimed: was there anything here to look at?
    #
    # `tech` survives and the signals do not, and the difference between them is
    # the whole point. A marker on the page is a positive fact — this shell does
    # load Intercom — and stays true however little the page rendered. Every
    # signal below is the other kind: "no chat", "no form", "nothing asks for a
    # name", each of them an absence, and an absence is only a fact when there
    # was somewhere to look.
    state = _unreadable(ordered, low, text)
    if state:
        result.update({
            "url": base_url, "final_url": home_url, "status": status or 200,
            "load_ms": load_ms, "pages": [p.url for p in ordered],
            "page_count": len(ordered), "title": title, "brand": brand,
            "tech": tech,
            "unreachable_reason": state, "unreachable_detail": unreachable_detail(state),
        })
        return result
    services = _services(ordered, title, brand)
    signals, facts = _signals(ordered, tech, home_url, load_ms, low, text)
    facts["bookable_services"] = (_bookable(services)
                                  or _bookable(facts.pop("service_listing", ())))
    gaps = _gaps(tech, signals, facts)

    result.update({
        "url": base_url,
        "final_url": home_url,
        "reachable": True,
        "status": status or 200,
        "load_ms": load_ms,
        "pages": [p.url for p in ordered],
        "page_count": len(ordered),
        "title": title, "description": description, "h1": h1, "brand": brand,
        "tech": tech,
        "services": services,
        "signals": signals,
        "gaps": gaps,
        "opportunity_score": _score(gaps, True, signals["has_email"]),
    })
    return result


# ── Public API ──


def audit_from_html(pages: dict, base_url: str) -> dict:
    """Audit `{url: html}` with no network at all. Every detection rule lives here.

    `load_ms` is 0 and `slow` is therefore False: speed is a property of the
    fetch, not of the markup, so the pure path never claims it.
    """
    try:
        return _audit(pages, base_url or "")
    except Exception as exc:
        return _blank(base_url or "", f"{type(exc).__name__}: {exc}"[:200])


def unreachable_audit(url: str, error: str = "", *, final_url: str = "",
                      status: int = 0) -> dict:
    """The audit for a site nobody could read, with the reason kept.

    The caller that has already paid the connection timeout — `core.campaign`
    does, twice, for an https host it retried over plain http — builds its
    result through here instead of through `audit_from_html({}, url)`, which
    threw the reason away and left the operator with a boolean.
    """
    result = _blank(str(url or ""), str(error or ""), status=int(status or 0))
    result["final_url"] = str(final_url or url or "")
    result["status"] = int(status or 0)
    return result


def audit_site(url: str, *, max_pages: int = 6, timeout: float = 8.0,
               prefetched: dict | None = None) -> dict:
    """Audit a live site, reusing `prefetched` HTML from `harvest_site` if given.

    Never raises and never blocks forever: one timed home fetch, then the best
    internal pages in parallel, each capped in size.
    """
    try:
        url = str(url or "").strip()
        if not url:
            return _blank("", "no url")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        usable = {k: v for k, v in (prefetched or {}).items()
                  if isinstance(k, str) and isinstance(v, str) and v.strip()}
        if usable:
            trimmed = dict(list(usable.items())[:max(1, max_pages)])
            return _audit(trimmed, url, final_url="", status=200, load_ms=0)

        started = time.perf_counter()
        home_html, final_url, status, error = _fetch(url, timeout)
        load_ms = int((time.perf_counter() - started) * 1000)
        if not home_html:
            dead = _blank(url, error or "empty response", status=status)
            dead["final_url"] = final_url or url
            dead["status"] = status
            return dead

        pages = {final_url: home_html}
        targets = _crawl_targets(home_html, final_url, max(0, max_pages - 1))
        if targets:
            workers = min(len(targets), 6)
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                for target, (html, resolved, _status, _err) in zip(
                        targets, pool.map(lambda t: _fetch(t, timeout), targets)):
                    if html:
                        pages.setdefault(resolved or target, html)

        return _audit(pages, url, final_url=final_url, status=status, load_ms=load_ms)
    except Exception as exc:
        return _blank(str(url or ""), f"{type(exc).__name__}: {exc}"[:200])


# ── Fetching ──

# Which pages tell you most about how a business runs, best first.
_CRAWL_PRIORITY: tuple[tuple[str, ...], ...] = (
    ("/services", "/our-services", "/what-we-do", "/treatments", "/solutions"),
    ("/contact", "/contact-us", "/get-in-touch"),
    ("/about", "/about-us", "/our-story"),
    ("/book", "/booking", "/appointments", "/schedule", "/request-appointment"),
    ("/pricing", "/prices", "/rates", "/plans"),
    ("/careers", "/jobs", "/join-us", "/employment"),
    ("/locations", "/our-locations", "/branches", "/find-us"),
    ("/blog", "/news", "/articles", "/insights"),
    ("/shop", "/store", "/products", "/collections"),
)


def _crawl_targets(html: str, base_url: str, limit: int) -> list[str]:
    if limit <= 0:
        return []
    base_host = _host(base_url)
    seen: set[str] = {base_url.rstrip("/")}
    buckets: list[list[str]] = [[] for _ in _CRAWL_PRIORITY]

    for href, label in _A_RE.findall(html):
        href = href.strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue
        full = urllib.parse.urljoin(base_url, href).split("#")[0]
        if not full.startswith(("http://", "https://")) or _ASSET_RE.search(full):
            continue
        if base_host and _host(full) != base_host:
            continue
        key = full.rstrip("/")
        if key in seen:
            continue
        path_and_label = (_path(full) + " " + _clean_text(label)).lower()
        for index, words in enumerate(_CRAWL_PRIORITY):
            if any(w in path_and_label or w.lstrip("/") in path_and_label for w in words):
                seen.add(key)
                buckets[index].append(full)
                break

    targets: list[str] = []
    for bucket in buckets:
        for target in bucket:
            targets.append(target)
            if len(targets) >= limit:
                return targets
    return targets


_LEGACY_CHARSET_RE = re.compile(r"(?i)^(?:iso[\-_]?8859|latin[\-_]?\d|windows[\-_]?12|cp12)")


def _charset(headers, raw: bytes) -> str:
    """The charset the response declares, header first, then the markup. "" if none."""
    content_type = (headers.get("Content-Type") or "") if headers else ""
    match = re.search(r"charset\s*=\s*([A-Za-z0-9_\-]+)", content_type, re.I)
    if match:
        return match.group(1)
    match = re.search(rb'charset\s*=\s*["\']?([A-Za-z0-9_\-]+)', raw[:2048], re.I)
    if match:
        return match.group(1).decode("ascii", "replace")
    return ""


def _decode(raw: bytes, headers) -> str:
    """Page bytes to text, strictly where possible. Mirrors `core.enrich._decode_body`.

    A candidate only wins if it decodes *strictly*, so an undeclared latin-1 page
    falls through to cp1252 instead of arriving as "Caf� Andr�".
    `errors="replace"` is the last resort and nothing else: the brand and title
    read here are most of what `digest()` sends the model, and U+FFFD in them
    becomes U+FFFD in a live email.

    A legacy single-byte label never fails, because every byte is a character in
    it, and stale `charset=iso-8859-1` defaults sit on plenty of utf-8 pages. When
    the bytes are genuinely multi-byte utf-8 they are utf-8, whatever the header says.
    """
    declared = _charset(headers, raw).strip().strip("'\"")
    candidates = [declared, "utf-8", "cp1252"]
    if declared and _LEGACY_CHARSET_RE.match(declared) and not raw.isascii():
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
    for name in candidates:
        if not name:
            continue
        try:
            return raw.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _fetch(url: str, timeout: float) -> tuple[str, str, int, str]:
    """(html, final_url, status, error). Capped read, decoded honestly, never raises."""
    request = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        # No brotli: stdlib cannot decode it and a garbled body reads as an empty site.
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(_READ_CAP)
            encoding = (response.headers.get("Content-Encoding") or "").lower()
            final_url = response.geturl() or url
            status = getattr(response, "status", 0) or response.getcode() or 0
            headers = response.headers
    except urllib.error.HTTPError as exc:
        return "", url, getattr(exc, "code", 0) or 0, f"HTTP {getattr(exc, 'code', '')}"
    except Exception as exc:
        return "", url, 0, f"{type(exc).__name__}: {exc}"[:200]

    if encoding == "gzip":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    elif encoding == "deflate":
        try:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:
            try:
                raw = zlib.decompress(raw)
            except Exception:
                pass

    return _decode(raw, headers), final_url, status, ""


# ── Digest ──


def _digest_lines(audit: dict, services: int, signals: int, gaps: int) -> list[str]:
    tech = audit.get("tech") or {}
    sig = audit.get("signals") or {}
    domain = _host(str(audit.get("final_url") or audit.get("url") or ""))
    brand = str(audit.get("brand") or audit.get("title") or "").strip()

    lines = ["SITE: " + " | ".join(p for p in (domain, brand[:60]) if p)]

    # A site nobody could read has no stack and no signals, and the lines below
    # would invent both: `tech` is empty on a dead host, so "no chat | no
    # booking | no crm | no analytics" went to the model as four facts about a
    # business whose home page never answered. The model then wrote an opener
    # around them. One line, and it says what is actually known.
    if not audit.get("reachable"):
        detail = (str(audit.get("unreachable_detail") or "").strip()
                  or unreachable_detail(str(audit.get("unreachable_reason") or ""))
                  or "the site could not be read")
        lines.append("UNREADABLE: " + detail)
        return lines

    offered = [str(s).strip() for s in (audit.get("services") or []) if str(s).strip()]
    if offered and services > 0:
        lines.append("WHAT: " + ", ".join(offered[:services]))

    stack = [str(tech.get("cms") or "custom site")]
    if tech.get("ecommerce"):
        stack.append(f"{tech['ecommerce']} store")
    stack.append(str(tech.get("chat") or "no chat"))
    stack.append(str(tech.get("booking") or "no booking"))
    stack.append(str(tech.get("crm") or "no crm"))
    analytics = [str(a) for a in (tech.get("analytics") or [])][:3]
    stack.append("+".join(analytics) if analytics else "no analytics")
    lines.append("STACK: " + " | ".join(stack))

    facts = [
        "contact form yes" if sig.get("has_contact_form") else "no contact form",
        "phone yes" if sig.get("has_phone") else "no phone",
        (f"blog {sig.get('blog_year')}" if sig.get("blog_year")
         else ("blog undated" if sig.get("has_blog") else "no blog")),
    ]
    if sig.get("has_careers"):
        facts.append("careers page yes")
    if sig.get("has_multiple_locations"):
        facts.append(f"{sig.get('location_count', 2)} locations")
    if not sig.get("has_pricing"):
        facts.append("no pricing")
    if not sig.get("has_social"):
        facts.append("no social links")
    if not sig.get("mobile_viewport"):
        facts.append("no mobile layout")
    if sig.get("stale_copyright") and sig.get("copyright_year"):
        facts.append(f"copyright {sig['copyright_year']}")
    if sig.get("slow"):
        facts.append("slow to load")
    if facts and signals > 0:
        lines.append("SIGNALS: " + ", ".join(facts[:signals]))

    top = [g for g in (audit.get("gaps") or []) if isinstance(g, dict)][:max(1, gaps)]
    if top:
        lines.append("TOP GAPS: " + "; ".join(
            f"{str(g.get('title') or g.get('code') or '').strip()} ({int(g.get('severity') or 1)})"
            for g in top))
    return lines


def digest(audit: dict, max_chars: int = 1200) -> str:
    """The five-line brief that is the *only* thing the model ever receives.

    Every character here is billed on every lead, so the shape is fixed and the
    content is trimmed rather than allowed to overflow: services first, then
    secondary signals, then gaps down to the headline one.
    """
    try:
        if not isinstance(audit, dict):
            return ""
        budget = max(80, int(max_chars))
        text = ""
        for services, signals, gaps in ((6, 9, 3), (4, 7, 3), (3, 5, 3),
                                        (2, 4, 2), (1, 3, 2), (0, 2, 1)):
            text = "\n".join(_digest_lines(audit, services, signals, gaps))
            if len(text) <= budget:
                return text
        # A site with absurdly long service names still has to fit the budget.
        return text[:budget].rstrip()
    except Exception:
        return ""
