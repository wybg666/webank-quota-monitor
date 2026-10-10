from __future__ import annotations
import argparse
import json
import sys
import webbrowser
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="微众银行产品额度监控 · Python 独立版（非官方）")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--allow-notifications", action="store_true", help="允许已启用的语音、邮件、Bark通知；演示模式仍禁止")
    parser.add_argument("--demo", action="store_true", help="明确标记的离线示例，不会连接业务接口或发送通知")
    parser.add_argument("--probe", action="store_true", help="只查询一次并打印结果，不启动页面或发送通知")
    args = parser.parse_args()
    if sys.version_info < (3, 11): parser.error("需要 Python 3.11+")
    if not 0 <= args.port <= 65535: parser.error("端口须在 0–65535 之间")
    from webank_monitor.client import WebankClient, ClientError
    from webank_monitor.demo import DemoClient
    client = DemoClient() if args.demo else WebankClient(timeout=10)
    if args.probe:
        try: print(json.dumps(client.fetch(), ensure_ascii=False, indent=2, allow_nan=False)); return 0
        except ClientError as exc: print(str(exc), file=sys.stderr); return 1
    from webank_monitor.config import data_directory
    from webank_monitor.engine import Monitor
    from webank_monitor.web import Server
    data = data_directory(args.demo)
    try: monitor = Monitor(client, data, allow_external=args.allow_notifications, demo=args.demo)
    except ValueError as exc: print(str(exc), file=sys.stderr); return 1
    try: server = Server(args.port, monitor)
    except OSError: print("端口无法使用，请通过 --port 更换；未停止其他程序。", file=sys.stderr); return 1
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"微众银行额度监控：{url}", flush=True)
    print(f"运行目录：{data}", flush=True)
    print("数据模式：" + ("离线示例（非真实）" if args.demo else "真实公开接口；由页面手动开始查询"), flush=True)
    print("通知外发：" + ("允许（仍需配置并开启具体渠道）" if monitor.allow_external else "禁止"), flush=True)
    print("保持窗口打开，Ctrl+C 停止。", flush=True)
    if args.open_browser:
        try: webbrowser.open(url)
        except Exception: pass
    try: server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt: pass
    finally: monitor.shutdown(); server.server_close()
    return 0

if __name__ == "__main__": raise SystemExit(main())
