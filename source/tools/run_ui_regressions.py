"""Run every Node UI regression file; requires Node.js on PATH."""
from pathlib import Path
import shutil,subprocess,sys
root=Path(__file__).resolve().parents[1]
node=shutil.which('node')
if not node:sys.exit('Node.js is required for UI regression tests (validated with Node 22).')
for file in sorted((root/'tests/browser').glob('*.test.cjs')):
 result=subprocess.run([node,str(file)],cwd=root)
 if result.returncode:sys.exit(result.returncode)
print('All UI regression files passed.')
