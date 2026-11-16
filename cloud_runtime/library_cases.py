"""Public case library. Every case here is written by the project team.

These are demonstrations with preset results, not live runs: `source` is
'demo', evidence is a preset excerpt marked as such, and no URL is attached
to demo evidence. User records never enter this library.
"""
from .assist import prompts

DEMO_SOURCE = '演示用证据（预设摘录，非实时检索）'


def _quote(text):
    return {'evidence_id': 'D1', 'text': text, 'title': DEMO_SOURCE, 'url': None, 'offset': 0}


def _mode(needed, reason, **extra):
    return {'needed': needed, 'reason': reason, **extra}


def _case(ident, title, tags, question, answer, correct, supplement, predict, revised=None, changes=None, evidence=None):
    # Spans and quotes are recomputed and replayed, never trusted as typed.
    texts = ' '.join(e['text'] for e in evidence or [])
    for claim in correct.get('claims', []):
        pos = answer.find(claim['text'])
        if pos < 0:
            raise ValueError('Demo claim is not a literal span of its answer: ' + ident)
        claim['span'] = [pos, pos + len(claim['text'])]
        if claim.get('quote') and claim['quote']['text'] not in texts:
            raise ValueError('Demo quote does not replay against demo evidence: ' + ident)
    result = {'status': 'ok', 'source': 'demo', 'demo': True, 'scope': 'open_claims_v1',
              'modes': {'correct': correct, 'supplement': supplement, 'predict': predict},
              'answer': ({'status': 'ok', 'revised_answer': revised, 'changes': changes or []} if revised else None),
              'usage': {'model_calls': 0, 'search_calls': 0}}
    result['prompts'] = prompts(question, result)
    return {'id': ident, 'title': title, 'tags': tags, 'question': question, 'answer': answer,
            'evidence': evidence or [], 'source': 'demo', 'label': '演示：预设结果', 'result': result}


EIFFEL_EVIDENCE = ("The tower was completed in March 1889 and served as the entrance arch to the 1889 World's Fair, "
                   "held to mark the centennial of the French Revolution.")
PHOTO_EVIDENCE = 'In plants, photosynthesis takes place in chloroplasts, which contain the pigment chlorophyll.'

CASES = [
    _case(
        'eiffel-year', '建成年份写错了', ['correct', 'predict'],
        '埃菲尔铁塔是哪一年建成的？为什么要建？',
        '埃菲尔铁塔于1899年建成，是为庆祝法国大革命一百周年而举办的巴黎世界博览会修建的入口拱门。',
        _mode(True, '回答包含年份与建造缘由两条可核实的断言', verdict='errors_found', claims=[
            {'id': 'C1', 'text': '埃菲尔铁塔于1899年建成', 'span': [0, 11], 'tier': 'open_claim',
             'verdict': 'contradicted', 'explanation': '证据说明铁塔于1889年3月完工，不是1899年。',
             'quote': _quote('The tower was completed in March 1889'), 'correction': '埃菲尔铁塔于1889年建成（1889年3月完工）。'},
            {'id': 'C2', 'text': '为庆祝法国大革命一百周年而举办的巴黎世界博览会修建的入口拱门', 'span': [12, 42], 'tier': 'open_claim',
             'verdict': 'supported', 'explanation': '证据说明它是为纪念法国大革命一百周年的世界博览会修建的入口拱门。',
             'quote': _quote("served as the entrance arch to the 1889 World's Fair, held to mark the centennial of the French Revolution"),
             'correction': None}]),
        _mode(False, '对“哪一年、为什么”两问都已作答', points=[]),
        _mode(True, '用户可能继续了解铁塔的后续历史', questions=[
            {'id': 'Q1', 'question': '埃菲尔铁塔建成后为什么差点被拆除？'},
            {'id': 'Q2', 'question': '埃菲尔铁塔建造用了多长时间？'}]),
        revised='埃菲尔铁塔于1889年建成（1889年3月完工），是为庆祝法国大革命一百周年而举办的巴黎世界博览会修建的入口拱门。',
        changes=[{'finding_ids': ['C1'], 'summary': '将建成年份由1899年改为1889年'}],
        evidence=[{'id': 'D1', 'title': DEMO_SOURCE, 'text': EIFFEL_EVIDENCE}]),
    _case(
        'analytical-engine', '证据不足，不等于回答错了', ['correct', 'predict'],
        'Who designed the Analytical Engine?',
        'The Analytical Engine was designed by Charles Babbage.',
        _mode(True, 'The answer names a designer, which can be checked', verdict='unverified', claims=[
            {'id': 'C1', 'text': 'The Analytical Engine was designed by Charles Babbage', 'span': [0, 53], 'tier': 'open_claim',
             'verdict': 'insufficient', 'explanation': '提供的来源只说明机器没有完成，没有说明设计者；结论保留为“未能核实”。',
             'quote': None, 'correction': None}]),
        _mode(False, 'The question asks only for the designer', points=[]),
        _mode(True, 'Users often ask about the people around Babbage next', questions=[
            {'id': 'Q1', 'question': "What was Ada Lovelace's role in the Analytical Engine?"},
            {'id': 'Q2', 'question': 'Why was the Analytical Engine never completed?'}]),
        evidence=[{'id': 'D1', 'title': DEMO_SOURCE, 'text': 'The Analytical Engine was never completed.'}]),
    _case(
        'rag-rollout', '讲了流程，漏了落地要点', ['supplement', 'predict'],
        '企业知识库问答怎么落地？我们要评估实施方案。',
        '先把企业文档切分并建立向量索引，用户提问时检索最相关的片段，再交给大模型生成答案。可以选用开源向量数据库和商用大模型 API。',
        _mode(False, '回答是方法介绍，没有需要核实的具体事实', verdict='not_run', claims=[]),
        _mode(True, '用户要评估实施方案，回答缺少影响上线的关键环节', points=[
            {'id': 'M1', 'point': '权限控制', 'why': '不同员工可见的文档不同，检索必须按权限过滤', 'kind': 'explanatory',
             'content': '检索阶段按用户身份过滤文档，避免把无权查看的内容放进答案；索引时同步保存每段文档的权限标签。',
             'source_status': 'not_required', 'quote': None},
            {'id': 'M2', 'point': '文档更新与索引同步', 'why': '文档频繁变动，索引过期会给出旧答案', 'kind': 'explanatory',
             'content': '为文档变更建立增量更新流程，记录每段内容的版本与更新时间，定期清理已删除文档的索引。',
             'source_status': 'not_required', 'quote': None},
            {'id': 'M3', 'point': '引用回溯与效果评测', 'why': '评估方案需要能衡量答案质量和成本', 'kind': 'explanatory',
             'content': '答案附上来源片段便于核对；上线前准备一组带标准答案的业务问题，分别统计正确率、引用支持率和单次成本。',
             'source_status': 'not_required', 'quote': None}]),
        _mode(True, '用户在评估方案，下一步多半关心具体做法', questions=[
            {'id': 'Q1', 'question': '权限控制一般怎么和向量检索结合？'},
            {'id': 'Q2', 'question': '评测集应该怎么准备，需要多少条？'}]),
        revised=('先把企业文档切分并建立向量索引，用户提问时检索最相关的片段，再交给大模型生成答案。可以选用开源向量数据库和商用大模型 API。\n\n'
                 '评估实施方案时还需要考虑三点：\n1. 权限控制：检索阶段按用户身份过滤文档，索引时同步保存每段文档的权限标签。\n'
                 '2. 文档更新：建立增量更新流程，记录内容版本，及时清理已删除文档的索引。\n'
                 '3. 引用与评测：答案附来源片段；上线前用一组带标准答案的业务问题统计正确率、引用支持率和单次成本。'),
        changes=[{'finding_ids': ['M1', 'M2', 'M3'], 'summary': '补充权限、更新与评测三个落地要点'}]),
    _case(
        'python-csv', '回答没问题，猜你下一步问什么', ['predict'],
        'Python 里怎么读取 CSV 文件？',
        '可以用内置的 csv 模块：with open("data.csv", newline="") as f: rows = list(csv.reader(f))。如果要做数据分析，用 pandas 更方便：df = pd.read_csv("data.csv")。',
        _mode(False, '回答是操作方法，没有需要核实的事实断言', verdict='not_run', claims=[]),
        _mode(False, '两种常用做法都已给出', points=[]),
        _mode(True, '读取 CSV 后常见的后续问题', questions=[
            {'id': 'Q1', 'question': '读取时中文出现乱码怎么办？'},
            {'id': 'Q2', 'question': '怎么只读取其中几列？'},
            {'id': 'Q3', 'question': '文件很大时怎么分块读取？'}])),
    _case(
        'photosynthesis', '三种模式同时触发', ['correct', 'supplement', 'predict'],
        '光合作用的产物是什么？在哪里进行？',
        '光合作用把二氧化碳和水转化为葡萄糖和氧气，整个过程都在线粒体中进行。',
        _mode(True, '回答给出了产物与发生部位两条事实', verdict='errors_found', claims=[
            {'id': 'C1', 'text': '整个过程都在线粒体中进行', 'span': [22, 34], 'tier': 'open_claim',
             'verdict': 'contradicted', 'explanation': '证据说明植物的光合作用在叶绿体中进行。',
             'quote': _quote('In plants, photosynthesis takes place in chloroplasts'), 'correction': '植物的光合作用在叶绿体中进行，而不是线粒体。'}]),
        _mode(True, '只说了产物，没有说明过程分为两个阶段', points=[
            {'id': 'M1', 'point': '光反应与暗反应两个阶段', 'why': '理解“在哪里进行”需要知道两个阶段的分工', 'kind': 'explanatory',
             'content': '光合作用分为光反应和暗反应（碳反应）两个阶段：前者在类囊体膜上把光能转化为化学能并释放氧气，后者在叶绿体基质中固定二氧化碳、合成糖类。',
             'source_status': 'not_required', 'quote': None}]),
        _mode(True, '用户常把光合作用与呼吸作用放在一起比较', questions=[
            {'id': 'Q1', 'question': '光合作用和细胞呼吸有什么区别？'}]),
        revised=('光合作用把二氧化碳和水转化为葡萄糖和氧气。植物的光合作用在叶绿体中进行，分为两个阶段：'
                 '光反应在类囊体膜上把光能转化为化学能并释放氧气，暗反应（碳反应）在叶绿体基质中固定二氧化碳、合成糖类。'),
        changes=[{'finding_ids': ['C1'], 'summary': '将发生部位由线粒体改为叶绿体'},
                 {'finding_ids': ['M1'], 'summary': '补充光反应与暗反应两个阶段'}],
        evidence=[{'id': 'D1', 'title': DEMO_SOURCE, 'text': PHOTO_EVIDENCE}]),
]


def catalog():
    return {'cases': CASES, 'source': 'demo', 'note': '案例由项目团队编写，结果为预设演示，不是实时运行记录，也不收录用户记录。'}
