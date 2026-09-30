"""读取项目声明的规范包版本，供CI选择确定的制品及核对安装版本。"""

import re
import tomllib
from pathlib import Path


def project_version(path: Path = Path("pyproject.toml")) -> str:
    version = tomllib.loads(path.read_text(encoding="utf-8"))["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?", version
    ):
        raise ValueError("项目version必须是规范的三段式包版本（可含预发布/post/dev后缀）。")
    return version


if __name__ == "__main__":
    print(project_version())
