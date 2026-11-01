"""Explicit opt-in graph example. No model or graph runs without --execute."""
import argparse
import json
from evidence_verifier import VerifierClient
from evidence_verifier.langgraph import build_verification_graph, new_verification_input


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:18790')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print('Prepared example only. Supply existing LangGraph and a configured verifier, then opt in with --execute.')
    else:
        from langgraph.checkpoint.memory import InMemorySaver
        client = VerifierClient(args.url)
        graph = build_verification_graph(client, checkpointer=InMemorySaver(), timeout=30)
        evidence = [{'id': 'E1', 'text': 'Nora Hale attended Lake University as a student.'}]
        relations = [{'subject': 'Nora Hale', 'predicate': 'educated_at', 'object': 'Lake University'}]
        state = new_verification_input(evidence, relations)
        config = {'configurable': {'thread_id': 'verifier-example'}}
        result = graph.invoke(state, config)
        # If pending, retain result and invoke again later with the same state/config.
        print(json.dumps(result, ensure_ascii=False, indent=2))
