"""Run the full suite with explicit collection/execution/skip accounting."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),top_level_dir=str(ROOT))
    collected=suite.countTestCases()
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    summary=dict(source_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                 collected=collected,tests_run=result.testsRun,executed=result.testsRun-len(result.skipped),
                 passed=result.testsRun-len(result.skipped)-len(result.failures)-len(result.errors)-len(result.unexpectedSuccesses),
                 failures=[{'test':str(t),'traceback':tb} for t,tb in result.failures],
                 errors=[{'test':str(t),'traceback':tb} for t,tb in result.errors],
                 skipped=[{'test':str(t),'reason':reason} for t,reason in result.skipped],
                 expected_failures=len(result.expectedFailures),unexpected_successes=len(result.unexpectedSuccesses),
                 ok=result.wasSuccessful())
    args.output.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('failures','errors','skipped')},ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':
    raise SystemExit(main())
