#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.secret_store import encrypt_secret_text, is_encrypted, is_secret_env_key


def transform_env_lines(lines):
    updated = []
    changed = 0
    for raw_line in lines:
        line = raw_line.rstrip('\n')
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in line:
            updated.append(raw_line)
            continue

        key, value = line.split('=', 1)
        env_key = key.strip()
        if env_key == 'MARKANM_SECRET_KEY':
            updated.append(raw_line)
            continue
        if not is_secret_env_key(env_key):
            updated.append(raw_line)
            continue

        plain_value = value.strip()
        if not plain_value or is_encrypted(plain_value):
            updated.append(raw_line)
            continue

        encrypted = encrypt_secret_text(plain_value)
        updated.append(f'{env_key}={encrypted}\n')
        changed += 1
    return updated, changed


def main():
    parser = argparse.ArgumentParser(
        description='Encrypt secret-looking values inside a .env file using MARKANM_SECRET_KEY.'
    )
    parser.add_argument('env_file', nargs='?', default='.env', help='Path to the env file to rewrite')
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        raise SystemExit(f'Env file not found: {env_path}')

    original = env_path.read_text(encoding='utf-8').splitlines(keepends=True)
    updated, changed = transform_env_lines(original)

    if changed == 0:
        print(f'No plaintext secret values found in {env_path}')
        return

    backup_path = env_path.with_suffix(env_path.suffix + '.bak')
    backup_path.write_text(''.join(original), encoding='utf-8')
    env_path.write_text(''.join(updated), encoding='utf-8')
    print(f'Encrypted {changed} values in {env_path}')
    print(f'Backup written to {backup_path}')
    print('Keep MARKANM_SECRET_KEY outside git if you want these encrypted values to stay protected.')


if __name__ == '__main__':
    main()
