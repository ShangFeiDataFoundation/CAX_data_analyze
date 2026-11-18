"""下载并校验外部样例数据。

  python 外部样例数据/fetch_samples.py            # 下载缺失文件，然后校验全部
  python 外部样例数据/fetch_samples.py --verify   # 只校验

清单见同目录 manifest.json；已存在且 sha256 一致的文件不会被重复下载。
"""

import hashlib
import json
import ssl
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))["files"]

# 本地网络环境存在自签证书链，样例数据为公开只读资源，此处不做证书校验。
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(item) -> bool:
    out = HERE / item["path"]
    if out.exists() and sha256(out) == item["sha256"]:
        print(f"SKIP {item['path']} (already OK)")
        return True
    out.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(item["url"], headers={"User-Agent": "Mozilla/5.0 (research)"})
    with urllib.request.urlopen(req, timeout=180, context=CTX) as r:
        out.write_bytes(r.read())
    print(f"GET  {item['path']} ({out.stat().st_size/1e6:.2f} MB)")
    return True


verify_only = "--verify" in sys.argv
ok = bad = 0
for item in MANIFEST:
    out = HERE / item["path"]
    if not verify_only:
        try:
            download(item)
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {item['path']} download: {type(e).__name__}: {e}")
            bad += 1
            continue
    if not out.exists():
        print(f"MISS {item['path']}")
        bad += 1
        continue
    got = sha256(out)
    if got == item["sha256"]:
        print(f"OK   {item['path']}")
        ok += 1
    else:
        print(f"BAD  {item['path']} sha256={got[:16]} expected={item['sha256'][:16]}")
        bad += 1

total = sum(p.stat().st_size for p in HERE.rglob("*") if p.is_file() and p.name != "README.md")
print(f"--- {ok} ok, {bad} bad; 目录当前 {total/1e6:.1f} MB ---")
