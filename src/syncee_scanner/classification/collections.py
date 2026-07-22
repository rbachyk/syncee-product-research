"""Collection keyword & category signals (spec §25.1–§25.3).

Data-only module: the keyword sets and category-name hints that the deterministic classifier
uses to assign each product to exactly one RB Home collection. Tuned over time; kept
separate from the classification logic so rules stay readable.
"""

from __future__ import annotations

from ..models import Collection

# Keyword signals per collection (matched against title + subcategory + description).
KEYWORDS: dict[Collection, tuple[str, ...]] = {
    # Gadgets, cookware, prep, storage — NOT tableware (that's Dining).
    Collection.KITCHEN_CONVENIENCE: (
        "kitchen", "cook", "cooking", "food", "meal", "chop", "peel", "slice", "grater",
        "utensil", "spatula", "whisk", "sink", "storage jar", "spice", "countertop",
        "baking", "pot", "pan", "colander", "strainer", "cutting board", "knife",
        "container", "jar", "gadget", "apron", "defrost",
    ),
    # Tableware & serving (multilingual).
    Collection.DINING: (
        "plate", "bowl", "dish", "platter", "cutlery", "flatware", "glass", "mug", "cup",
        "saucer", "teapot", "tableware", "dinnerware", "serveware", "tablecloth", "napkin",
        "placemat", "carafe", "decanter", "tumbler", "coaster", "serving", "trivet",
        "teller", "schale", "besteck", "piatto", "ciotola", "posate", "assiette", "plato",
        "cubiertos", "bicchiere", "vassoio", "geschirr",
    ),
    Collection.HOME_COMFORT: (
        "bedroom", "bed", "pillow", "blanket", "throw", "sofa", "couch", "living room",
        "cushion", "lamp", "light", "lighting", "cozy", "comfort", "relax", "curtain",
        "rug", "candle", "diffuser", "slippers", "warm",
    ),
    # Bathroom fixtures, accessories, bath textiles (multilingual).
    Collection.BATHROOM: (
        "bathroom", "bath mat", "bath rug", "bath towel", "hand towel", "shower", "toilet",
        "soap dispenser", "soap dish", "toothbrush", "towel rail", "towel rack", "bathrobe",
        "shower curtain", "basin", "vanity", "loofah", "hammam", "pestemal", "toiletry",
        "razor", "washcloth", "badezimmer", "handtuch", "bagno", "asciugamano", "baño",
        "salle de bain",
    ),
}

# Subcategory / category name fragments that map deterministically (higher confidence).
CATEGORY_HINTS: dict[str, Collection] = {
    "kitchen": Collection.KITCHEN_CONVENIENCE,
    "cookware": Collection.KITCHEN_CONVENIENCE,
    "dining": Collection.DINING,
    "tableware": Collection.DINING,
    "dinnerware": Collection.DINING,
    "bedroom": Collection.HOME_COMFORT,
    "bedding": Collection.HOME_COMFORT,
    "living room": Collection.HOME_COMFORT,
    "lighting": Collection.HOME_COMFORT,
    "home decor": Collection.HOME_COMFORT,
    "bath": Collection.BATHROOM,
    "bathroom": Collection.BATHROOM,
}
