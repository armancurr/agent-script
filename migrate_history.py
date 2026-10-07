#!/usr/bin/env python3
"""Prepare rewritten Git history locally; publish only through a separate command."""
import argparse
from collections import Counter
import json
from pathlib import Path
import random
import re
import subprocess
import sys


def run(argv, data=None):
    p = subprocess.run(argv, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        # Avoid leaking credentials from Git URLs, helpers or server output.
        raise RuntimeError(f'{argv[0]} operation failed (exit {p.returncode}); inspect locally. No automatic retry.')
    return p.stdout


def git(repo, *args, data=None):
    return run(['git', '--git-dir=' + str(repo), *args], data)


def github(path):
    return json.loads(run(['gh', 'api', path]))


def refs(repo):
    return dict(line.split() for line in git(repo, 'for-each-ref', '--format=%(refname) %(objectname)').decode().splitlines())


def validate_slug(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value):
        raise ValueError('Repository must have OWNER/NAME form, without credentials')
    return value


def prepare(args):
    source = validate_slug(args.source)
    destination = validate_slug(args.destination)
    if source.lower() == destination.lower():
        raise ValueError('Source and destination must differ')
    pool = list(dict.fromkeys(args.agent))
    if len(pool) < 3:
        raise ValueError('Provide at least three distinct authorized --agent usernames')
    if not 3 <= args.count <= min(8, len(pool)):
        raise ValueError('--count must be 3–8 and no greater than the account pool')
    work = Path(args.workdir).resolve()
    work.mkdir(parents=True, exist_ok=False)
    # Directory creation is exclusive; never reuse or overwrite an earlier run.
    accounts = []
    for login in random.SystemRandom().sample(pool, args.count):
        if not re.fullmatch(r'[A-Za-z0-9-]+', login):
            raise ValueError('Invalid agent username')
        u = github('users/' + login)
        accounts.append({'login': u['login'], 'email': f"{u['id']}+{u['login']}@users.noreply.github.com"})
    repo = work / 'rewritten.git'
    url = 'https://github.com/' + source + '.git'
    run(['git', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
         '-c', 'http.extraHeader=', 'clone', '--mirror', url, str(repo)])
    original_refs = refs(repo)
    if not original_refs or any(not r.startswith(('refs/heads/', 'refs/tags/')) for r in original_refs):
        raise ValueError('Empty repository or unsupported refs; stopped without publishing')
    default = git(repo, 'symbolic-ref', 'HEAD').decode().strip()
    if default not in original_refs:
        raise ValueError('Source default branch not found')
    commits = git(repo, 'rev-list', '--reverse', '--topo-order', '--all').decode().splitlines()
    if len(commits) < args.count:
        raise ValueError('Fewer commits than selected accounts')
    mapping, assignments = {}, []
    counts = Counter()
    for i, old in enumerate(commits):
        raw = git(repo, 'cat-file', 'commit', old)
        header, message = raw.split(b'\n\n', 1)
        account = accounts[i % len(accounts)]
        identity = f"{account['login']} <{account['email']}>".encode()
        lines, skip = [], False
        for line in header.split(b'\n'):
            if line.startswith(b' '):
                if not skip:
                    lines.append(line)
                continue
            skip = False
            key = line.split(b' ', 1)[0]
            if key in (b'gpgsig', b'gpgsig-sha256', b'mergetag'):
                skip = True
                continue
            if key == b'parent':
                line = b'parent ' + mapping[line.split()[1].decode()].encode()
            if key in (b'author', b'committer'):
                timestamp = re.search(rb' (-?\d+ [+-]\d{4})$', line)
                if not timestamp:
                    raise ValueError('Unexpected identity date format')
                line = key + b' ' + identity + b' ' + timestamp[1]
            lines.append(line)
        rewritten = b'\n'.join(lines) + b'\n\n' + message
        new = git(repo, 'hash-object', '-w', '-t', 'commit', '--stdin', data=rewritten).decode().strip()
        if git(repo, 'cat-file', 'commit', new) != rewritten:
            raise ValueError('Commit verification failed')
        if next(l for l in lines if l.startswith(b'tree ')) != next(l for l in header.splitlines() if l.startswith(b'tree ')):
            raise ValueError('Tree verification failed')
        mapping[old] = new
        counts[account['login']] += 1
        assignments.append({'original': old, 'rewritten': new, 'account': account['login']})

    # Annotated tags can point to commits, trees, blobs or other tags.
    # Preserve tagger attribution and message, but remove invalidated signatures.
    def remap(oid):
        if oid in mapping:
            return mapping[oid]
        kind = git(repo, 'cat-file', '-t', oid).strip()
        if kind != b'tag':
            return oid
        raw = git(repo, 'cat-file', 'tag', oid)
        header, message = raw.split(b'\n\n', 1)
        lines = header.split(b'\n')
        target = next(l.split()[1].decode() for l in lines if l.startswith(b'object '))
        lines = [b'object ' + remap(target).encode() if l.startswith(b'object ') else l for l in lines]
        for marker in (b'-----BEGIN PGP SIGNATURE-----', b'-----BEGIN SSH SIGNATURE-----', b'-----BEGIN SIGNED MESSAGE-----'):
            if marker in message:
                message = message.split(marker, 1)[0]
        new = git(repo, 'hash-object', '-w', '-t', 'tag', '--stdin', data=b'\n'.join(lines) + b'\n\n' + message).decode().strip()
        mapping[oid] = new
        return new

    for ref, old in original_refs.items():
        git(repo, 'update-ref', ref, remap(old), old)
    git(repo, 'fsck', '--no-reflogs')
    if int(git(repo, 'rev-list', '--count', '--all')) != len(commits):
        raise ValueError('Commit count verification failed')
    # Disable accidental mirror pushes and point the local remote only at destination.
    git(repo, 'config', 'remote.origin.mirror', 'false')
    git(repo, 'remote', 'set-url', 'origin', 'https://github.com/' + destination + '.git')
    manifest = {'source': source, 'destination': destination, 'visibility': 'public',
                'default_branch': default.removeprefix('refs/heads/'), 'accounts': accounts,
                'commit_count': len(commits), 'account_counts': dict(counts),
                'original_refs': original_refs, 'rewritten_refs': refs(repo), 'commits': assignments}
    (work / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({k: manifest[k] for k in ('source', 'destination', 'commit_count', 'account_counts')}, indent=2))
    print('Prepared locally. Nothing created or pushed on GitHub. Review manifest.json before publishing.')


def publish(args):
    if not args.confirm_public:
        raise ValueError('Publishing exposes all reachable code/history publicly. Supply --confirm-public after review.')
    work = Path(args.workdir).resolve()
    m = json.loads((work / 'manifest.json').read_text())
    destination = validate_slug(m['destination'])
    if destination.lower() == validate_slug(m['source']).lower():
        raise ValueError('Destination matches source')
    repo = work / 'rewritten.git'
    if refs(repo) != m['rewritten_refs']:
        raise ValueError('Local refs differ from reviewed manifest')
    # Require a confirmed 404. Never overwrite an existing repository.
    lookup = subprocess.run(['gh', 'api', 'repos/' + destination], capture_output=True)
    if lookup.returncode == 0:
        raise ValueError('Destination already exists; stopped without pushing')
    if b'HTTP 404' not in lookup.stderr:
        raise ValueError('Could not confirm destination absence')
    owner, name = destination.split('/')
    owner_type = github('users/' + owner)['type']
    if owner_type != 'Organization':
        raise ValueError('This tool publishes only to an organization')
    payload = json.dumps({'name': name, 'private': False, 'auto_init': False}).encode()
    run(['gh', 'api', '--method', 'POST', 'orgs/' + owner + '/repos', '--input', '-'], payload)
    # Use explicit refspecs; never mirror or force-push.
    run(['git', '--git-dir=' + str(repo), '-c', 'credential.helper=',
         '-c', 'credential.helper=!gh auth git-credential', '-c', 'http.extraHeader=',
         'push', '--atomic', 'https://github.com/' + destination + '.git',
         *[r + ':' + r for r in m['rewritten_refs']]])
    run(['gh', 'api', '--method', 'PATCH', 'repos/' + destination, '--input', '-'],
        json.dumps({'default_branch': m['default_branch']}).encode())
    observed = run(['git', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
                    '-c', 'http.extraHeader=', 'ls-remote', '--refs', 'https://github.com/' + destination + '.git'])
    remote_refs = {r: sha for sha, r in (l.split() for l in observed.decode().splitlines())}
    if remote_refs != m['rewritten_refs'] or github('repos/' + destination)['private']:
        raise ValueError('Published, but final remote verification failed; inspect before retrying')
    (work / 'published.json').write_text(json.dumps({'url': 'https://github.com/' + destination,
                                                   'verified_refs': remote_refs}, indent=2) + '\n')
    print('Published and verified: https://github.com/' + destination)
    print('Open the repository and commits page in a signed-out browser to verify public visibility and attribution.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare', help='Read source and prepare locally; no GitHub writes')
    prep.add_argument('--source', required=True)
    prep.add_argument('--destination', required=True)
    prep.add_argument('--agent', action='append', required=True, help='Authorized GitHub username; repeat for pool')
    prep.add_argument('--count', type=int, default=3, help='Number of accounts randomly selected from pool (3–8)')
    prep.add_argument('--workdir', required=True, help='New local directory; must not exist')
    pub = sub.add_parser('publish', help='Create PUBLIC destination and push reviewed history')
    pub.add_argument('--workdir', required=True)
    pub.add_argument('--confirm-public', action='store_true')
    a = p.parse_args()
    try:
        (prepare if a.command == 'prepare' else publish)(a)
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        print('Stopped: ' + str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
