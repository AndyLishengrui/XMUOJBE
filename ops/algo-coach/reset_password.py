#!/usr/bin/env python3
"""
Reset XMUOJ student password to 123456.
Usage:
  python3 reset_password.py <student_id> [real_name]
  Called from reset_password.sh or directly.

Also importable as a module for the Flask API endpoint.
"""

import sys
import subprocess
import json


def reset_password(username: str, real_name: str = None) -> dict:
    """
    Reset a user's password to '123456'.
    Returns {'ok': True, 'username': ..., 'real_name': ...} on success,
            {'ok': False, 'error': ...} on failure.
    """
    # Build Python code to run inside Django
    code = f"""
from django.contrib.auth import get_user_model
from account.models import UserProfile
import json

User = get_user_model()

try:
    user = User.objects.get(username='{username}')
except User.DoesNotExist:
    print(json.dumps({{'ok': False, 'error': 'user_not_found', 'username': '{username}'}}))
    exit()

profile = UserProfile.objects.filter(user=user).first()
profile_name = profile.real_name if profile else ''

# Verify real_name if provided
{'' if not real_name else f'''if profile_name != '{real_name}':
    print(json.dumps({{'ok': False, 'error': 'name_mismatch', 'expected': '{real_name}', 'actual': profile_name}}))
    exit()'''}

# Reset password
user.set_password('123456')
user.save()

print(json.dumps({{
    'ok': True,
    'username': user.username,
    'real_name': profile_name,
    'message': 'Password reset to 123456'
}}))
"""

    cmd = [
        "sudo", "docker", "exec", "oj-backend",
        "python3", "manage.py", "shell", "-c", code
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

    stdout = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout

    # Extract JSON from output (filter out Django startup noise)
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    return {"ok": False, "error": "unexpected_output", "raw": stdout[:500]}


def main():
    if len(sys.argv) < 2:
        print("Usage: reset_password.py <student_id> [real_name]")
        print("  student_id = 学号 (User.username)")
        print("  real_name  = 姓名 (optional, for verification)")
        sys.exit(1)

    username = sys.argv[1]
    real_name = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"=== Password Reset ===")
    print(f"Student ID: {username}")
    if real_name:
        print(f"Name: {real_name}")

    result = reset_password(username, real_name)

    if result.get("ok"):
        print(f"✓ {result['message']}")
        print(f"  Username: {result['username']}")
        print(f"  Real Name: {result['real_name']}")
        sys.exit(0)
    else:
        error_map = {
            "user_not_found": f"✗ User not found: {username}",
            "name_mismatch": f"✗ Name mismatch: expected '{result.get('expected')}', actual '{result.get('actual')}'",
            "no_profile": f"✗ No UserProfile for {username}",
        }
        print(error_map.get(result["error"], f"✗ Error: {result}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
