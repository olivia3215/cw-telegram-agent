#! /bin/sh

mkdir -p tmp
source venv/bin/activate
source .env

mv tmp/run4.log tmp/run5.log
mv tmp/run3.log tmp/run4.log
mv tmp/run2.log tmp/run3.log
mv tmp/run1.log tmp/run2.log
mv tmp/run.log tmp/run1.log

python run.py 2>&1 | tee tmp/run.log
