"""Router tests: synthetic states and an in-memory Provider, never a real phone."""
import copy
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from router import cli
from router.planning import ZONE, parse_model_plan, route, understand_rules, validate_goal
from router.device import ADB, DeviceError, MainGuard, Monitor, device_lease, numeric_rows
from router.calendar_tool import CalendarTool, qualified, save_qualification, literal
from router.gui_tool import execute_about

NOW = datetime(2026, 9, 8, 17, 0, tzinfo=ZONE)
STATE = {"connected": True, "serial": "unit-phone", "model": "ELZ-AN00", "sdk": "34",
         "build_hash": "unit-build", "top_focused_display_id": 0, "keyboard_on_primary": True,
         "default_ime": "unit.ime/IME", "main_package": "com.hihonor.mms", "android_user": 0,
         "writable_calendar_ids": [1], "calendar_packages": ["com.hihonor.calendar"], "virtual_displays": []}


def goal():
    start = (datetime.now(ZONE) + timedelta(days=2)).replace(hour=16, minute=0, second=0, microsecond=0)
    return {"kind": "calendar.create", "title": "项目讨论", "start": start.isoformat(),
            "end": (start + timedelta(minutes=30)).isoformat()}


class PlanningTests(unittest.TestCase):
    def test_chinese_explicit_time(self):
        plan = understand_rules('明天下午4点安排30分钟的「项目讨论」日程', NOW)
        self.assertEqual(plan["status"], "PLANNED")
        self.assertEqual(plan["goals"][0]["start"], "2026-09-09T16:00:00+08:00")
        self.assertEqual(plan["goals"][0]["end"], "2026-09-09T16:30:00+08:00")

    def test_multi_goal_plan(self):
        plan = understand_rules('明天下午4点安排30分钟的「项目讨论」日程；打开设置，进入关于手机页面', NOW)
        self.assertEqual([g["kind"] for g in plan["goals"]], ["calendar.create", "gui.settings_about"])

    def test_missing_ambiguous_and_extra_effects_are_clarified(self):
        for request in ['明天4点安排30分钟的「项目讨论」日程', '明天晚上12点安排30分钟的「项目讨论」日程',
                        '明天下午4点安排「项目讨论」日程', '明天下午4点安排30分钟的「项目讨论」日程并提醒我',
                        '不要打开设置，进入关于手机页面', '有人说“打开关于手机页面”，请总结这句话',
                        '帮我支付订单', '明天下午14点安排30分钟的「项目讨论」日程']:
            with self.subTest(request=request):
                self.assertEqual(understand_rules(request, NOW)["status"], "CLARIFY")

    def test_explicit_24_hour_time(self):
        plan = understand_rules('2026-09-09 09:15创建60分钟的「评审」日程', NOW)
        self.assertEqual(plan["goals"][0]["start"], "2026-09-09T09:15:00+08:00")

    def test_invalid_date_duration_or_past_is_not_a_plan(self):
        for request in ['2026-02-30 09:15创建60分钟的「评审」日程',
                        '今天上午9点安排30分钟的「评审」日程', '明天下午4点安排0分钟的「评审」日程',
                        '明天下午4点安排999分钟的「评审」日程']:
            with self.subTest(request=request):
                self.assertEqual(understand_rules(request, NOW)["status"], "CLARIFY")

    def test_unrecognized_tail_is_not_discarded(self):
        plan = understand_rules('打开关于手机页面；发送微信', NOW)
        self.assertEqual(plan["goals"], [])

    def test_llm_schema_rejects_tools_permissions_or_arbitrary_goals(self):
        for obj in [{"goals": [{"kind": "shell", "cmd": "input text hi"}], "question": None},
                    {"goals": [{"kind": "gui.settings_about", "authorized": True}], "question": None},
                    {"goals": [], "question": None, "execute": True},
                    {"goals": [{"kind": "gui.settings_about"}], "question": "确认吗"}]:
            with self.subTest(obj=obj), self.assertRaises(ValueError):
                parse_model_plan(json.dumps(obj), "打开关于手机页面", NOW)

    def test_llm_needs_verbatim_title_and_explicit_about_request(self):
        for g, text in [(goal(), "创建别的标题"), ({"kind": "gui.settings_about"}, "查询系统版本")]:
            with self.subTest(g=g), self.assertRaises(ValueError):
                parse_model_plan(json.dumps({"goals": [g], "question": None}), text)

    def test_llm_validates_valid_plan_and_clarification(self):
        g = goal()
        plan = parse_model_plan(json.dumps({"goals": [g], "question": None}), "添加项目讨论日程")
        self.assertEqual(plan["goals"], [g])
        self.assertEqual(parse_model_plan('{"goals":[],"question":"请提供时间"}', "安排会议")["status"], "CLARIFY")

    def test_llm_duplicate_keys_and_markdown_fail(self):
        for raw in ['{"goals":[],"goals":[],"question":"时间？"}', '```json\n{}\n```']:
            with self.assertRaises(ValueError):
                parse_model_plan(raw, "x")

    def test_invalid_title_and_timezone_fail(self):
        for g in [{**goal(), "title": "x:y"}, {**goal(), "title": "x\\y"},
                  {**goal(), "title": "x\n"}, {**goal(), "start": "2026-09-09T16:00:00"}]:
            with self.assertRaises(ValueError):
                validate_goal(g)

    def test_plan_mode_never_constructs_adb(self):
        with patch("router.cli.ADB") as adb, redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["plan", "打开关于手机页面"]), 0)
        adb.assert_not_called()


class RoutingTests(unittest.TestCase):
    def test_calendar_needs_live_qualification(self):
        self.assertEqual(route(goal(), STATE, 1)["status"], "WAITING")
        self.assertEqual(route(goal(), STATE, 1, calendar_qualified=True)["status"], "READY")
        self.assertEqual(route(goal(), STATE, 1, calibration=True)["status"], "READY")

    def test_unknown_or_changed_runtime_state_cannot_route(self):
        for changes in [{"connected": False}, {"default_ime": None}, {"keyboard_on_primary": False},
                        {"top_focused_display_id": 7}, {"android_user": 10}, {"writable_calendar_ids": []},
                        {"calendar_packages": []}, {"main_package": "com.hihonor.calendar"},
                        {"main_package": None}, {"virtual_displays": [7]}]:
            with self.subTest(changes=changes):
                self.assertEqual(route(goal(), {**STATE, **changes}, 1, True)["status"], "WAITING")

    def test_gui_is_same_device_and_no_type(self):
        g = {"kind": "gui.settings_about"}
        self.assertEqual(route(g, STATE)["channel"], "isolated_gui")
        for changes in [{"model": "other"}, {"sdk": "35"}, {"main_package": "com.android.settings"}, {"virtual_displays": [7]}]:
            with self.subTest(changes=changes):
                self.assertEqual(route(g, {**STATE, **changes})["status"], "WAITING")

    def test_multiple_calendars_need_explicit_choice(self):
        state = {**STATE, "writable_calendar_ids": [1, 2]}
        self.assertIsNone(cli.choose_calendar(state))
        self.assertEqual(cli.choose_calendar(state, 2), 2)


class DeviceGuardTests(unittest.TestCase):
    def test_numeric_parser_never_treats_errors_as_empty(self):
        self.assertEqual(numeric_rows("No result found.", ["_id"]), [])
        self.assertEqual(numeric_rows("Row: 0 _id=7", ["_id"]), [{"_id": 7}])
        for text in ["", "Error: permission", "Row: 0 _id=NULL", "Row: 0 _id=7, title=SECRET", "Row: 0 _id=7\nRow: 1 _id=7"]:
            with self.assertRaises(DeviceError):
                numeric_rows(text, ["_id"])

    def test_transport_quotes_remote_shell_and_detects_zero_exit_errors(self):
        adb = ADB("unit")
        with patch("router.device.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as run:
            adb.shell("content", "insert", "--bind", "title:s:$(touch nope)")
        self.assertIn("'title:s:$(touch nope)'", run.call_args.args[0][-1])
        with patch("router.device.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="Error while accessing provider")):
            with self.assertRaises(DeviceError):
                adb.shell("content", "query")

    def test_user_can_change_main_app_but_not_conflict_or_ime(self):
        adb = Mock()
        guard = MainGuard(adb, STATE["default_ime"], STATE["calendar_packages"])
        adb.state.return_value = {**STATE, "main_package": "com.browser"}
        guard.require()
        for changes in [{"default_ime": "other/IME"}, {"main_package": "com.hihonor.calendar"},
                        {"keyboard_on_primary": False}, {"main_package": None}, {"android_user": 1}]:
            adb.state.return_value = {**STATE, **changes}
            with self.assertRaises(DeviceError):
                guard.require()

    def test_monitor_does_not_hide_final_state_failure(self):
        guard = Mock()
        guard.require.side_effect = [STATE, DeviceError("focus changed")]
        with Monitor(guard) as monitor:
            pass
        self.assertEqual(monitor.errors, ["focus changed"])

    def test_device_lease_releases_after_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "stop"):
                with device_lease(directory, "unit"):
                    with self.assertRaises(DeviceError):
                        with device_lease(directory, "unit"):
                            pass
                    raise ValueError("stop")
            with device_lease(directory, "unit"):
                pass


class FakeProvider:
    """Model SQLite selections and ContentValues without invoking adb."""
    def __init__(self):
        self.serial = "unit-phone"
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE events (_id INTEGER PRIMARY KEY, calendar_id INTEGER, title TEXT, dtstart INTEGER, dtend INTEGER, eventTimezone TEXT, description TEXT, allDay INTEGER, hasAlarm INTEGER, hasAttendeeData INTEGER, deleted INTEGER DEFAULT 0)")
        self.inserts, self.fail_before, self.fail_after = 0, False, False
        self.writable = [1]

    def connect(self):
        return self

    def state(self):
        return copy.deepcopy(STATE)

    def probe(self):
        return copy.deepcopy(STATE)

    def calendars(self):
        return self.writable

    def shell(self, *args):
        if args[:2] == ("content", "query"):
            where = args[args.index("--where") + 1]
            rows = self.db.execute("SELECT _id FROM events WHERE " + where).fetchall()
            return "\n".join(f"Row: {i} _id={row[0]}" for i, row in enumerate(rows)) if rows else "No result found."
        if args[:2] != ("content", "insert"):
            raise AssertionError("Unexpected command")
        self.inserts += 1
        if self.fail_before:
            raise DeviceError("transport failed before write")
        fields = dict((value.split(":", 2)[0], value.split(":", 2)[2]) for i, value in enumerate(args) if i and args[i-1] == "--bind")
        self.db.execute("INSERT INTO events (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")", list(fields.values()))
        self.db.commit()
        if self.fail_after:
            raise DeviceError("transport timed out after write")
        return ""


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.adb = FakeProvider()
        self.addCleanup(self.adb.db.close)
        self.path = Path(self.directory.name) / "journal.sqlite3"
        self.tool = CalendarTool(self.adb, self.path)
        self.addCleanup(self.tool.close)

    def test_create_and_exact_readback(self):
        receipt = self.tool.create(goal(), 1, Mock())
        self.assertTrue(receipt["effect_verified"])
        self.assertFalse(receipt["reused"])
        self.assertEqual(self.adb.inserts, 1)
        self.assertEqual(self.adb.db.execute("SELECT title,hasAlarm,hasAttendeeData FROM events").fetchone(), ("项目讨论", 0, 0))

    def test_repeat_reuses_exact_event_without_writing(self):
        self.tool.create(goal(), 1, Mock())
        receipt = self.tool.create(goal(), 1, Mock())
        self.assertTrue(receipt["reused"])
        self.assertEqual(self.adb.inserts, 1)

    def test_timeout_after_success_is_resolved_by_readback(self):
        self.adb.fail_after = True
        receipt = self.tool.create(goal(), 1, Mock())
        self.assertTrue(receipt["effect_verified"])
        self.assertIsNotNone(receipt["transport_warning"])
        self.assertEqual(self.adb.inserts, 1)

    def test_unknown_write_does_not_retry_even_after_restart(self):
        self.adb.fail_before = True
        with self.assertRaises(DeviceError):
            self.tool.create(goal(), 1, Mock())
        self.adb.fail_before = False
        another = CalendarTool(self.adb, self.path)
        try:
            with self.assertRaisesRegex(DeviceError, "不自动重试"):
                another.create(goal(), 1, Mock())
        finally:
            another.close()
        self.assertEqual(self.adb.inserts, 1)

    def test_user_edit_or_delete_is_not_overwritten(self):
        self.tool.create(goal(), 1, Mock())
        self.adb.db.execute("UPDATE events SET title='user changed'")
        with self.assertRaises(DeviceError):
            self.tool.create(goal(), 1, Mock())
        self.adb.db.execute("DELETE FROM events")
        with self.assertRaises(DeviceError):
            self.tool.create(goal(), 1, Mock())
        self.assertEqual(self.adb.inserts, 1)

    def test_sql_and_shell_like_title_is_data(self):
        g = {**goal(), "title": "O'Reilly $(echo x) `cmd` ; DROP TABLE events --"}
        receipt = self.tool.create(g, 1, Mock())
        self.assertTrue(receipt["effect_verified"])
        self.assertEqual(self.adb.db.execute("SELECT title FROM events").fetchone()[0], g["title"])

    def test_guard_or_missing_calendar_blocks_write(self):
        with self.assertRaises(DeviceError):
            self.tool.create(goal(), 1, Mock(side_effect=DeviceError("focus")))
        self.adb.writable = []
        with self.assertRaises(DeviceError):
            self.tool.create(goal(), 1, Mock())
        self.assertEqual(self.adb.inserts, 0)

    def test_qualification_bound_to_device_build_calendar_and_code(self):
        root = self.directory.name
        self.assertFalse(qualified(root, STATE, 1))
        save_qualification(root, STATE, 1, "private-evidence.json")
        self.assertTrue(qualified(root, STATE, 1))
        self.assertFalse(qualified(root, {**STATE, "build_hash": "changed"}, 1))
        self.assertFalse(qualified(root, STATE, 2))
        with patch("router.calendar_tool.implementation_hash", return_value="changed"):
            self.assertFalse(qualified(root, STATE, 1))


class ExecutionTests(unittest.TestCase):
    def test_noninteractive_execution_never_connects(self):
        with patch("router.cli.sys.stdin.isatty", return_value=False), patch("router.cli.ADB") as adb:
            with self.assertRaises(ValueError):
                cli.execute(SimpleNamespace())
        adb.assert_not_called()

    def test_cancelled_calibration_never_writes(self):
        fake = Mock()
        fake.connect.return_value = fake
        fake.serial, fake.probe.return_value = "unit-phone", STATE
        with tempfile.TemporaryDirectory() as directory, patch("router.cli.ROOT", Path(directory)), \
             patch("router.cli.sys.stdin.isatty", return_value=True), patch("router.cli.ADB", return_value=fake), \
             patch("builtins.input", return_value="no"), patch("router.cli.run_calendar") as run, redirect_stdout(io.StringIO()):
            self.assertEqual(cli.execute(SimpleNamespace(serial=None, calendar_id=None), calibration=True), 2)
            run.assert_not_called()

    def test_unqualified_normal_run_waits_without_write(self):
        fake = Mock()
        fake.connect.return_value = fake
        fake.serial, fake.probe.return_value = "unit-phone", STATE
        args = SimpleNamespace(serial=None, calendar_id=None, planner="rules", task='明天下午4点安排30分钟的「项目讨论」日程')
        with tempfile.TemporaryDirectory() as directory, patch("router.cli.ROOT", Path(directory)), \
             patch("router.cli.sys.stdin.isatty", return_value=True), patch("router.cli.ADB", return_value=fake), \
             patch("router.cli.run_calendar") as run, patch("builtins.input") as ask, redirect_stdout(io.StringIO()):
            self.assertEqual(cli.execute(args), 2)
            run.assert_not_called()
            ask.assert_not_called()

    def test_calibration_end_to_end_requires_both_effect_and_human_isolation(self):
        for observation in ["0", "1", "2"]:
            with self.subTest(observation=observation), tempfile.TemporaryDirectory() as directory:
                fake = FakeProvider()
                try:
                    with patch("router.cli.ROOT", Path(directory)), patch("router.cli.sys.stdin.isatty", return_value=True), \
                         patch("router.cli.ADB", return_value=fake), patch("builtins.input", side_effect=["yes", observation]), \
                         patch("router.cli.countdown"), patch("router.cli.time.sleep"), redirect_stdout(io.StringIO()):
                        result = cli.execute(SimpleNamespace(serial=None, calendar_id=1), calibration=True)
                    self.assertEqual(result, 0 if observation == "0" else 1)
                    self.assertEqual(fake.inserts, 1)
                    reports = list((Path(directory) / "outputs").glob("router-*/result.json"))
                    report = json.loads(reports[0].read_text())
                    self.assertTrue(report["operations"][0]["effect_verified"])
                    self.assertTrue(report["operations"][0]["journal_write_started"])
                    self.assertEqual(qualified(directory, STATE, 1), observation == "0")
                finally:
                    fake.db.close()

    def test_runtime_change_after_approval_blocks_execution(self):
        fake = Mock()
        fake.connect.return_value = fake
        fake.serial = "unit-phone"
        fake.probe.side_effect = [STATE, {**STATE, "keyboard_on_primary": False}]
        with tempfile.TemporaryDirectory() as directory, patch("router.cli.ROOT", Path(directory)), \
             patch("router.cli.sys.stdin.isatty", return_value=True), patch("router.cli.ADB", return_value=fake), \
             patch("builtins.input", return_value="yes"), patch("router.cli.countdown"), \
             patch("router.cli.run_calendar") as run, redirect_stdout(io.StringIO()):
            self.assertEqual(cli.execute(SimpleNamespace(serial=None, calendar_id=1), calibration=True), 2)
            run.assert_not_called()

    def test_gui_wrapper_needs_human_observation_trace_and_cleanup(self):
        import run_autoglm_focus as driver
        good = {"top_focused_display_id": 0, "ime_state": ["mCurTokenDisplayId=0", "mInputShown=true"]}
        report = {"task_verified": True, "focus_trace": [good], "human_observation": "正常", "virtual_display_removed": True}
        with tempfile.TemporaryDirectory() as directory:
            class Base:
                def __init__(self, args):
                    self.report = copy.deepcopy(report)
                    self.output = Path(directory)
            for changed in [{}, {"human_observation": "未观察"}, {"focus_trace": []}, {"virtual_display_removed": False}]:
                def fake_execute(args):
                    instance = driver.AutoGLMSession(args)
                    instance.report.update(changed)
                    return 0
                with self.subTest(changed=changed), patch.object(driver, "AutoGLMSession", Base), \
                     patch.object(driver, "execute", side_effect=fake_execute), patch("router.gui_tool.MainGuard"):
                    receipt = execute_about(SimpleNamespace(serial="unit-phone"), "unit.ime/IME")
                    self.assertEqual(receipt["passed"], not changed)
                    self.assertIs(driver.AutoGLMSession, Base)


if __name__ == "__main__":
    unittest.main()
