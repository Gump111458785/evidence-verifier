"""Server-side operator CLI for Evidence Verifier invitation-only accounts.

Reads EV_REDIS_REST_URL, EV_REDIS_REST_TOKEN, and EV_CLOUD_BUDGET_ID from environment
(or uses --local-db for local SQLite development). Never embeds credentials in code.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'app') not in sys.path:
    sys.path.insert(0, str(ROOT / 'app'))

from cloud_runtime.auth import Accounts, generate_temp_password
from cloud_runtime.store import CloudError, Store


def open_accounts(local_db=None):
    if local_db:
        import server
        server.DB = Path(local_db)
        acct, backing = server.accounts()
        return acct, backing
    store = Store(os.environ)
    if not store.configured:
        raise CloudError(
            'storage_not_configured',
            'Missing EV_REDIS_REST_URL or EV_REDIS_REST_TOKEN (or pass --local-db for local workbench).',
            503,
        )
    return Accounts(store), None


def resolve_password(args):
    if getattr(args, 'generate_password', False):
        return generate_temp_password(), True
    if getattr(args, 'password_stdin', False):
        return sys.stdin.readline().rstrip('\r\n'), False
    return getpass.getpass('Password: '), False


def main(argv=None):
    parser = argparse.ArgumentParser(description='Manage Evidence Verifier invitation-only accounts.')
    parser.add_argument('--local-db', help='Optional path to local SQLite DB instead of Upstash Redis.')
    sub = parser.add_subparsers(dest='command', required=True)

    create = sub.add_parser('create', help='Create an invited account.')
    create.add_argument('--username', required=True)
    create.add_argument('--email', required=True)
    create.add_argument('--display-name', default='')
    create.add_argument('--role', choices=('admin', 'member'), default='member')
    create.add_argument('--password-stdin', action='store_true')
    create.add_argument('--generate-password', action='store_true')

    sub.add_parser('list', help='List invited accounts.')

    reset = sub.add_parser('reset-password', help='Reset an invited account password and revoke sessions.')
    reset.add_argument('--identifier', required=True, help='Username, email, or user ID.')
    reset.add_argument('--password-stdin', action='store_true')
    reset.add_argument('--generate-password', action='store_true')

    status = sub.add_parser('set-status', help='Enable or disable an invited account.')
    status.add_argument('--identifier', required=True, help='Username, email, or user ID.')
    status.add_argument('--status', choices=('active', 'disabled'), required=True)

    args = parser.parse_args(argv)
    accounts, backing = open_accounts(args.local_db)

    if args.command == 'list':
        print(json.dumps({'users': accounts.list_users()}, ensure_ascii=False, indent=2))
        return 0

    if args.command == 'create':
        password, generated = resolve_password(args)
        user = accounts.create_user(
            username=args.username,
            email=args.email,
            password=password,
            display_name=args.display_name,
            role=args.role,
            created_by='cli_operator',
        )
        if backing:
            backing.save()
        payload = {'user': user}
        if generated:
            payload['generated_password'] = password
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    target = accounts.get_user(args.identifier) or accounts.find_user(args.identifier)
    if not target:
        raise SystemExit(f'User not found: {args.identifier}')

    if args.command == 'reset-password':
        password, generated = resolve_password(args)
        updated = accounts.admin_update_user(target['id'], new_password=password)
        if backing:
            backing.save()
        payload = {'user': updated}
        if generated:
            payload['generated_password'] = password
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == 'set-status':
        updated = accounts.admin_update_user(target['id'], status=args.status)
        if backing:
            backing.save()
        print(json.dumps({'user': updated}, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == '__main__':
    raise SystemExit(main())
