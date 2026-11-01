"""Check tracked-source candidates for private deployment paths and credential shapes."""
from pathlib import Path
import re
from build_release import ROOT, include

checks = {
    'private_path': re.compile(r'/(?:Users|root/projects|root/models)/'),
    'private_network': re.compile(r'\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b'),
    'credential_shape': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)'),
}
violations, count = [], 0
for path in ROOT.rglob('*'):
    if not path.is_file() or not include(path):
        continue
    try:
        text = path.read_text()
    except UnicodeDecodeError:
        continue
    count += 1
    for label, pattern in checks.items():
        if pattern.search(text):
            violations.append((str(path.relative_to(ROOT)), label))
if violations:
    raise SystemExit('Publication check failed (values withheld): '+repr(violations))
print(f'Publication source check: {count} text files; no matching private deployment paths or credential shapes. This is not an exhaustive secret audit.')
