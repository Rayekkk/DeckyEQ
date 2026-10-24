"""The frontend tests use tests/fixtures/snapshot.json; it must be exactly what Service.snapshot() sends.
Regenerate after an intended change with: DECKYEQ_UPDATE_FIXTURES=1 python -m unittest tests.test_contract"""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deckyeq.model import flat_eq
from deckyeq.service import Service
from deckyeq.storage import PresetFiles, Store
from test_service import FakeRuntime

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "snapshot.json"


class ContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_frontend_fixture_is_the_backend_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            service = Service(Store(Path(temp) / "settings"), FakeRuntime(), PresetFiles(temp))
            eq = flat_eq()
            eq["bands"][0].update(type="lowshelf", gain=-3)
            with patch("deckyeq.model.uuid.uuid4") as uuid4:
                uuid4.return_value.hex = "0" * 32
                await service.action("edit", {"eq": eq}, 0)
                await service.action("save_as", {"name": "Warm"}, 1)
            await service.action("select", {"id": "flat"}, 2)
            await service.set_game("1145360", "Hades")  # A running game that has its own sound.
            await service.action("own_sound", {"enabled": True, "appid": "1145360"}, 3)
            snapshot = json.loads(json.dumps(service.snapshot()))
        snapshot.update(version="0.0.0", home="/home/deck", export_directory="/home/deck/Documents/DeckyEQ")
        if os.environ.get("DECKYEQ_UPDATE_FIXTURES") == "1":
            FIXTURE.parent.mkdir(exist_ok=True)
            FIXTURE.write_bytes((json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        self.assertEqual(snapshot, json.loads(FIXTURE.read_text(encoding="utf-8")))


class SchemaTests(unittest.TestCase):
    def test_published_preset_schema_matches_the_shared_limits(self):
        from deckyeq.model import FILTER_TYPES, LIMITS
        schema = json.loads((FIXTURE.parent.parent.parent / "schemas" / "preset-v2.schema.json").read_text(encoding="utf-8"))
        eq = schema["$defs"]["eq"]["properties"]
        band = eq["bands"]["items"]["properties"]
        span = lambda prop: [prop["minimum"], prop["maximum"]]
        self.assertEqual(eq["bands"]["minItems"], len(LIMITS["frequencies"]))
        self.assertEqual(eq["bands"]["maxItems"], len(LIMITS["frequencies"]))
        self.assertEqual(span(band["frequency"]), LIMITS["frequency"])
        self.assertEqual(span(band["gain"]), LIMITS["band_gain"])
        self.assertEqual(span(band["q"]), LIMITS["q"])
        self.assertEqual(band["type"]["enum"], list(FILTER_TYPES))
        self.assertEqual(span(eq["bass"]), LIMITS["tone_gain"])
        self.assertEqual(span(eq["treble"]), LIMITS["tone_gain"])
        self.assertEqual(span(eq["preamp"]), LIMITS["preamp"])
        self.assertEqual(span(eq["width"]), LIMITS["width"])  # Volume boost is global, so not in a preset.
        self.assertEqual(schema["$defs"]["preset"]["properties"]["name"]["maxLength"], LIMITS["name_length"])


class ActionTests(unittest.TestCase):
    def test_the_frontend_sends_the_backends_action_names(self):
        from deckyeq.service import HANDLERS
        code = "\n".join(path.read_text(encoding="utf-8") for path in (FIXTURE.parent.parent.parent / "src").glob("*.ts*")
                         if path.name != "updates.tsx")  # Its controller.run("check" | "download") is not a backend action.
        direct = {"refresh", "export"}  # Handled in Service._action itself, not through HANDLERS.
        sent = set(re.findall(r'\b(?:run|perform)\(\s*"(\w+)"', code))
        self.assertLessEqual(sent, set(HANDLERS) | direct)
        for name in set(HANDLERS) | direct:  # save_as and rename are passed to the name dialog instead.
            self.assertIn(f'"{name}"', code, name)


if __name__ == "__main__":
    unittest.main()
