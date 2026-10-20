from datetime import datetime

from minty_core.plugins.host import Tool


def register(ctx):
    def get_time(args):
        return datetime.now().astimezone().strftime("%A, %B %d, %Y, %I:%M %p %Z")

    return [Tool("get_time", "Get the current local date and time.", {"type": "object", "properties": {}}, get_time)]
