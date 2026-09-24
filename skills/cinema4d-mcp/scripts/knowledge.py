"""Read the canonical C4D knowledge archive without contacting Cinema 4D."""
import argparse
import json
import os
from pathlib import Path
import re


def resolve_repo(explicit=None):
    if explicit:
        candidates = [Path(explicit)]
    elif os.environ.get('C4D_MCP_REPO'):
        candidates = [Path(os.environ['C4D_MCP_REPO'])]
    else:
        candidates = [Path.cwd(), *Path.cwd().parents,
                      *Path(__file__).resolve().parents,
                      Path.home() / 'Projects' / 'cinema4d-mcp']
    for candidate in candidates:
        if (candidate / 'docs' / 'c4d_2026_api_gotchas.md').is_file():
            return candidate.resolve()
    raise ValueError('Knowledge checkout not found; pass --repo or set C4D_MCP_REPO.')


def entries(text):
    headings = list(re.finditer(r'^## (.+)$', text, re.MULTILINE))
    found = {}
    for index, heading in enumerate(headings):
        number = re.match(r'(\d+)\.\s', heading.group(1))
        if not number:
            continue
        key = int(number.group(1))
        if key in found:
            raise ValueError('Duplicate gotcha ID: %s' % key)
        end = headings[index+1].start() if index+1 < len(headings) else len(text)
        found[key] = (heading.group(1), text[heading.start():end].rstrip())
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', help='Explicit cinema4d-mcp checkout')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--list', action='store_true')
    group.add_argument('--id', nargs='+', type=int, help='Print complete entries')
    group.add_argument('--search', help='Search bodies/titles; return matching titles')
    group.add_argument('--catalog', action='store_true')
    args = parser.parse_args()
    try:
        repo = resolve_repo(args.repo)
        if args.catalog:
            value = json.loads((repo / 'data' / 'knowledge_manifest.json').read_text(encoding='utf-8'))
            print(json.dumps(value, indent=2, ensure_ascii=False))
            return
        path = repo / 'docs' / 'c4d_2026_api_gotchas.md'
        index = entries(path.read_text(encoding='utf-8'))
        print('Source: %s\n' % path)
        if args.id:
            missing = [n for n in args.id if n not in index]
            if missing:
                raise ValueError('Unknown gotcha IDs: %s' % missing)
            print('\n\n'.join(index[n][1] for n in args.id))
        else:
            query = args.search.casefold() if args.search else None
            for key in sorted(index):
                title, body = index[key]
                if query is None or query in body.casefold():
                    print(title)
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
