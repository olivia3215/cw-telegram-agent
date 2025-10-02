#! /bin/sh

mkdir -p tmp
source venv/bin/activate
source .env

find . -name "*.pyc" -delete
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

mv tmp/run4.log tmp/run5.log
mv tmp/run3.log tmp/run4.log
mv tmp/run2.log tmp/run3.log
mv tmp/run1.log tmp/run2.log
mv tmp/run.log tmp/run1.log

# export GEMINI_DEBUG_LOGGING=true
PYTHONPATH=src python run.py 2>&1 | tee tmp/run.log
