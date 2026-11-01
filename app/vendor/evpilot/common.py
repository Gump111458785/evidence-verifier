import hashlib,json,re,unicodedata
from pathlib import Path

def digest(value):
    if not isinstance(value,bytes): value=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(value).hexdigest()
def read_jsonl(path):
    with open(path) as f:
        for line in f:
            if line.strip():yield json.loads(line)
def write_jsonl(path,rows):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
def norm(text):
    return ' '.join(re.findall(r'\w+',unicodedata.normalize('NFKC',text).lower()))
def file_hash(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
