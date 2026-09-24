"""大体积数据下载脚本（H2/H3：5GB 数据不进 git/镜像，清单 + SHA256 校验）。

当前（B1）不落地任何真实下载逻辑——数据集仍存放于旧仓库与外部冷存储，
本脚本提供清单框架与校验函数，供阶段 B6（CI 数据管线）与外部托管就绪后填充。

数据清单（provenance 见旧仓 fullflow-demo/data/scRNA_datasets/ 与
docs/specs/2026-08-13-bio-audit-full-audit-report.md C13）：
- GSE115978（Melanoma）：GSE115978_raw.h5ad / counts / annotations / summary
- GSE131907（NSCLC）：GSE131907_raw.h5ad / UMI matrix / annotations / summary
- GSE132465（CRC）：GSE132465_raw.h5ad / UMI matrix / annotations / summary
- CellVoyager 输出：fullflow-demo/data/cellvoyager_output/（D5 修复后轨迹已入库内）

使用（示例）：
  python scripts/download_datasets.py --check <dir>
"""

import argparse
import hashlib
from pathlib import Path

DATASETS = [
    # {name, url(待填), sha256(待填), note}
    {"name": "GSE115978_raw.h5ad", "url": None, "sha256": None,
     "note": "Melanoma; 见旧仓 fullflow-demo/data/scRNA_datasets/"},
    {"name": "GSE131907_raw.h5ad", "url": None, "sha256": None,
     "note": "NSCLC; 见旧仓 fullflow-demo/data/scRNA_datasets/"},
    {"name": "GSE132465_raw.h5ad", "url": None, "sha256": None,
     "note": "CRC; 见旧仓 fullflow-demo/data/scRNA_datasets/"},
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_dir(data_dir: Path) -> dict:
    """校验目录内数据集哈希（sha256 待填写后生效）。"""
    results = []
    for ds in DATASETS:
        p = data_dir / ds["name"]
        state = "missing"
        digest = None
        if p.exists():
            digest = sha256_file(p)
            state = "ok" if (ds["sha256"] is None or digest == ds["sha256"]) else "hash_mismatch"
        results.append({**ds, "state": state, "actual_sha256": digest})
    return {"datasets": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", default=None, help="校验本地数据目录")
    args = parser.parse_args()
    if args.check:
        import json
        print(json.dumps(check_dir(Path(args.check)), ensure_ascii=False, indent=1))
    else:
        print("B1 阶段：下载逻辑待外部托管就绪后填充（H2/H3）。")
        print("数据仍位于旧仓库 fullflow-demo/data/（h5ad 不进 git）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
