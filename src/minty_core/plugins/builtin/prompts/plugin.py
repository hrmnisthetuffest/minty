from minty_core.plugins.host import Tool
from minty_core.policy import R1, R2

TEXT = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}


def register(ctx):
    layers = ctx.services.get("prompts")

    def add_instruction(args):
        return f"Standing instruction saved: {layers.add_instruction(args.get('text'))}"

    def learn(args):
        entry = layers.learn(args.get("text"), "learned by Minty")
        return f"Learned: {entry['text']}" if entry else "Already known."

    def create_command(args):
        entry = layers.add_command(args.get("phrase"), args.get("action") or "", args.get("say"))
        return f"Custom command saved: when you say \"{entry['phrase']}\" I will {entry['action']}"

    def forget_command(args):
        return "Command removed." if layers.forget_command(args.get("phrase")) else "No such command."

    def forget_rule(args):
        key = str(args.get("text") or "").lower().strip()
        for r in layers.rules():
            if r["id"] == key or key and key in r["text"].lower():
                layers.forget_rule(r["id"])
                return f"Forgot: {r['text']}"
        return "No such learned rule."

    def listing(args):
        out = []
        instructions = layers.instructions().strip()
        if instructions:
            out.append("Standing instructions:\n" + instructions)
        if layers.rules():
            out.append("Learned:\n" + "\n".join(f"- {r['text']}" for r in layers.rules()))
        if layers.commands():
            out.append("Commands:\n" + "\n".join(f'- "{c["phrase"]}": {c["action"]}' for c in layers.commands()))
        return "\n\n".join(out) or "Nothing has been set up yet."

    def wrap(fn):
        def run(args):
            if layers is None:
                return "Error: prompt storage is unavailable."
            try:
                return fn(args)
            except ValueError as e:
                return f"Error: {e}"
        return run

    obj = {"type": "object", "properties": {}}
    return [
        Tool("add_instruction", "Save a standing instruction the user just gave ('from now on…', 'always…', "
             "'never…'). Only when the user said it themselves.", TEXT, wrap(add_instruction),
             risk=R2),  # a standing rule steers every later turn, so a page cannot plant one unasked
        Tool("learn", "Remember a durable preference or fact you inferred about the user, to behave better later. One "
                      "short sentence.", TEXT, wrap(learn), risk=R1),
        Tool("create_command", "Teach yourself a voice command the user asked for: when they say the phrase, do the "
                               "action. For a fixed reply, set say to the exact words.",
             {"type": "object", "properties": {"phrase": {"type": "string"}, "action": {"type": "string"},
                                               "say": {"type": "string", "description": "exact words to speak"}},
              "required": ["phrase"]}, wrap(create_command), risk=R2),
        Tool("forget_command", "Remove a custom command by its phrase.",
             {"type": "object", "properties": {"phrase": {"type": "string"}}, "required": ["phrase"]},
             wrap(forget_command), risk=R1),
        Tool("forget", "Forget a learned rule (match by words in it).", TEXT, wrap(forget_rule), risk=R1),
        Tool("list", "List the standing instructions, learned rules and custom commands.", obj, wrap(listing)),
    ]
