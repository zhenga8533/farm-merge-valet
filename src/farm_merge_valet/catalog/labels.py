"""Player-facing labels for runtime catalog identities."""

from __future__ import annotations

_FAMILY_NAMES = {
    "alpaca": "Alpaca",
    "apple": "Apple",
    "avocado": "Avocado Plant",
    "bee": "Bee",
    "carrot": "Carrot",
    "chicken": "Chicken",
    "coffee": "Coffee Plant",
    "coin": "Coin",
    "corn": "Corn",
    "cow": "Cow",
    "deer": "Deer",
    "energy": "Energy",
    "flower": "Flowers",
    "gazebo": "Grand Gazebo",
    "gazebo_decoration": "Park Decorations",
    "gazebo_token": "Park Decoration Tokens",
    "gem": "Crystal",
    "goat": "Goat",
    "greenhouse": "Greenhouse",
    "horse": "Horse",
    "islandbird": "Island Bird",
    "islandflower": "Island Flower",
    "islandfruit": "Island Fruit",
    "jungleadventuregear": "Jungle Adventure Gear",
    "junglebutterfly": "Jungle Butterfly",
    "junglenavigation": "Jungle Navigation",
    "pig": "Pig",
    "sapling": "Sapling",
    "sheep": "Sheep",
    "soybeans": "Soybeans",
    "stone": "Stone",
    "supplies": "Supplies",
    "sugarcane": "Sugarcane",
    "sunflower": "Sunflower",
    "tomato": "Tomato Plant",
    "tool": "Tools",
    "trufflepig": "Truffle Pig",
    "upgrade_card": "Upgrade Card",
    "wheat": "Wheat",
    "wood": "Wood",
}

_BLUEPRINT_NAMES = {
    "market": "Farmer's Market",
    "bakery": "Bakery",
    "dairy": "Dairy",
    "bbq": "BBQ",
    "sweets": "Sweets Station",
    "loom": "Loom",
    "barista": "Barista",
    "tomato_on_wheels": "Tomato on Wheels",
    "building_avocadofiesta": "Avocado Fiesta",
    "building_trufflelicious": "Trufflelicious",
    "building_apple_delights": "Apple Delights",
    "building_bee_food_truck": "The Honey Pot",
    "building_pink_pony_club": "Pink Pony Club",
    "building_casa_de_alpaca": "Casa de Alpaca",
    "decorative_toilet": "Outhouse",
    "decorative_windmill": "Windmill",
    "decorative_chickencoop": "Chicken Coop",
    "decorative_doghouse": "Dog House",
    "decorative_farmhouse": "Farm House",
    "decorative_feedingtrough": "Feeding Trough",
    "decorative_birdshouse": "Bird House",
    "decorative_barn": "Barn",
    "decorative_flowerpots": "Flower Pot",
    "decorative_fountain": "Fountain",
    "decorative_haywagon": "Hay Wagon",
    "decorative_lamppost": "Lamp Post",
    "decorative_milktank": "Milk Tank",
    "decorative_picknicktable": "Picnic Table",
    "decorative_shed": "Shed",
    "decorative_silo": "Silo",
    "decorative_stoneflowerpot": "Stone Flower Pot",
    "decorative_watertower": "Water Tower",
    "decorative_well": "Well",
    "alpacawool": "Alpaca Wool",
    "apple": "Apple",
    "avocado": "Avocado",
    "bacon": "Bacon",
    "carrot": "Carrot",
    "coffeebeans": "Coffee Beans",
    "corn": "Corn",
    "egg": "Egg",
    "fur": "Fur",
    "goatmilk": "Goat Milk",
    "honey": "Honey",
    "horseshoe": "Horseshoe",
    "milk": "Milk",
    "soybeans": "Soybeans",
    "sugarcane": "Sugarcane",
    "sunflower": "Sunflower",
    "tomato": "Tomato",
    "trainstation": "Train Station",
    "traintrack_stop": "Train Track Stop",
    "truffle": "Truffle",
    "wheat": "Wheat",
    "wool": "Wool",
}

_COMPOUND_WORDS = {
    "islandbush": "Island Bush",
    "islandnest": "Island Nest",
    "islandpalmtree": "Island Palm Tree",
    "junglesuitcase": "Jungle Suitcase",
    "jungletent": "Jungle Tent",
    "jungletree": "Jungle Tree",
}

_GROUP_NAMES = {
    "rock": "Rocks",
    "toolbox": "Toolboxes",
    "tree": "Trees",
}


def catalog_display_name(blueprint_id: str, family_id: str) -> str:
    if blueprint_id.startswith("recipe_"):
        return _humanize(blueprint_id.removeprefix("recipe_"))
    if blueprint_id in _BLUEPRINT_NAMES:
        return _BLUEPRINT_NAMES[blueprint_id]
    if family_id in _FAMILY_NAMES:
        return _FAMILY_NAMES[family_id]
    return _humanize(family_id)


def catalog_group_name(group_id: str, fallback: str) -> str:
    return _GROUP_NAMES.get(
        group_id,
        _FAMILY_NAMES.get(
            group_id,
            _BLUEPRINT_NAMES.get(group_id, _humanize(group_id) or fallback),
        ),
    )


def _humanize(value: str) -> str:
    parts = []
    for token in value.split("_"):
        parts.append(_COMPOUND_WORDS.get(token, token.title()))
    return " ".join(parts)
