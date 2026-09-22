"""Download the pinned pretrained NLI baseline for local CPU inference."""
import argparse
import json
from pathlib import Path
from gleipnir.pretrained import DEFAULT_DIRECTORY, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT_DIRECTORY)
    args = parser.parse_args()
    print(json.dumps(prepare(args.directory), indent=2))


if __name__ == '__main__':
    main()
