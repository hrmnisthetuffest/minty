"""A health export sent to the bot is imported on the laptop and never reaches a model (W3B-04)."""
from tests.test_health_import import CRONOMETER, HEVY_CSV, STRAVA
from tests.test_telegram_gateway import OWNER, build
from minty_core.health import HealthHub
from minty_core.secretscan import SecretScanner
from minty_core.telegram.health import HealthImport
from minty_core.telegram.intake import Intake


class Recorder:
    history: list = []

    def __init__(self):
        self.calls = []

    async def respond(self, text, **kw):
        self.calls.append((text, kw))
        yield "A grocery list."


def setup(tmp_path, grant=("training", "nutrition", "activity"), **kw):
    agent = Recorder()
    gw, fake, store, _ = build(tmp_path, agent=agent, **kw)
    hub = HealthHub(tmp_path / "health.db")
    for d in grant:
        hub.grant(d)
    HealthImport(gw, lambda: hub).install()  # before the generic intake, as the runtime does
    Intake(gw, tmp_path / "inbox", SecretScanner(), ocr=None).install()
    return gw, fake, agent, hub


def send(fake, data: bytes, name: str):
    fake.add_file("d1", data, "documents/d1.bin")
    return fake.say(OWNER, "", document={"file_id": "d1", "file_name": name, "mime_type": "text/csv",
                                         "file_size": len(data)})


async def test_a_hevy_export_is_imported_locally_and_no_model_hears_about_it(tmp_path):
    gw, fake, agent, hub = setup(tmp_path)
    await gw.handle_update(send(fake, HEVY_CSV.encode(), "workouts.csv"))
    assert agent.calls == [] and hub.counts()["training"] == 2
    assert "Imported 2 new records from Hevy" in fake.texts(OWNER)[-1]
    assert not (tmp_path / "inbox").exists()  # not kept as a loose file either


async def test_the_same_export_again_says_so_and_a_partial_consent_is_explained(tmp_path):
    gw, fake, agent, hub = setup(tmp_path, grant=("nutrition",))
    await gw.handle_update(send(fake, CRONOMETER.encode(), "dailysummary.csv"))
    await gw.handle_update(send(fake, CRONOMETER.encode(), "dailysummary.csv"))
    assert "already imported" in fake.texts(OWNER)[-1]
    await gw.handle_update(send(fake, STRAVA.encode(), "activities.csv"))
    assert "not agreed" in fake.texts(OWNER)[-1] and hub.counts()["activity"] == 0 and agent.calls == []


async def test_a_broken_health_export_is_refused_here_and_never_given_to_a_model(tmp_path):
    gw, fake, agent, hub = setup(tmp_path)
    cut_short = '{"page": 1, "workouts": [{"id": "a", "title": "Push", "exercises": [{"exercise_template_id": "t",'
    await gw.handle_update(send(fake, cut_short.encode(), "hevy.json"))
    assert agent.calls == [] and "couldn't read" in fake.texts(OWNER)[-1]


async def test_an_ordinary_csv_goes_on_to_the_normal_file_reader(tmp_path):
    gw, fake, agent, hub = setup(tmp_path)
    await gw.handle_update(send(fake, b"item,price\nmilk,30\neggs,70\n", "shopping.csv"))
    assert agent.calls and "shopping.csv" in agent.calls[0][0]


async def test_in_private_mode_nothing_is_downloaded(tmp_path):
    gw, fake, agent, hub = setup(tmp_path, private=True)
    await gw.handle_update(send(fake, HEVY_CSV.encode(), "workouts.csv"))
    assert "getFile" not in fake.calls and hub.counts()["training"] == 0
