"""Start Photo Backup and open it in the browser."""

import argparse
import threading
import webbrowser

from photobackup.app import create_app


def main():
    parser = argparse.ArgumentParser(description="Photo Backup - local photo/video sorter")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    print(f"Photo Backup is running at {url}  (close this window to stop)")
    create_app().run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
