"""Loop 文本产物的脱敏写入与可信字节复制。"""

from pathlib import Path

from .redaction import redact_text


def write_text_artifact(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact_text(text), encoding="utf-8")


def copy_if_exists(source: Path, target: Path, *, preserve_bytes: bool = False) -> None:
    if source.exists():
        if preserve_bytes:
            content = source.read_bytes()
            text = content.decode("utf-8")
            # 已脱敏 Brief 按原始字节绑定；仍需脱敏时沿原路径写入并由完整性门禁拒绝。
            if redact_text(text) == text:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                return
        write_text_artifact(
            target,
            source.read_text(encoding="utf-8", errors="replace"),
        )
