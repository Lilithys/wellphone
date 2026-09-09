#!/usr/bin/env python3
"""Audio-only qualification: no model, no messaging, no GUI input actions."""
from __future__ import annotations

import argparse
import os
import signal
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from douyin_audio import AudioLease, durable_json, player_states, restore_safely
from douyin_state import experiment_lease
from router.device import ADB, Monitor
from run_douyin_test import (ROOT, DouyinSession, PinnedMain,
                             countdown, interrupt_on_signal, verify_source)


def permission_cycle(adb, approved=False):
    output_root = ROOT / "outputs"
    output_root.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="audio-cycle-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-", dir=output_root))
    report = {"mode": "permission_cycle_only", "model_called": False, "app_launched": False,
              "messages_sent": False, "audio_isolation_qualified": False}
    lease, prepared = AudioLease(adb, ROOT), False
    print("权限回环检查：不启动抖音、不播放声音、不创建副屏；临时设 ignore，立即恢复原值。", flush=True)
    try:
        with experiment_lease(ROOT, adb.serial):
            if not approved and input("同意只修改并恢复抖音两项音频权限？yes：").strip() != "yes":
                raise RuntimeError("已取消，无权限写入。")
            try:
                lease.prepare()
                prepared = True
                lease.apply()
                lease.require_restricted()
                report["restriction_readback_verified"] = True
            finally:
                if prepared:
                    old_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
                    old_term = signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    try:
                        restore_safely(lease, report)
                    finally:
                        signal.signal(signal.SIGINT, old_int)
                        signal.signal(signal.SIGTERM, old_term)
    except (EOFError, KeyboardInterrupt):
        report["interrupted"] = True
    except Exception as exc:
        report["error"] = str(exc)
        print(str(exc), flush=True)
    passed = bool(report.get("restriction_readback_verified") and report.get("permissions_restored")
                  and not report.get("error") and not report.get("interrupted"))
    report["passed"] = passed
    durable_json(output / "result.json", report)
    print("权限设置和恢复回读通过；尚未验证实际静音或焦点隔离。" if passed else "权限回环未通过，不启动抖音。")
    print(f"记录：{output / 'result.json'}", flush=True)
    return 0 if passed else 1


def run_phone_probe(args, adb):
    session, lease = DouyinSession(args), AudioLease(adb, ROOT)
    monitor, prepared = None, False
    report = session.report
    report.update(mode="douyin_audio_qualification", recipient=None, model_called=False,
                  text_input_enabled=False, send_attempted=False, gui_input_actions=0,
                  automatic_navigation=False, audio_samples=[], permissions_restored=False,
                  verification="appops_readback_and_human_audio_observation")
    try:
        session.prepare()
        print("只测试抖音音频：临时限制 PLAY_AUDIO 和 TAKE_AUDIO_FOCUS，结束恢复原值。")
        print("不调用模型、不发消息、不输入/点击/滑动；不改全局音量，不采集或转发任何音频。")
        print("副屏图像只保存在本机 outputs。测试会影响整个抖音应用的声音，不是按显示器隔离。")
        print("建议你先播放一段背景音乐，再进入主屏短信草稿并保持键盘，用来观察音乐是否被暂停/压低。")
        print("抖音可能拒绝播放或限制可能无效；异常立即 Ctrl+C。退出先关闭副屏，确认抖音不再播放后才解禁。")
        if input("同意并准备好后输入 yes；随后有 5 秒回到手机持续打字：").strip() != "yes":
            raise RuntimeError("已取消，无权限写入或应用启动。")
        countdown()
        guard = PinnedMain(adb, adb.state())
        guard.require()
        with experiment_lease(ROOT, adb.serial):
            monitor = Monitor(guard)
            session.monitor = monitor
            monitor.__enter__()
            try:
                lease.prepare()
                prepared = True
                report["audio_original"] = lease.data["original"]
                session.save()
                lease.apply()
                lease.require_restricted()
                report["restriction_readback_verified"] = True
                session.audio_lease = lease
                session.launch()
                report["launch_verified"] = True
                print(f"副屏已启动，观察 {args.seconds} 秒；不要在副屏点击，不要在主屏打开抖音。", flush=True)
                deadline = time.monotonic() + args.seconds
                while time.monotonic() < deadline:
                    session.scope()
                    lease.require_restricted()
                    report["audio_samples"].append({"time": datetime.now().isoformat(),
                        "players": player_states(adb.shell("dumpsys", "audio"), lease.data["uid"])})
                    session.save()
                    time.sleep(.5)
                session.capture("audio-observation-end.png")
                report["observation_completed"] = True
            finally:
                # Ordinary interruption must not interrupt recovery a second time.
                old_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
                old_term = signal.signal(signal.SIGTERM, signal.SIG_IGN)
                try:
                    try:
                        session.stop_display()
                    except Exception as exc:
                        report["cleanup_error"] = str(exc)
                    if session.frames:
                        try:
                            session.frames.stop()
                        except Exception as exc:
                            report["decoder_cleanup_error"] = str(exc)
                    if prepared:
                        restore_safely(lease, report)
                    monitor.__exit__(None, None, None)
                finally:
                    signal.signal(signal.SIGINT, old_int)
                    signal.signal(signal.SIGTERM, old_term)
    except (EOFError, KeyboardInterrupt):
        report["interrupted"] = True
        print("已中断音频专项；请留意上方恢复结果。")
    except Exception as exc:
        report["error"] = str(exc)
        print("音频测试停止：" + str(exc), flush=True)
    finally:
        if monitor:
            report.update(primary_samples=monitor.samples, primary_errors=monitor.errors)
        report["finished_at"] = datetime.now().isoformat()
        session.save()
        (session.output / "scrcpy.log").write_text("\n".join(session.scrcpy_log), encoding="utf-8")
    if report.get("launch_verified"):
        try:
            report["video_observation"] = input("副屏期间确实有视频连续播放（不是静止消息页/暂停）？是 / 否 / 未看清：").strip()
            report["douyin_sound"] = input("手机或耳机是否听到抖音声音？无 / 有 / 未观察：").strip()
            report["background_music"] = input("原有背景音乐是否保持正常、不暂停也不压低？正常 / 异常 / 未测试：").strip()
            report["keyboard_observation"] = input("主屏打字：正常 / 白屏 / 收起 / 丢字 / 未观察：").strip()
        except (EOFError, KeyboardInterrupt):
            pass
    exercised = any(p["state"] == "started" for s in report["audio_samples"] for p in s["players"])
    infrastructure = bool(report.get("observation_completed") and report.get("restriction_readback_verified")
                          and report.get("permissions_restored") and report.get("virtual_display_removed")
                          and monitor and monitor.samples and not monitor.errors
                          and not any(report.get(k) for k in ("error", "interrupted", "cleanup_error", "decoder_cleanup_error")))
    muted = bool(infrastructure and exercised and report.get("video_observation") == "是"
                 and report.get("douyin_sound") == "无" and report.get("keyboard_observation") == "正常")
    full = muted and report.get("background_music") == "正常"
    report.update(playback_exercised=exercised, playback_muting_observed=muted,
                  focus_continuity_human_confirmed=report.get("background_music") == "正常",
                  audio_isolation_qualified=full, passed=full)
    session.save()
    if full:
        print("本轮音频、背景音乐连续性和键盘观察通过；这是应用级限制，不是通用副屏音频隔离。")
    elif muted:
        print("静音和键盘观察通过；背景音乐连续性未通过/未测试，不能判定完整音频隔离通过。")
    else:
        print("音频专项未通过或证据不足（若没有实际播放音轨，安静不能证明静音有效）。")
    print(f"记录：{session.output / 'result.json'}")
    return 0 if full else 1


def main():
    parser = argparse.ArgumentParser(description="抖音应用级音频专项；不调用模型、不发消息、不改全局音量")
    parser.add_argument("--serial")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--cycle", action="store_true", help="只检查权限设置与恢复，不启动抖音")
    modes.add_argument("--restore", action="store_true", help="只恢复已记录的两项原值，不启动应用")
    parser.add_argument("--yes", action="store_true", help="仅用于已获批准的 --cycle")
    parser.add_argument("--seconds", type=int, default=15)
    args = parser.parse_args()
    if args.yes and not args.cycle:
        parser.error("--yes 仅允许与 --cycle 一起使用。")
    if not 5 <= args.seconds <= 30:
        parser.error("观察时长仅允许 5–30 秒。")
    if not sys.stdin.isatty() and not (args.restore or args.cycle and args.yes):
        parser.error("完整测试需要在 Terminal 交互运行，尚未操作手机。")
    verify_source()
    os.umask(0o077)
    os.environ.pop("PHONE_AGENT_API_KEY", None)
    os.environ.pop("DEEPSEEK_API_KEY", None)
    os.environ.pop("WELLPHONE_PLANNER_API_KEY", None)
    signal.signal(signal.SIGTERM, interrupt_on_signal)
    adb = ADB(args.serial).connect()
    if args.restore:
        with experiment_lease(ROOT, adb.serial):
            lease = AudioLease(adb, ROOT)
            lease.restore()
            print("抖音两项音频设置已回读确认恢复原值。记录：" + str(lease.path))
        return 0
    if args.cycle:
        return permission_cycle(adb, args.yes)
    args.serial, args.preflight, args.step_by_step = adb.serial, True, False
    args.server_path = str(ROOT / "work/focus_experiment/scrcpy-server-focus")
    args.client_path = str(ROOT / "work/frame_stream/scrcpy-live")
    args.require_focus_flags, args.trace_focus, args.live_mkv = True, True, True
    args.ime_policy, args.no_system_decorations = "local", False
    args.output_prefix, args.window_title = "douyin-audio", "Wellphone-Audio-Probe"
    return run_phone_probe(args, adb)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError) as exc:
        raise SystemExit(str(exc))
