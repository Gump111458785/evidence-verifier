"""Read metadata by default. --execute explicitly submits a teaching input."""
import argparse
import json
from evidence_verifier import VerifierClient


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:18790')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    client = VerifierClient(args.url)
    if not args.execute:
        print(json.dumps(client.health(), ensure_ascii=False, indent=2))
    else:
        evidence = [{'id': 'E1', 'text': 'The Meridian Engine was designed by Nora Hale.'},
                    {'id': 'E2', 'text': 'Nora Hale attended Lake University as a student.'}]
        plan = [{'subject': 'Meridian Engine', 'predicate': 'designed_by', 'object': '?designer'},
                {'subject': '?designer', 'predicate': 'educated_at', 'object': 'Lake University'}]
        print(json.dumps(client.verify(evidence, plan), ensure_ascii=False, indent=2))
