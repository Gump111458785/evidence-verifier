"""Bounded source-grounding revisions; never receives a verification target."""
import copy
import re

REPAIR_SYSTEM = '''Repair source grounding of an existing list of fact hypotheses.
You have only source evidence, predicate definitions, the hypotheses, and exact
program validation errors. You have no final question, answer or target relation.
Return JSON with the SAME number of facts in the SAME order. Facts without an
error must remain byte-for-byte equivalent JSON objects. For an erroneous fact,
only correct quote_ids, subject surface text, and object surface/operand text.
Do NOT change predicate, polarity, qualifier or object.kind. Do NOT add or remove
facts. Do NOT turn an unrelated hypothesis into another relationship.
Every endpoint and arithmetic operand must occur VERBATIM in that fact's quotes.
If an original sentence uses a pronoun/surname, cite its introducing sentence
from the SAME document as well; do not borrow identity from another document.
You may not add a source document absent from that fact's original quote_ids.
These are hypotheses, not verified facts: do not claim a relation is supported
just because its strings can be located. Semantic support is checked separately.
If grounding cannot be repaired under these rules, leave the hypothesis unchanged;
the program will retain its failure. Return only {"facts":[...]}.
'''


def source_payload(evidence, kernel):
    """Join artificial abbreviation splits without changing any source characters.

    The archived segmenter splits 'Engine No. 2' after 'No.'. This live-only
    catalogue retains exact offsets and joins only adjacent spans in one line.
    """
    payload, old = kernel.extraction_payload(evidence)
    sources = {e['id']: e['text'] for e in evidence}
    items, cat = [], {}
    for doc in payload['evidence']:
        spans = []
        for segment in doc['segments']:
            current = dict(old[segment['quote_id']])
            if spans:
                previous = spans[-1]
                gap = sources[doc['id']][previous['end']:current['start']]
                if re.search(r'\b(?:No|Dr|Mr|Mrs|Ms|Prof|Sr|Jr|St)\.$', previous['text']) and not gap.strip() and '\n' not in gap:
                    previous['end'] = current['end']
                    previous['text'] = sources[doc['id']][previous['start']:previous['end']]
                    continue
            spans.append(current)
        segments = []
        for span in spans:
            qid = 'Q' + str(len(cat) + 1)
            cat[qid] = span
            segments.append({'quote_id': qid, 'text': span['text']})
        items.append({'id': doc['id'], 'segments': segments})
    return {'predicate_definitions': kernel.CONTRACT, 'evidence': items}, cat


def materialize_all(facts, cat, kernel):
    import jsonschema
    rows, errors = {}, []
    for index, fact in enumerate(facts):
        try:
            rows[index] = kernel.materialize_fact(fact, cat)
        except (ValueError, KeyError, TypeError, jsonschema.ValidationError) as exc:
            errors.append({'index': index, 'error': str(exc)[:1000]})
    return rows, errors


def expand_local_context(facts, errors, payload, cat, neighbors=2, max_extra_chars=600):
    """Locate missing literal text nearby, including every intervening sentence.

    This adds source context, not semantic support. Never cross documents, skip
    intervening text, shorten an entity, or silently trim an oversized expansion.
    """
    revised, audit = copy.deepcopy(facts), []
    documents = {d['id']: [s['quote_id'] for s in d['segments']] for d in payload['evidence']}
    positions = {q: (eid, i) for eid, ids in documents.items() for i, q in enumerate(ids)}
    for error in errors:
        index = error['index'];fact = revised[index];original = fact['quote_ids']
        literals = [fact['subject']] + [v for k, v in fact['object'].items() if k != 'kind']
        missing = [v for v in literals if not any(v in cat[q]['text'] for q in original)]
        additions = set()
        for anchor in original:
            eid, center = positions[anchor];ids = documents[eid]
            for i in range(max(0, center-neighbors), min(len(ids), center+neighbors+1)):
                if any(value in cat[ids[i]]['text'] for value in missing):
                    additions.update(ids[min(i,center):max(i,center)+1])
        additions.difference_update(original)
        if not additions:
            continue
        ordered = [q for q in cat if q in additions]
        chars = sum(len(cat[q]['text']) for q in ordered)
        if chars > max_extra_chars:
            audit.append({'index': index, 'applied': False, 'reason': 'adjacent_context_limit', 'extra_chars': chars})
            continue
        fact['quote_ids'] = [q for q in cat if q in set(original) | additions]
        audit.append({'index': index, 'applied': True, 'added_quote_ids': ordered,
                      'extra_chars': chars, 'semantic_support': 'not_established'})
    return revised, audit


def revision_schema(cat, count, kernel):
    return kernel.obj({'facts': {'type': 'array', 'minItems': count, 'maxItems': count,
                                 'items': kernel.fact_schema(cat)}})


def apply_revision(original, revised, errors, cat):
    """Constrain the edit before re-running the unchanged source validator."""
    if len(original) != len(revised):
        raise ValueError('Grounding revision may not add or remove facts')
    editable = {e['index'] for e in errors}
    for index, (before, after) in enumerate(zip(original, revised)):
        if index not in editable:
            if before != after:
                raise ValueError('Grounding revision changed an already grounded fact')
            continue
        for field in ('predicate', 'polarity', 'qualifier'):
            if before[field] != after[field]:
                raise ValueError('Grounding revision changed a fixed semantic field: ' + field)
        if before['object']['kind'] != after['object']['kind']:
            raise ValueError('Grounding revision changed the endpoint kind')
        allowed = {cat[q]['evidence_id'] for q in before['quote_ids']}
        if any(cat[q]['evidence_id'] not in allowed for q in after['quote_ids']):
            raise ValueError('Grounding revision borrowed a different source document')
    return copy.deepcopy(revised)


def cited_evidence(payload, quote_ids):
    """A support verifier gets exactly the cited sentences, not other sources."""
    selected, wanted = [], set(quote_ids)
    for doc in payload['evidence']:
        segments = [copy.deepcopy(s) for s in doc['segments'] if s['quote_id'] in wanted]
        if segments:
            selected.append({'id': doc['id'], 'segments': segments})
    return selected
