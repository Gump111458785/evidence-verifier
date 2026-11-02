"""Validate local documentation links without fetching remote sites."""
from pathlib import Path
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def slug(value):
    value = re.sub(r'[^\w\s-]', '', value.lower()).replace(' ', '-')
    return value


def check():
    files = [ROOT/'README.md', ROOT/'CHANGELOG.md', ROOT/'THIRD_PARTY_NOTICES.md']
    files += sorted((ROOT/'docs').glob('*.md'))
    files += [ROOT/'api_edition/README.md', ROOT/'app/README.md', ROOT/'app/extension/README.md', ROOT/'sdk/README.md']
    failures, links = [], 0
    for file in files:
        text = file.read_text()
        for raw in re.findall(r'\]\(([^)\s]+)\)', text):
            if re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', raw):
                continue
            path, _, anchor = unquote(raw).partition('#')
            target = (file.parent/path).resolve() if path else file
            links += 1
            if not target.is_relative_to(ROOT) or not target.exists():
                failures.append(f'{file.relative_to(ROOT)} -> {raw}')
            elif anchor and target.suffix == '.md':
                headings = {slug(h) for h in re.findall(r'^#{1,6}\s+(.+)$', target.read_text(), re.M)}
                if anchor not in headings:
                    failures.append(f'{file.relative_to(ROOT)} -> missing heading {raw}')
    if failures:
        raise ValueError('\n'.join(failures))
    print(f'Documentation: {len(files)} Markdown files, {links} local links checked.')


if __name__ == '__main__':
    check()
