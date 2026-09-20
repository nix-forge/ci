"""Rewrite repository-source links for the published documentation artifact."""

import re
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, unquote, urlsplit, urlunsplit

ATTRIBUTE = re.compile(r'(?P<prefix>\b(?:href|src)=")(?P<url>[^"]+)')


class _SourceFile(Protocol):
    abs_src_path: str


class _Page(Protocol):
    file: _SourceFile


def on_page_content(
    html: str, page: _Page, config: dict[str, str], **_kwargs: object
) -> str:
    """Point links outside docs_dir at the repository's GitHub source.

    Returns:
        Rendered HTML with out-of-doc links rewritten.

    """
    docs_dir = Path(config["docs_dir"]).resolve()
    repository = config["repo_url"].rstrip("/")

    def replace(match: re.Match[str]) -> str:
        value = match.group("url")
        parts = urlsplit(value)
        if parts.scheme or parts.netloc or parts.path.startswith(("/", "#")):
            return match.group(0)

        target = (Path(page.file.abs_src_path).parent / unquote(parts.path)).resolve()
        try:
            relative = target.relative_to(docs_dir)
        except ValueError:
            try:
                relative = target.relative_to(docs_dir.parent)
            except ValueError:
                return match.group(0)
            kind = "raw" if match.group("prefix").startswith("src=") else "blob"
            rewritten = f"{repository}/{kind}/main/{quote(relative.as_posix())}"
            value = urlunsplit(("", "", rewritten, parts.query, parts.fragment))

        return f"{match.group('prefix')}{value}"

    return ATTRIBUTE.sub(replace, html)
