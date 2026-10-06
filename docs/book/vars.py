#!/usr/bin/env python3
"""mdbook preprocessor: expands {{repo}} in chapter content to the repo slug
(owner/name), derived from output.html.git-repository-url — book.toml is the
single place the repository is named. (theme/reps-commands.js necessarily
repeats it as its GITHUB/TARBALL constants; change both.)"""
import json
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "supports":
        sys.exit(0)
    ctx, book = json.load(sys.stdin)
    url = ctx["config"]["output"]["html"]["git-repository-url"]
    repo = "/".join(url.rstrip("/").split("/")[-2:])

    def walk(items):
        for item in items:
            chapter = item.get("Chapter")
            if not chapter:
                continue
            chapter["content"] = chapter["content"].replace("{{repo}}", repo)
            walk(chapter.get("sub_items", []))

    # mdbook ≤0.4 says "sections", 0.5 says "items"
    walk(book.get("items") or book.get("sections") or [])
    json.dump(book, sys.stdout)


main()
