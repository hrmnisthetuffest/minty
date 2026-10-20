"""Games and small pleasures: coins, dice, random picks, jokes, facts, quotes, counting, spelling."""
from __future__ import annotations

import re

from minty_core.instant.base import Query, Rule

CATEGORY = "fun and games"

JOKES = (
    "Why don't scientists trust atoms? Because they make up everything.",
    "Why did the scarecrow win an award? He was outstanding in his field.",
    "What do you call a fake noodle? An impasta.",
    "Why did the math book look sad? It had too many problems.",
    "I only know 25 letters of the alphabet. I don't know y.",
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "There are 10 kinds of people: those who understand binary and those who don't.",
    "Why was the computer cold? It left its Windows open.",
    "What do you call cheese that isn't yours? Nacho cheese.",
    "Why can't you trust stairs? They're always up to something.",
    "I used to hate facial hair, but then it grew on me.",
    "What's a computer's favourite snack? Microchips.",
    "Why did the bicycle fall over? It was two tired.",
    "How does a penguin build its house? Igloos it together.",
    "Why did the developer go broke? He used up all his cache.",
    "What do you call a bear with no teeth? A gummy bear.",
    "Why don't eggs tell jokes? They'd crack each other up.",
    "I would tell you a UDP joke, but you might not get it.",
    "A SQL query walks into a bar, walks up to two tables and asks: can I join you?",
    "Why did the phone wear glasses? It lost its contacts.",
    "What did the ocean say to the beach? Nothing, it just waved.",
    "Parallel lines have so much in common. It's a shame they'll never meet.",
    "Why did the tomato blush? It saw the salad dressing.",
    "I'm reading a book about anti-gravity. It's impossible to put down.",
    "How do you organise a space party? You planet.",
    "Why was six afraid of seven? Because seven ate nine.",
    "What do you call a sleeping dinosaur? A dino-snore.",
    "What's orange and sounds like a parrot? A carrot.",
    "Why did the golfer bring two pairs of trousers? In case he got a hole in one.",
    "What do you call a factory that makes okay products? A satisfactory.",
)
FACTS = (
    "Honey never spoils. Edible honey has been found in ancient Egyptian tombs.",
    "Octopuses have three hearts.",
    "A day on Venus is longer than its year.",
    "Bananas are berries, but strawberries are not.",
    "The Eiffel Tower grows a few centimetres taller in summer, because the iron expands in the heat.",
    "Sharks are older than trees. They have been around for more than 400 million years.",
    "A group of flamingos is called a flamboyance.",
    "Wombat droppings are cube-shaped.",
    "A lightning bolt is several times hotter than the surface of the sun.",
    "Your brain uses about a fifth of your body's energy.",
    "Sound travels about four times faster in water than in air.",
    "The shortest war in history, between Britain and Zanzibar in 1896, lasted less than an hour.",
    "Cleopatra lived closer in time to the Moon landing than to the building of the Great Pyramid.",
    "Butterflies taste with their feet.",
    "Antarctica is the largest desert on Earth.",
    "The Moon is moving away from the Earth by about four centimetres a year.",
    "A day on Mars is about 24 hours and 37 minutes.",
    "Venus spins backwards compared with most planets.",
    "Saturn is less dense than water, so in a big enough bath it would float.",
    "The Great Wall of China is not visible from space with the naked eye.",
    "Hummingbirds are the only birds that can fly backwards.",
    "Humans and giraffes both have seven bones in their necks.",
    "Dolphins sleep with one half of the brain at a time.",
    "The first email was sent in 1971 by Ray Tomlinson.",
    "Ada Lovelace wrote what is often called the first computer program, in the 1840s.",
    "The Pacific Ocean covers more area than all the land on Earth put together.",
    "A cloud can weigh hundreds of tonnes.",
    "Mount Everest grows by a few millimetres every year.",
    "At its triple point, water can be ice, liquid and vapour all at once.",
    "Octopus blood is blue.",
)
QUOTES = (
    "“A journey of a thousand miles begins with a single step.” Laozi.",
    "“It does not matter how slowly you go as long as you do not stop.” Confucius.",
    "“The best time to plant a tree was twenty years ago. The second best time is now.” A Chinese proverb.",
    "“Well begun is half done.” Aristotle.",
    "“Fall seven times, stand up eight.” A Japanese proverb.",
    "“He who has a why to live can bear almost any how.” Friedrich Nietzsche.",
    "“Do what you can, with what you have, where you are.” Often credited to Theodore Roosevelt.",
    "“Energy and persistence conquer all things.” Benjamin Franklin.",
    "“Lost time is never found again.” Benjamin Franklin.",
    "“An investment in knowledge pays the best interest.” Benjamin Franklin.",
    "“Slow and steady wins the race.” Aesop.",
    "“Every expert was once a beginner.” A proverb.",
    "“A smooth sea never made a skilled sailor.” A proverb.",
    "“Where there's a will, there's a way.” A proverb.",
    "“The secret of getting ahead is getting started.” Often credited to Mark Twain.",
)
COMPLIMENTS = (
    "You ask good questions. That's how people get smart.",
    "You've got this.",
    "You're doing better than you think.",
    "Your curiosity is a real strength.",
    "Good work today. Keep going.",
    "You're sharper than you give yourself credit for.",
    "Take a breath. You've handled harder things than this.",
    "You're someone people are lucky to know.",
)
TWISTERS = (
    "She sells seashells by the seashore.",
    "Peter Piper picked a peck of pickled peppers.",
    "How much wood would a woodchuck chuck if a woodchuck could chuck wood?",
    "Red lorry, yellow lorry, red lorry, yellow lorry.",
    "Betty Botter bought some butter, but she said the butter's bitter.",
    "Unique New York, unique New York, you know you need unique New York.",
    "I scream, you scream, we all scream for ice cream.",
    "Fuzzy Wuzzy was a bear. Fuzzy Wuzzy had no hair.",
)
EIGHT_BALL = (
    "It is certain.", "It is decidedly so.", "Without a doubt.", "Yes, definitely.", "You may rely on it.", "As I see it, yes.",
    "Most likely.", "Outlook good.", "Yes.", "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
    "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.", "Don't count on it.", "My reply is no.",
    "My sources say no.", "Outlook not so good.", "Very doubtful.",
)
COLOURS = ("red", "orange", "yellow", "green", "blue", "purple", "pink", "teal", "turquoise", "magenta", "brown", "black", "white",
           "grey", "gold", "silver", "navy", "maroon", "lime", "mint")
RANKS = ("Ace", "2", "3", "4", "5", "6", "7", "8", "9", "10", "Jack", "Queen", "King")
SUITS = ("Spades", "Hearts", "Diamonds", "Clubs")


def _article(n: int) -> str:
    return "an" if n in (8, 11, 18) or 80 <= n <= 89 else "a"


def _join(items) -> str:
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# ---------------------------------------------------------------------------------------------- chance
_COIN = re.compile(r"^(?:flip|toss|spin|throw) (?:a |the )?coin$|^coin (?:flip|toss)$|^heads or tails$|^(?:flip|toss) a coin to decide$")
_DICE = re.compile(r"^(?:roll|throw|toss|cast|shake) (?:(a|an|the) )?(?:(\d+) )?(?:(\d+) ?sided )?(die|dice)$")
_DNOTATION = re.compile(r"^(?:roll|throw|toss) (?:a |an )?(\d*)d(\d+)$")
_DICE_PLAIN = re.compile(r"^(?:dice|die) roll$|^roll (?:some )?dice$")
_NUMBER_BETWEEN = re.compile(r"^(?:pick |choose |give |generate |say |get )?(?:me )?(?:a |an )?(?:random )?(?:number|integer) "
                             r"(?:between|from) (-?\d+) (?:and|to) (-?\d+)$|^random (-?\d+) (?:to|and) (-?\d+)$")
_NUMBER_ANY = re.compile(r"^(?:pick |give |generate |say |get )?(?:me )?(?:a |an )?random number$")
_NUMBER_PICK = re.compile(r"^(?:pick|choose|think of) (?:a )?number$")
_CARD = re.compile(r"^(?:pick|draw|deal|choose)(?: me)? (?:a |one )?(?:random )?card(?: from a deck)?$|^random card$")
_LETTER = re.compile(r"^(?:pick |give |say |choose )?(?:me )?(?:a |one )?random letter$")
_COLOUR = re.compile(r"^(?:pick |give |say |choose )?(?:me )?(?:a |one )?random colou?r$")
_YES_NO = re.compile(r"^(?:yes or no|pick yes or no|say yes or no)$")
_BALL = re.compile(r"^(?:ask |shake |consult )?(?:the )?(?:magic )?(?:8|eight) ?ball\b.*$|^magic (?:8|eight) ?ball.*$")
_RPS = re.compile(r"^(?:play )?rock paper scissors$")
_CHOOSE = re.compile(r"^(?:pick|choose|decide|select)(?: one)?(?: between| from| of| out of)? (.+)$")
_PARTICLES = ("up", "out", "off", "on", "in", "over", "down", "all", "a random", "number", "card", "letter", "colour", "color")


def chance(q: Query) -> str | None:
    d, rng = q.digits, q.env.rng
    if _COIN.match(q.cmd):
        return q.pick("Heads.", "Tails.")
    m = _DICE.match(d)
    count = sides = None
    if m:
        article, number, side, noun = m.groups()
        if number:
            count = int(number)
        else:
            count = 2 if noun == "dice" and article in (None, "the") else 1  # "roll the dice" is a pair, "a die" is one
        sides = int(side) if side else 6
    else:
        m = _DNOTATION.match(d)
        if m:
            count, sides = int(m[1] or 1), int(m[2])
        elif _DICE_PLAIN.match(d):
            count, sides = 1, 6
    if count is not None:
        if not (1 <= count <= 10 and 2 <= sides <= 1000):
            return None
        rolls = [rng.randint(1, sides) for _ in range(count)]
        suffix = "" if sides == 6 else f" on the d{sides}"
        if count == 1:
            return f"You rolled {_article(rolls[0])} {rolls[0]}{suffix}."
        return f"You rolled {_join(rolls)}, for a total of {sum(rolls)}."
    m = _NUMBER_BETWEEN.match(d)
    if m:
        low, high = (m[1], m[2]) if m[1] is not None else (m[3], m[4])
        a, b = sorted((int(low), int(high)))
        if b - a > 10**9:
            return None
        return f"Your number is {rng.randint(a, b)}."
    if _NUMBER_ANY.match(d):
        return f"Your number is {rng.randint(1, 100)}."
    if _NUMBER_PICK.match(d):
        return f"I pick {rng.randint(1, 10)}."
    if _CARD.match(d):
        return f"{rng.choice(RANKS)} of {rng.choice(SUITS)}."
    if _LETTER.match(d):
        return f"{rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}."
    if _COLOUR.match(d):
        return f"{rng.choice(COLOURS).capitalize()}."
    if _YES_NO.match(d):
        return q.pick("Yes.", "No.")
    if _BALL.match(d):
        return q.pick(*EIGHT_BALL)
    if _RPS.match(d):
        return f"Rock, paper, scissors, shoot! I pick {rng.choice(['rock', 'paper', 'scissors'])}."
    return None


def choose(q: Query) -> str | None:
    m = _CHOOSE.match(q.listy)
    if not m or not re.search(r"\bor\b|\bbetween\b|\bone of\b|\bfrom\b", q.listy):
        return None  # "pick up the kids and the dog" is an errand, not a choice
    rest = m[1].strip()
    if rest.startswith(_PARTICLES):
        return None
    options = [re.sub(r"^(?:between|from) ", "", o.strip(" .")) for o in re.split(r",\s*(?:or |and )?|\s+or\s+|\s+and\s+", rest)
               if o.strip(" .")]
    if not 2 <= len(options) <= 8 or any(len(o.split()) > 5 for o in options):
        return None
    return f"I pick {q.env.rng.choice(options)}."


# ---------------------------------------------------------------------------------------------- things to say
_JOKE = re.compile(r"^(?:an? |another |one more |some )?(?:funny |good |bad |dad |clean |silly |short )?(?:joke|jokes|one liner|pun|"
                   r"dad joke)$|^(?:make me laugh|say something funny|got any jokes|do you know any jokes|know any jokes|tell a joke|"
                   r"be funny|joke time|another one)$")
_FACT = re.compile(r"^(?:an? |another |one more |some )?(?:random |fun |interesting |cool |weird |amazing )?facts?$|^(?:did you know|"
                   r"tell me something interesting|surprise me|something interesting|teach me something|fun fact)$")
_QUOTE = re.compile(r"^(?:an? |another |some )?(?:motivational |inspirational |inspiring |famous |wise )?quote$|^(?:motivate me|"
                    r"inspire me|i need motivation|motivation|inspiration|some wisdom|i need inspiration|say something inspiring|"
                    r"encourage me|give me some motivation)$")
_COMPLIMENT = re.compile(r"^(?:compliment me|say something nice|say something nice to me|cheer me up|make me smile|be nice to me)$")
_TWISTER = re.compile(r"^(?:an? |another |some )?tongue twisters?$")


def stories(q: Query) -> str | None:
    if _JOKE.match(q.cmd):
        return q.pick(*JOKES)
    if _FACT.match(q.cmd):
        return q.pick(*FACTS)
    if _QUOTE.match(q.cmd):
        return q.pick(*QUOTES)
    if _COMPLIMENT.match(q.cmd):
        return q.pick(*COMPLIMENTS)
    if _TWISTER.match(q.cmd):
        return q.pick(*TWISTERS)
    return None


# ---------------------------------------------------------------------------------------------- counting and spelling
_ALPHABET = re.compile(r"^(?:say|recite|sing)(?: me)?(?: the)? (?:alphabet|abcs?)$|^(?:the )?alphabet$")
_COUNT_UP = re.compile(r"^count (?:up )?(?:to|up to) (\d+)$")
_COUNT_DOWN = re.compile(r"^count (?:down|backwards?) from (\d+)$")
_SPELL = re.compile(r"^(?:how do you |can you |please )?spell(?: out)?(?: the word)? ([a-z']+)$")
_NOT_WORDS = {"check", "it", "that", "this", "my", "your", "out", "something"}


def counting(q: Query) -> str | None:
    if _ALPHABET.match(q.cmd):
        return "A, B, C, D, E, F, G, H, I, J, K, L, M, N, O, P, Q, R, S, T, U, V, W, X, Y, Z."
    m = _COUNT_UP.match(q.digits)
    if m and 1 <= int(m[1]) <= 30:
        return ", ".join(str(i) for i in range(1, int(m[1]) + 1)) + "."
    m = _COUNT_DOWN.match(q.digits)
    if m and 1 <= int(m[1]) <= 30:
        return ", ".join(str(i) for i in range(int(m[1]), 0, -1)) + "."
    m = _SPELL.match(q.cmd)
    if m and m[1] not in _NOT_WORDS and len(m[1]) <= 30:
        return ", ".join(c.upper() for c in m[1].replace("'", "")) + "."
    return None


RULES = (
    Rule("chance", CATEGORY, (
        "flip a coin", "toss a coin", "heads or tails", "roll a die", "roll two dice", "roll a d20", "roll 2d6", "roll the dice",
        "roll a 20 sided die", "pick a number between 1 and 10", "random number between 5 and 50", "give me a random number",
        "pick a number", "pick a card", "pick a random letter", "give me a random colour", "yes or no", "ask the magic 8 ball",
        "shake the magic eight ball", "rock paper scissors", "number between 1 and 6"), chance),
    Rule("choose for me", CATEGORY, ("pick between pizza and burgers", "choose between red, blue or green", "decide between tea or coffee",
                                     "pick one of sleep, study or games"), choose),
    Rule("jokes, facts and quotes", CATEGORY, (
        "tell me a joke", "another joke", "make me laugh", "say something funny", "tell me a fun fact", "random fact", "did you know",
        "surprise me", "give me a quote", "motivate me", "inspire me", "compliment me", "say something nice", "cheer me up",
        "tell me a tongue twister", "got any jokes"), stories),
    Rule("counting and spelling", CATEGORY, ("say the alphabet", "count to 10", "count down from 5", "how do you spell necessary",
                                             "spell restaurant", "recite the alphabet", "count up to twenty"), counting),
)
