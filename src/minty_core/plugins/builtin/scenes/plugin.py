"""Scenes: named lists of web addresses and apps from `overlay.scenes`. Opening anything asks first (class 2)."""
from minty_core import scenes as scenelib
from minty_core.plugins.host import Tool


def register(ctx):
    def configured() -> dict[str, list[str]]:
        return dict(ctx.services["config"]().overlay.scenes)

    def list_scenes(args):
        sc = configured()
        return "; ".join(f"{n}: {len(t)} item{'s' * (len(t) != 1)}" for n, t in sorted(sc.items())) or \
            "No scenes set. Add some under overlay.scenes."

    def launch(args):
        n = scenelib.launch(str(args["name"]), configured())
        return f"Opened {n} item{'s' * (n != 1)} for {args['name']}."

    def describe(args):
        sc = configured().get(str(args.get("name")), [])
        return f"Open {len(sc)} things for the {args.get('name')} scene: " + ", ".join(t[:60] for t in sc[:5])

    obj = {"type": "object", "properties": {}}
    return [
        Tool("list", "List the saved scenes.", obj, list_scenes),
        Tool("launch", "Open a saved scene by name.",
             {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}, launch, risk=2,
             describe=describe),
    ]
