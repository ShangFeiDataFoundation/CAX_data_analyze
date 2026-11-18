"""Command-line entry point for the local CAX workspace prototype."""

from __future__ import annotations

import argparse
import threading
import webbrowser


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CAX 工作区扫描与视觉检索原型")
    parser.add_argument("--serve", action="store_true", help="启动本地 Web 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8200)
    parser.add_argument("--runs-root", default="data/frontend_runs")
    parser.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args(argv)
    if args.serve:
        import uvicorn

        from .api import create_app

        if not args.no_open:
            open_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
            url = f"http://{open_host}:{args.port}"
            threading.Timer(1.2, lambda: webbrowser.open(url)).start()
        uvicorn.run(
            create_app(runs_root=args.runs_root),
            host=args.host,
            port=args.port,
        )
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
