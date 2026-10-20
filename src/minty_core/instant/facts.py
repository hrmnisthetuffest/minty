"""Things that do not change: capitals, the periodic table, formulas, full forms, constants and counts. No model, no network."""
from __future__ import annotations

import re

from minty_core.instant.base import Query, Rule

CATEGORY = "facts"
CAPITALS = {
    "albania": "Tirana", "austria": "Vienna", "belarus": "Minsk", "belgium": "Brussels", "bosnia and herzegovina": "Sarajevo",
    "bulgaria": "Sofia", "croatia": "Zagreb", "cyprus": "Nicosia", "czech republic": "Prague", "czechia": "Prague",
    "denmark": "Copenhagen", "estonia": "Tallinn", "finland": "Helsinki", "france": "Paris", "germany": "Berlin",
    "greece": "Athens", "hungary": "Budapest", "iceland": "Reykjavik", "ireland": "Dublin", "italy": "Rome", "latvia": "Riga",
    "lithuania": "Vilnius", "luxembourg": "Luxembourg", "malta": "Valletta", "moldova": "Chisinau", "montenegro": "Podgorica",
    "netherlands": "Amsterdam", "holland": "Amsterdam", "north macedonia": "Skopje", "norway": "Oslo", "poland": "Warsaw",
    "portugal": "Lisbon", "romania": "Bucharest", "russia": "Moscow", "serbia": "Belgrade", "slovakia": "Bratislava",
    "slovenia": "Ljubljana", "spain": "Madrid", "sweden": "Stockholm", "switzerland": "Bern", "ukraine": "Kyiv",
    "united kingdom": "London", "uk": "London", "britain": "London", "great britain": "London", "england": "London",
    "scotland": "Edinburgh", "wales": "Cardiff", "northern ireland": "Belfast",
    "afghanistan": "Kabul", "armenia": "Yerevan", "azerbaijan": "Baku", "bahrain": "Manama", "bangladesh": "Dhaka",
    "bhutan": "Thimphu", "brunei": "Bandar Seri Begawan", "cambodia": "Phnom Penh", "china": "Beijing", "georgia": "Tbilisi",
    "india": "New Delhi", "iran": "Tehran", "iraq": "Baghdad", "israel": "Jerusalem", "japan": "Tokyo", "jordan": "Amman",
    "kazakhstan": "Astana", "kuwait": "Kuwait City", "kyrgyzstan": "Bishkek", "laos": "Vientiane", "lebanon": "Beirut",
    "malaysia": "Kuala Lumpur", "maldives": "Male", "mongolia": "Ulaanbaatar", "myanmar": "Naypyidaw", "nepal": "Kathmandu",
    "north korea": "Pyongyang", "oman": "Muscat", "pakistan": "Islamabad", "philippines": "Manila", "qatar": "Doha",
    "saudi arabia": "Riyadh", "singapore": "Singapore", "south korea": "Seoul", "syria": "Damascus", "taiwan": "Taipei",
    "tajikistan": "Dushanbe", "thailand": "Bangkok", "turkey": "Ankara", "turkmenistan": "Ashgabat",
    "united arab emirates": "Abu Dhabi", "uae": "Abu Dhabi", "uzbekistan": "Tashkent", "vietnam": "Hanoi", "yemen": "Sanaa",
    "algeria": "Algiers", "angola": "Luanda", "botswana": "Gaborone", "cameroon": "Yaounde", "egypt": "Cairo",
    "ethiopia": "Addis Ababa", "ghana": "Accra", "ivory coast": "Yamoussoukro", "kenya": "Nairobi", "libya": "Tripoli",
    "madagascar": "Antananarivo", "morocco": "Rabat", "mozambique": "Maputo", "namibia": "Windhoek", "nigeria": "Abuja",
    "rwanda": "Kigali", "senegal": "Dakar", "somalia": "Mogadishu", "sudan": "Khartoum", "tanzania": "Dodoma", "tunisia": "Tunis",
    "uganda": "Kampala", "zambia": "Lusaka", "zimbabwe": "Harare", "democratic republic of the congo": "Kinshasa",
    "south africa": "Pretoria, which is the executive capital. Cape Town and Bloemfontein are the other two",
    "argentina": "Buenos Aires", "bolivia": "Sucre, though La Paz is the seat of government", "brazil": "Brasilia",
    "canada": "Ottawa", "chile": "Santiago", "colombia": "Bogota", "costa rica": "San Jose", "cuba": "Havana",
    "dominican republic": "Santo Domingo", "ecuador": "Quito", "el salvador": "San Salvador", "guatemala": "Guatemala City",
    "haiti": "Port-au-Prince", "honduras": "Tegucigalpa", "jamaica": "Kingston", "mexico": "Mexico City",
    "nicaragua": "Managua", "panama": "Panama City", "paraguay": "Asuncion", "peru": "Lima", "united states": "Washington, D.C.",
    "usa": "Washington, D.C.", "us": "Washington, D.C.", "america": "Washington, D.C.", "united states of america": "Washington, D.C.",
    "uruguay": "Montevideo", "venezuela": "Caracas", "australia": "Canberra", "new zealand": "Wellington", "fiji": "Suva",
    "papua new guinea": "Port Moresby", "sri lanka": "Sri Jayawardenepura Kotte, though Colombo is the biggest city",
}
_THE = {"united states", "usa", "us", "united kingdom", "uk", "netherlands", "philippines", "uae", "united arab emirates",
        "czech republic", "united states of america", "democratic republic of the congo", "maldives"}
_DISPLAY = {"usa": "the United States", "us": "the United States", "united states": "the United States",
            "united states of america": "the United States", "america": "America", "uk": "the UK",
            "united kingdom": "the United Kingdom", "uae": "the UAE", "united arab emirates": "the UAE"}
_REVERSE: dict[str, str] = {}
for _country, _cap in CAPITALS.items():
    if "," not in _cap or _cap.endswith("D.C."):
        _REVERSE.setdefault(_cap.lower(), _DISPLAY.get(_country, ("the " if _country in _THE else "") + _country.title()))

_ELEMENTS = (
    "H Hydrogen,He Helium,Li Lithium,Be Beryllium,B Boron,C Carbon,N Nitrogen,O Oxygen,F Fluorine,Ne Neon,Na Sodium,"
    "Mg Magnesium,Al Aluminium,Si Silicon,P Phosphorus,S Sulfur,Cl Chlorine,Ar Argon,K Potassium,Ca Calcium,Sc Scandium,"
    "Ti Titanium,V Vanadium,Cr Chromium,Mn Manganese,Fe Iron,Co Cobalt,Ni Nickel,Cu Copper,Zn Zinc,Ga Gallium,Ge Germanium,"
    "As Arsenic,Se Selenium,Br Bromine,Kr Krypton,Rb Rubidium,Sr Strontium,Y Yttrium,Zr Zirconium,Nb Niobium,Mo Molybdenum,"
    "Tc Technetium,Ru Ruthenium,Rh Rhodium,Pd Palladium,Ag Silver,Cd Cadmium,In Indium,Sn Tin,Sb Antimony,Te Tellurium,"
    "I Iodine,Xe Xenon,Cs Caesium,Ba Barium,La Lanthanum,Ce Cerium,Pr Praseodymium,Nd Neodymium,Pm Promethium,Sm Samarium,"
    "Eu Europium,Gd Gadolinium,Tb Terbium,Dy Dysprosium,Ho Holmium,Er Erbium,Tm Thulium,Yb Ytterbium,Lu Lutetium,Hf Hafnium,"
    "Ta Tantalum,W Tungsten,Re Rhenium,Os Osmium,Ir Iridium,Pt Platinum,Au Gold,Hg Mercury,Tl Thallium,Pb Lead,Bi Bismuth,"
    "Po Polonium,At Astatine,Rn Radon,Fr Francium,Ra Radium,Ac Actinium,Th Thorium,Pa Protactinium,U Uranium,Np Neptunium,"
    "Pu Plutonium,Am Americium,Cm Curium,Bk Berkelium,Cf Californium,Es Einsteinium,Fm Fermium,Md Mendelevium,No Nobelium,"
    "Lr Lawrencium,Rf Rutherfordium,Db Dubnium,Sg Seaborgium,Bh Bohrium,Hs Hassium,Mt Meitnerium,Ds Darmstadtium,"
    "Rg Roentgenium,Cn Copernicium,Nh Nihonium,Fl Flerovium,Mc Moscovium,Lv Livermorium,Ts Tennessine,Og Oganesson")
ELEMENTS = [(i + 1, *item.split(" ")) for i, item in enumerate(_ELEMENTS.split(","))]  # (number, symbol, name)
BY_NAME = {name.lower(): e for e in ELEMENTS for name in (e[2],)}
BY_NAME.update({"aluminum": ELEMENTS[12], "sulphur": ELEMENTS[15], "cesium": ELEMENTS[54]})
BY_SYMBOL = {e[1].lower(): e for e in ELEMENTS}
MASS = {"hydrogen": "1.008", "helium": "4.0026", "lithium": "6.94", "beryllium": "9.0122", "boron": "10.81", "carbon": "12.011",
        "nitrogen": "14.007", "oxygen": "15.999", "fluorine": "18.998", "neon": "20.180", "sodium": "22.990", "magnesium": "24.305",
        "aluminium": "26.982", "aluminum": "26.982", "silicon": "28.085", "phosphorus": "30.974", "sulfur": "32.06",
        "chlorine": "35.45", "argon": "39.948", "potassium": "39.098", "calcium": "40.078"}
FORMULAS = {"water": "H2O", "carbon dioxide": "CO2", "oxygen": "O2", "ozone": "O3", "methane": "CH4", "ammonia": "NH3",
            "table salt": "NaCl", "salt": "NaCl", "sodium chloride": "NaCl", "hydrogen peroxide": "H2O2", "glucose": "C6H12O6",
            "sulfuric acid": "H2SO4", "sulphuric acid": "H2SO4", "hydrochloric acid": "HCl", "nitric acid": "HNO3",
            "sodium hydroxide": "NaOH", "carbon monoxide": "CO", "baking soda": "NaHCO3", "sodium bicarbonate": "NaHCO3",
            "ethanol": "C2H5OH", "calcium carbonate": "CaCO3", "acetic acid": "CH3COOH", "vinegar": "CH3COOH", "nitrogen": "N2",
            "hydrogen": "H2", "chlorine": "Cl2", "sugar": "C12H22O11", "sucrose": "C12H22O11"}
FORMULA_NAMES = {"h2o": "water", "co2": "carbon dioxide", "o2": "oxygen", "o3": "ozone", "ch4": "methane", "nh3": "ammonia",
                 "nacl": "sodium chloride, or table salt", "h2o2": "hydrogen peroxide", "c6h12o6": "glucose",
                 "h2so4": "sulfuric acid", "hcl": "hydrochloric acid", "hno3": "nitric acid", "naoh": "sodium hydroxide",
                 "co": "carbon monoxide", "nahco3": "sodium bicarbonate, or baking soda", "c2h5oh": "ethanol",
                 "caco3": "calcium carbonate", "ch3cooh": "acetic acid, the acid in vinegar", "n2": "nitrogen", "h2": "hydrogen",
                 "cl2": "chlorine", "c12h22o11": "sucrose, or table sugar"}
FORMULA_DISPLAY = {v.lower(): v for v in FORMULAS.values()}
FULL_FORMS = {
    "cpu": "Central Processing Unit", "gpu": "Graphics Processing Unit", "ram": "Random Access Memory", "rom": "Read Only Memory",
    "ssd": "Solid State Drive", "hdd": "Hard Disk Drive", "usb": "Universal Serial Bus", "bios": "Basic Input Output System",
    "os": "Operating System", "gui": "Graphical User Interface", "cli": "Command Line Interface",
    "ide": "Integrated Development Environment", "api": "Application Programming Interface", "url": "Uniform Resource Locator",
    "http": "Hypertext Transfer Protocol", "https": "Hypertext Transfer Protocol Secure", "html": "Hypertext Markup Language",
    "css": "Cascading Style Sheets", "json": "JavaScript Object Notation", "sql": "Structured Query Language",
    "xml": "Extensible Markup Language", "pdf": "Portable Document Format", "jpeg": "Joint Photographic Experts Group",
    "png": "Portable Network Graphics", "gif": "Graphics Interchange Format", "dns": "Domain Name System", "ip": "Internet Protocol",
    "tcp": "Transmission Control Protocol", "udp": "User Datagram Protocol", "lan": "Local Area Network",
    "wan": "Wide Area Network", "vpn": "Virtual Private Network", "isp": "Internet Service Provider",
    "gps": "Global Positioning System", "led": "Light Emitting Diode", "lcd": "Liquid Crystal Display",
    "ai": "Artificial Intelligence", "ml": "Machine Learning", "iot": "Internet of Things", "ar": "Augmented Reality",
    "vr": "Virtual Reality", "pin": "Personal Identification Number", "otp": "One Time Password",
    "atm": "Automated Teller Machine", "upi": "Unified Payments Interface", "gst": "Goods and Services Tax",
    "pan": "Permanent Account Number", "nasa": "National Aeronautics and Space Administration",
    "isro": "Indian Space Research Organisation", "unesco": "United Nations Educational, Scientific and Cultural Organization",
    "unicef": "United Nations Children's Fund", "who": "World Health Organization", "un": "United Nations",
    "nato": "North Atlantic Treaty Organization", "cbse": "Central Board of Secondary Education",
    "ncert": "National Council of Educational Research and Training", "jee": "Joint Entrance Examination",
    "neet": "National Eligibility cum Entrance Test", "iit": "Indian Institute of Technology", "diy": "Do It Yourself",
    "faq": "Frequently Asked Questions", "asap": "As Soon As Possible", "ceo": "Chief Executive Officer",
    "pc": "Personal Computer", "hdmi": "High Definition Multimedia Interface", "dvd": "Digital Versatile Disc",
    "cd": "Compact Disc", "sim": "Subscriber Identity Module", "sms": "Short Message Service",
    "gsm": "Global System for Mobile Communications", "nfc": "Near Field Communication", "rgb": "Red Green Blue",
    "dpi": "Dots Per Inch", "fps": "Frames Per Second", "lte": "Long Term Evolution", "mms": "Multimedia Messaging Service",
    "am": "Ante Meridiem, which is Latin for before midday", "pm": "Post Meridiem, which is Latin for after midday",
    "ssh": "Secure Shell", "ftp": "File Transfer Protocol", "smtp": "Simple Mail Transfer Protocol", "ui": "User Interface",
    "ux": "User Experience", "seo": "Search Engine Optimization", "saas": "Software as a Service", "ocr": "Optical Character Recognition",
    "tts": "Text To Speech", "stt": "Speech To Text", "llm": "Large Language Model",
}
_DAY_SEQ = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTH_SEQ = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
              "december"]
SHAPES = {"triangle": 3, "square": 4, "rectangle": 4, "quadrilateral": 4, "pentagon": 5, "hexagon": 6, "heptagon": 7,
          "octagon": 8, "nonagon": 9, "decagon": 10, "dodecagon": 12}
STATIC = [
    (r"how many continents(?: are there)?|how many continents are there in the world", "Seven: Asia, Africa, North America, South America, Antarctica, Europe and Australia."),
    (r"how many oceans(?: are there)?", "Five: the Pacific, Atlantic, Indian, Southern and Arctic."),
    (r"how many planets(?: are there)?(?: in (?:the|our) solar system)?|(?:name|list) the planets|planets in order",
     "Eight: Mercury, Venus, Earth, Mars, Jupiter, Saturn, Uranus and Neptune, in order from the sun."),
    (r"(?:what is )?the (?:biggest|largest) planet|which planet is the (?:biggest|largest)", "Jupiter is the largest planet."),
    (r"(?:what is )?the smallest planet|which planet is the smallest", "Mercury is the smallest planet."),
    (r"(?:what is )?the hottest planet|which planet is the hottest", "Venus is the hottest planet, at about 465 degrees Celsius."),
    (r"(?:what is )?the (?:closest|nearest) planet to the sun|which planet is (?:closest|nearest) to the sun", "Mercury is closest to the sun."),
    (r"(?:what is )?the red planet|which planet is (?:called )?the red planet", "Mars is the red planet."),
    (r"(?:what is )?the (?:biggest|largest) ocean|which ocean is the (?:biggest|largest)", "The Pacific Ocean is the largest."),
    (r"(?:what is )?the (?:biggest|largest) continent|which continent is the (?:biggest|largest)", "Asia is the largest continent."),
    (r"(?:what is )?the smallest continent|which continent is the smallest", "Australia, also called Oceania, is the smallest continent."),
    (r"(?:what is )?the (?:biggest|largest) country(?: in the world)?(?: by area)?|which country is the (?:biggest|largest)",
     "Russia is the largest country by area."),
    (r"(?:what is )?the smallest country(?: in the world)?|which country is the smallest", "Vatican City is the smallest country."),
    (r"(?:what is )?the (?:tallest|highest) mountain(?: in the world)?|how tall is (?:mount |mt )?everest", "Mount Everest, at about 8,849 metres above sea level."),
    (r"(?:what is )?the (?:deepest|deepest part of the) ocean|how deep is the (?:mariana trench|deepest ocean)", "The Mariana Trench, at about 11 kilometres deep."),
    (r"(?:what is )?the (?:tallest|highest) building(?: in the world)?", "The Burj Khalifa in Dubai, at 828 metres."),
    (r"(?:what is )?the fastest land animal|which animal is the fastest", "The cheetah, at up to about 110 kilometres an hour."),
    (r"(?:what is )?the (?:biggest|largest) animal(?: in the world)?", "The blue whale."),
    (r"(?:what is )?the tallest animal", "The giraffe."),
    (r"(?:what is )?the speed of light|how fast does light travel|how fast is light", "299,792,458 metres per second, about 300,000 kilometres per second."),
    (r"(?:what is )?the speed of sound|how fast does sound travel|how fast is sound", "About 343 metres per second in air at 20 degrees Celsius."),
    (r"(?:what is )?(?:the )?(?:acceleration (?:due to|of) gravity|gravity on earth|value of g)|how strong is gravity", "About 9.81 metres per second squared."),
    (r"(?:what is )?the boiling point of water|at what temperature does water boil|when does water boil", "100 degrees Celsius, or 212 Fahrenheit, at sea level."),
    (r"(?:what is )?the freezing point of water|at what temperature does water freeze|when does water freeze", "0 degrees Celsius, or 32 Fahrenheit."),
    (r"(?:what is )?absolute zero", "Minus 273.15 degrees Celsius, which is zero kelvin."),
    (r"(?:what is )?(?:a |the )?(?:normal|average) (?:human )?body temperature", "About 37 degrees Celsius, or 98.6 Fahrenheit."),
    (r"(?:how far|what is the distance) (?:is|to) (?:the )?moon|distance to the moon", "About 384,400 kilometres."),
    (r"(?:how far|what is the distance) (?:is|to) (?:the )?sun|distance to the sun", "About 150 million kilometres. Light takes about eight minutes to cover it."),
    (r"(?:what is )?the age of the universe|how old is the universe", "About 13.8 billion years."),
    (r"(?:what is )?the age of the earth|how old is the earth", "About 4.5 billion years."),
    (r"(?:what is )?avogadro'?s? (?:number|constant)", "6.022 times 10 to the 23, per mole."),
    (r"how many bones (?:are )?(?:there )?in the (?:human )?body|how many bones do (?:humans|adults) have", "An adult has 206 bones."),
    (r"how many teeth do (?:adults|humans|people) have|how many teeth does an adult have", "An adult has 32 teeth."),
    (r"how many chromosomes do (?:humans|people) have", "46, in 23 pairs."),
    (r"how many colou?rs (?:are )?(?:there )?in (?:a|the) rainbow", "Seven: red, orange, yellow, green, blue, indigo and violet."),
    (r"how many states (?:are )?(?:there )?in (?:the )?(?:usa|us|united states|america)", "Fifty."),
    (r"how many states (?:are )?(?:there )?in india", "28 states and 8 union territories."),
    (r"how many letters (?:are )?(?:there )?in the (?:english )?alphabet", "26."),
    (r"how many players (?:are )?(?:there )?in a cricket team|how many players in cricket", "11 players a side."),
    (r"how many players (?:are )?(?:there )?in a (?:football|soccer) team", "11 players a side."),
    (r"how many players (?:are )?(?:there )?in a basketball team", "5 on the court for each team."),
    (r"how many players (?:are )?(?:there )?in a (?:hockey|field hockey) team", "11 on the field for each team."),
    (r"how many players (?:are )?(?:there )?in a volleyball team", "6 on the court for each team."),
    (r"how many players (?:are )?(?:there )?in a baseball team", "9 players a side."),
    (r"how many players (?:are )?(?:there )?in a kabaddi team", "7 on the mat for each team."),
    (r"how many degrees (?:are )?(?:there )?in a circle", "360."),
    (r"how many degrees (?:are )?(?:there )?in a triangle|what do the angles of a triangle add up to", "The angles of a triangle add up to 180 degrees."),
    (r"how many degrees (?:are )?(?:there )?in a (?:square|rectangle|quadrilateral)", "The angles add up to 360 degrees."),
    (r"how many zeros (?:are )?(?:there )?in a million", "6."),
    (r"how many zeros (?:are )?(?:there )?in a billion", "9."),
    (r"how many zeros (?:are )?(?:there )?in a trillion", "12."),
    (r"how many zeros (?:are )?(?:there )?in a lakh", "5."),
    (r"how many zeros (?:are )?(?:there )?in a crore", "7."),
    (r"how many lakhs? (?:are )?(?:there )?in a crore", "100 lakh make a crore."),
    (r"how many millions (?:are )?(?:there )?in a billion", "1,000 million make a billion."),
    (r"how many thousands? (?:are )?(?:there )?in a million", "1,000 thousand make a million."),
    (r"how many seasons (?:are )?(?:there )?in a year", "Four: spring, summer, autumn and winter."),
    (r"how many time zones (?:are )?(?:there )?in the world", "24 standard time zones, though there are more offsets in use."),
    (r"what is the national (?:anthem|song) of india", "Jana Gana Mana is the national anthem, and Vande Mataram is the national song."),
]
_STATIC = [(re.compile(f"^(?:{p})$"), reply) for p, reply in STATIC]


def _the(country: str) -> str:
    return _DISPLAY.get(country, ("the " if country in _THE else "") + country.title())


_CAPITAL_Q = [re.compile(p) for p in (
    r"^what is the capital (?:city )?of (?:the )?(.+)$", r"^(?:the )?capital (?:city )?of (?:the )?(.+)$",
    r"^what is (?:the )?(.+?)'s capital(?: city)?$", r"^which city is the capital of (?:the )?(.+)$",
    r"^what is the capital of (?:the )?(.+) again$")]
_CAPITAL_OF = [re.compile(p) for p in (
    r"^what is (.+?) the capital of$", r"^(.+?) is the capital of what(?: country)?$", r"^what country is (.+?) the capital of$",
    r"^which country has (.+?) as (?:its )?capital$", r"^(.+?) is the capital of which country$")]


def capitals(q: Query) -> str | None:
    for pattern in _CAPITAL_Q:
        m = pattern.match(q.cmd)
        if m and m[1].strip().replace("'", "") in CAPITALS:
            country = m[1].strip().replace("'", "")
            capital = CAPITALS[country]
            return f"The capital of {_the(country)} is {capital}{'' if capital.endswith('.') else '.'}"
    for pattern in _CAPITAL_OF:
        m = pattern.match(q.cmd)
        if m and m[1].strip() in _REVERSE:
            return f"{m[1].strip().title()} is the capital of {_REVERSE[m[1].strip()]}."
    return None


def _element(text: str):
    t = text.strip().lower()
    return BY_NAME.get(t) or (BY_SYMBOL.get(t) if len(t) <= 2 else None)


def chemistry(q: Query) -> str | None:
    c = q.cmd
    m = re.match(r"^(?:what is )?(?:the )?(?:chemical )?symbol (?:for|of) (?:the element )?(.+)$", c)
    if m:
        e = BY_NAME.get(m[1].strip())
        return f"The symbol for {m[1].strip()} is {e[1]}." if e else None
    m = re.match(r"^(?:what is )?(?:the )?atomic number of (.+)$", q.digits)
    if m:
        e = _element(m[1])
        return f"{e[2]} has atomic number {e[0]}." if e else None
    m = re.match(r"^(?:what is )?(?:the )?atomic (?:mass|weight) of (.+)$", c)
    if m and m[1].strip() in MASS:
        return f"The atomic mass of {m[1].strip()} is about {MASS[m[1].strip()]}."
    m = (re.match(r"^what element (?:has|is) (?:the )?(?:chemical )?(?:symbol )?([a-z]{1,2})$", c)
         or re.match(r"^what (?:element )?does ([a-z]{1,2}) stand for(?: in chemistry)?$", c)
         or re.match(r"^what is the element ([a-z]{1,2})$", c))
    if m and m[1] in BY_SYMBOL:
        e = BY_SYMBOL[m[1]]
        return f"{e[1]} is {e[2].lower()}."
    m = re.match(r"^what element (?:has|is) (?:the )?atomic number (\d+)$|^(?:what is )?element (?:number )?(\d+)$", q.digits)
    if m:
        n = int(m[1] or m[2])
        if 1 <= n <= 118:
            e = ELEMENTS[n - 1]
            return f"Element {n} is {e[2].lower()}, symbol {e[1]}."
    m = re.match(r"^(?:what is )?(?:the )?(?:chemical )?formula (?:for|of) (.+)$", c)
    if m and m[1].strip() in FORMULAS:
        return f"The formula for {m[1].strip()} is {FORMULAS[m[1].strip()]}."
    m = re.match(r"^what is ([a-z0-9]{2,10})$", c)
    if m and m[1] in FORMULA_NAMES and (re.search(r"\d", m[1]) or len(m[1]) >= 3):
        return f"{FORMULA_DISPLAY[m[1]]} is {FORMULA_NAMES[m[1]]}."
    return None


_FULL = [re.compile(p) for p in (
    r"^what is the full form of ([a-z0-9]+)$", r"^([a-z0-9]+) full form$", r"^what does ([a-z0-9]+) stand for$",
    r"^what is ([a-z0-9]+) short for$", r"^expand ([a-z0-9]+)$", r"^what is the (?:long|full) form of ([a-z0-9]+)$",
    r"^full form of ([a-z0-9]+)$")]


def full_forms(q: Query) -> str | None:
    for pattern in _FULL:
        m = pattern.match(q.cmd)
        if m and m[1] in FULL_FORMS:
            return f"{m[1].upper()} stands for {FULL_FORMS[m[1]]}."
    return None


def constants_and_counts(q: Query) -> str | None:
    for pattern, reply in _STATIC:
        if pattern.match(q.cmd):
            return reply
    m = re.match(r"^how many (?:sides|corners|angles|vertices) (?:does|do) (?:a |an )?([a-z]+) have$", q.cmd)
    if m and m[1] in SHAPES:
        return f"A {m[1]} has {SHAPES[m[1]]} sides."
    m = re.match(r"^what (day|month) (?:comes |is )?(?:right )?(after|before) ([a-z]+)$", q.cmd)
    if m:
        seq = _DAY_SEQ if m[1] == "day" else _MONTH_SEQ
        if m[3] in seq:
            nxt = seq[(seq.index(m[3]) + (1 if m[2] == "after" else -1)) % len(seq)]
            return f"{nxt.title()} comes {m[2]} {m[3].title()}."
    m = re.match(r"^how many letters (?:are )?in (?:the word )?([a-z']+)$", q.cmd)
    if m and m[1] not in ("the", "alphabet"):
        return f"{m[1].capitalize()} has {len(m[1].replace(chr(39), ''))} letters."
    return None


RULES = (
    Rule("capitals", CATEGORY, ("what is the capital of france", "capital of japan", "what's the capital of india",
                                "which city is the capital of australia", "what is paris the capital of",
                                "what's the capital of the usa", "capital of south africa"), capitals),
    Rule("chemistry", CATEGORY, ("what is the chemical symbol for gold", "atomic number of oxygen", "what element has the symbol Fe",
                                 "what is the atomic mass of carbon", "what is the formula for water", "what is H2O",
                                 "element 26", "what does Na stand for in chemistry", "what is the atomic number of helium"),
         chemistry),
    Rule("full forms", CATEGORY, ("what is the full form of cpu", "ram full form", "what does usb stand for", "what is gps short for",
                                  "expand api", "what is the full form of upi"), full_forms),
    Rule("constants and counts", CATEGORY, (
        "how many continents are there", "how many planets are there", "what is the largest planet", "what is the speed of light",
        "what is the boiling point of water", "what is absolute zero", "how many bones are in the human body",
        "how many players are in a cricket team", "how many zeros in a crore", "how many lakh in a crore",
        "how many sides does a hexagon have", "what day comes after friday", "what month comes before march",
        "how many letters in necessary", "what is the tallest mountain", "how far is the moon", "how many states in india",
        "what is the freezing point of water", "how old is the universe", "how many degrees in a circle"), constants_and_counts),
)
