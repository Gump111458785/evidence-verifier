"""Explicit API retrieval injection; no process-wide SearchSessions monkey patch."""
import os


def configure_environment(provider, gateway_url, gateway_token):
    prefixes = ('EV_MODEL_', 'EV_VERIFIER_', 'EV_AGENT_', 'EV_PLANNER_', 'EV_INFERENCE_', 'EV_ROUTING_', 'EV_REPLICA_', 'EV_GROUNDING_')
    for key in list(os.environ):
        if key.startswith(prefixes) or key == 'EV_EXTENSION_ID':
            os.environ.pop(key)
    os.environ['EV_EDITION'] = 'pure_api'
    os.environ['EV_GROUNDING_REPAIR_ATTEMPTS'] = str(provider.config.get('grounding_repair_attempts', 0))
    if provider.ready('extractor') and provider.ready('verifier'):
        os.environ.update(EV_MODEL_URL=gateway_url, EV_MODEL_NAME='api-verifier', EV_MODEL_KEY=gateway_token)
    for role, prefix in (('planner', 'EV_PLANNER'), ('agent', 'EV_AGENT')):
        if provider.ready(role):
            os.environ.update({prefix + '_URL': gateway_url, prefix + '_NAME': 'api-' + role, prefix + '_KEY': gateway_token})
    if provider.config.get('extension_id'):
        os.environ['EV_EXTENSION_ID'] = provider.config['extension_id']


def search_sessions(provider, connections, corpus):
    from retrieval import SearchSessions, RetrievalError

    class APISearchSessions(SearchSessions):
        def _search(self, query, required_entity=None):
            try:
                return provider.search(query, required_entity)
            except (ValueError, RuntimeError) as exc:
                raise RetrievalError('search_api_failure', str(exc)) from None

        def corpus_identity(self):
            return provider.search_identity()

    return APISearchSessions(connections, corpus)
