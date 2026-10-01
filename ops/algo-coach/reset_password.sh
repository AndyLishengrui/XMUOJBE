#!/bin/bash
# Reset XMUOJ student password to 123456
# Usage: reset_password.sh <student_id> [real_name]
#
# Remote call example (from local AI):
#   ssh ubuntu@xmuoj.com 'bash /opt/algo-coach/reset_password.sh 37120252204383 王荣汐'
set -euo pipefail
cd /opt/algo-coach
sudo python3 reset_password.py "$@"
