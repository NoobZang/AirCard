import base64
import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import aircard_backend
import apply_card_skin


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class CardFlashTests(unittest.TestCase):
    def test_cache_removal_moves_link_and_required_companion_payload(self) -> None:
        successful = {
            "exitCode": 0,
            "targetGatePassed": True,
            "operation": {"ok": True},
        }
        with (
            patch.object(apply_card_skin, "native", return_value=successful),
            patch.object(apply_card_skin, "run_json", return_value={"exitCode": 0, "ok": True}) as transfer,
        ):
            result = apply_card_skin.remove_files(
                "device", "/protected/card.cache", ["FrontFace"], retries=1
            )

        self.assertTrue(result)
        command = transfer.call_args.args[0]
        self.assertEqual(len(command), 6)
        self.assertIn("/airlift-link-", command[4])
        self.assertTrue(command[5].endswith("/removed-0"))

    def test_flash_writes_pdf_and_removes_rendered_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            write_file = Mock(return_value=True)
            remove_files = Mock(return_value=True)

            with (
                patch.object(aircard_backend, "write_file", write_file),
                patch.object(aircard_backend, "write_files_batch", Mock(return_value=False)),
                patch.object(aircard_backend, "remove_files", remove_files),
                redirect_stdout(io.StringIO()),
            ):
                result = aircard_backend.cmd_flash("device", "card", str(image_path))

        self.assertTrue(result)

        writes = [call.args for call in write_file.call_args_list]
        pass_assets = {
            leaf: payload
            for _, target, leaf, payload in writes
            if target.endswith(".pkpass")
        }
        self.assertEqual(
            set(pass_assets),
            {
                "cardBackgroundCombined@3x.png",
                "cardBackgroundCombined@2x.png",
                "cardBackgroundCombined.pdf",
            },
        )
        self.assertTrue(pass_assets["cardBackgroundCombined.pdf"].startswith(b"%PDF-"))

        removals = [call.args for call in remove_files.call_args_list]
        for extension in (".cache", ".pkcache"):
            self.assertIn(("device", f"/var/mobile/Library/Passes/Cards/card{extension}", list(aircard_backend.CACHE_FILES)), removals)

    def test_flash_fails_when_wallet_cache_cannot_be_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            output = io.StringIO()
            with (
                patch.object(aircard_backend, "write_files_batch", return_value=True),
                patch.object(aircard_backend, "remove_files", side_effect=[True, False]),
                redirect_stdout(output),
            ):
                result = aircard_backend.cmd_flash("device", "card", str(image_path))
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertFalse(result)
        self.assertEqual(messages[-1]["type"], "error")

    def test_flash_reports_failure_when_an_asset_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            write_file = Mock(
                side_effect=[True, True, False, True, True, True, True, True, True]
            )
            output = io.StringIO()

            with (
                patch.object(aircard_backend, "write_files_batch", Mock(return_value=False)),
                patch.object(aircard_backend, "write_file", write_file),
                patch.object(aircard_backend, "remove_files", Mock(return_value=True)),
                redirect_stdout(output),
            ):
                result = aircard_backend.cmd_flash("device", "card", str(image_path))

        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertFalse(result)
        self.assertEqual(messages[-1]["type"], "error")
        self.assertFalse(any(message["type"] == "success" for message in messages))

    def test_flash_streams_per_file_progress_and_finishes_at_total(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            output = io.StringIO()

            def batch(_udid, _target, files, retries=3, progress_callback=None):
                self.assertEqual(retries, 3)
                self.assertIsNotNone(progress_callback)
                for index, (leaf, _payload) in enumerate(files, 1):
                    progress_callback({
                        "type": "atc_progress",
                        "index": index,
                        "total": len(files),
                        "leaf": leaf,
                    })
                return True

            with (
                patch.object(aircard_backend, "write_files_batch", side_effect=batch),
                patch.object(aircard_backend, "remove_files", return_value=True),
                redirect_stdout(output),
            ):
                result = aircard_backend.cmd_flash("device", "card", str(image_path))

        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        written = [
            message for message in messages
            if message.get("message", "").startswith("Writing cardBackground")
        ]
        self.assertTrue(result)
        self.assertEqual([message["step"] for message in written], [1, 2, 3])
        self.assertTrue(all(message["total"] == 5 for message in written))
        self.assertEqual(messages[-1]["type"], "success")
        self.assertEqual(messages[-1]["step"], messages[-1]["total"])

    def test_flash_fallback_emits_per_file_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            output = io.StringIO()
            with (
                patch.object(aircard_backend, "write_files_batch", return_value=False),
                patch.object(aircard_backend, "write_file", return_value=True),
                patch.object(aircard_backend, "remove_files", return_value=True),
                redirect_stdout(output),
            ):
                result = aircard_backend.cmd_flash("device", "card", str(image_path))

        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        fallback = [
            message for message in messages
            if message.get("message", "").startswith("[Fallback] Writing")
        ]
        self.assertTrue(result)
        self.assertEqual(len(fallback), 3)
        self.assertEqual([message["step"] for message in fallback], [1, 2, 3])
        self.assertTrue(all(message["total"] == 5 for message in fallback))
        self.assertEqual(messages[-1]["step"], 5)
        self.assertEqual(messages[-1]["total"], 5)

    @staticmethod
    def _wallet_database(unique_id: str) -> bytes:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "passes23.sqlite"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE pass (pid INTEGER PRIMARY KEY, unique_id TEXT, foreground_color TEXT, label_color TEXT)"
            )
            connection.execute(
                "INSERT INTO pass (unique_id, foreground_color, label_color) VALUES (?, ?, ?)",
                (unique_id, "rgba(255, 0, 0, 1.00)", "rgba(255, 0, 0, 1.00)"),
            )
            connection.commit()
            connection.close()
            return path.read_bytes()

    def test_card_number_color_updates_pass_json_and_matching_database_row(self) -> None:
        pass_json = b'{"foregroundColor":"rgb(255,0,0)","labelColor":"rgb(255,0,0)","organizationName":"Bank"}'
        write_file = Mock(return_value=True)
        with (
            patch.object(aircard_backend, "read_file", side_effect=[pass_json, self._wallet_database("target-card")]),
            patch.object(aircard_backend, "write_file", write_file),
        ):
            ok, message = aircard_backend.update_card_number_color("device", "target-card", "#FFFFFF")

        self.assertTrue(ok, message)
        self.assertEqual(write_file.call_count, 2)
        _, target, leaf, payload = write_file.call_args_list[0].args
        self.assertEqual((target, leaf), ("/var/mobile/Library/Passes/Cards/target-card.pkpass", "pass.json"))
        metadata = json.loads(payload)
        self.assertEqual(metadata["foregroundColor"], "rgb(255,255,255)")
        self.assertEqual(metadata["labelColor"], "rgb(255,255,255)")
        self.assertEqual(metadata["organizationName"], "Bank")

        _, target, leaf, payload = write_file.call_args_list[1].args
        self.assertEqual((target, leaf), ("/var/mobile/Library/Passes", "passes23.sqlite"))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "passes23.sqlite"
            path.write_bytes(payload)
            connection = sqlite3.connect(path)
            row = connection.execute(
                "SELECT foreground_color, label_color FROM pass WHERE unique_id = ?", ("target-card",)
            ).fetchone()
            connection.close()
        self.assertEqual(row, ("rgba(255, 255, 255, 1.00)", "rgba(255, 255, 255, 1.00)"))

    def test_card_number_color_writes_nothing_when_card_is_absent_from_database(self) -> None:
        with (
            patch.object(aircard_backend, "read_file", side_effect=[b"{}", self._wallet_database("another-card")]),
            patch.object(aircard_backend, "write_file", Mock(return_value=True)) as write_file,
        ):
            ok, message = aircard_backend.update_card_number_color("device", "target-card", "#FFFFFF")

        self.assertFalse(ok)
        self.assertIn("exactly one matching card", message)
        write_file.assert_not_called()

    def test_card_number_color_rejects_malformed_values(self) -> None:
        for value in ("FFFFFF", "#FFF", "rgba(255, 255, 255, 1.00)", "#GGGGGG"):
            with self.assertRaises(ValueError):
                aircard_backend.parse_rgb_color(value)

    def test_flash_without_color_never_touches_pass_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            with (
                patch.object(aircard_backend, "write_files_batch", return_value=True),
                patch.object(aircard_backend, "remove_files", return_value=True),
                patch.object(aircard_backend, "update_card_number_color") as update_color,
                redirect_stdout(io.StringIO()),
            ):
                result = aircard_backend.cmd_flash("device", "card", str(image_path))

        self.assertTrue(result)
        update_color.assert_not_called()

    def test_flash_with_color_updates_metadata_and_counts_its_step(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            output = io.StringIO()
            with (
                patch.object(aircard_backend, "write_files_batch", return_value=True),
                patch.object(aircard_backend, "remove_files", return_value=True),
                patch.object(aircard_backend, "update_card_number_color", return_value=(True, "ok")) as update_color,
                redirect_stdout(output),
            ):
                result = aircard_backend.cmd_flash("device", "card", str(image_path), "#112233")

        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(result)
        update_color.assert_called_once_with("device", "card", "#112233")
        self.assertEqual(messages[-1]["type"], "success")
        self.assertEqual(messages[-1]["total"], 6)

    def test_color_only_flash_skips_artwork_but_clears_rendered_cache(self) -> None:
        output = io.StringIO()
        with (
            patch.object(aircard_backend, "write_files_batch") as write_batch,
            patch.object(aircard_backend, "remove_files", return_value=True) as remove_files,
            patch.object(aircard_backend, "update_card_number_color", return_value=(True, "ok")) as update_color,
            redirect_stdout(output),
        ):
            result = aircard_backend.cmd_flash("device", "card", None, "#112233")

        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(result)
        write_batch.assert_not_called()
        update_color.assert_called_once_with("device", "card", "#112233")
        self.assertTrue(remove_files.called)
        self.assertEqual(messages[-1]["type"], "success")
        self.assertEqual(messages[-1]["total"], 3)

    def test_flash_without_image_or_color_is_rejected(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertFalse(aircard_backend.cmd_flash("device", "card", None))

    def test_flash_skips_color_update_after_an_artwork_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            with (
                patch.object(aircard_backend, "write_files_batch", return_value=False),
                patch.object(aircard_backend, "write_file", return_value=False),
                patch.object(aircard_backend, "remove_files", return_value=True),
                patch.object(aircard_backend, "update_card_number_color") as update_color,
                redirect_stdout(io.StringIO()),
            ):
                result = aircard_backend.cmd_flash("device", "card", str(image_path), "#112233")

        self.assertFalse(result)
        update_color.assert_not_called()

    def test_flash_reports_failure_when_pdf_conversion_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            write_file = Mock(return_value=True)
            output = io.StringIO()

            with (
                patch.object(aircard_backend, "write_file", write_file),
                patch.object(
                    aircard_backend,
                    "build_card_assets",
                    side_effect=subprocess.CalledProcessError(1, ["sips"]),
                ),
                redirect_stdout(output),
            ):
                result = aircard_backend.cmd_flash("device", "card", str(image_path))

        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertFalse(result)
        self.assertEqual(messages[-1]["type"], "error")
        write_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
