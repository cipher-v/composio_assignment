#!/bin/bash
# waits for pass 1 to finish, retries any failed apps, then runs the verification chain
cd /e/compsio-product-ops
export PYTHONIOENCODING=utf-8 PYTHONWARNINGS=ignore
P=.venv/Scripts/python.exe
while powershell -NoProfile -Command "if (Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { \$_.CommandLine -like '*run.py research*' }) { exit 0 } else { exit 1 }"; do sleep 10; done
echo "== pass1 retry"; $P -u run.py research --workers 6
echo "== evidence1"; $P -u run.py evidence --pass 1 --force | tail -1
rm -rf data/pass2 data/evidence2
echo "== verify"; $P -u run.py verify --workers 8
echo "== verify retry"; $P -u run.py verify --workers 4
echo "== evidence2"; $P -u run.py evidence --pass 2 | tail -1
echo "== merge/analyze/review/page"; $P run.py merge && $P run.py analyze && $P run.py review --per-category 2 && $P run.py page
echo "== ALL DONE"
