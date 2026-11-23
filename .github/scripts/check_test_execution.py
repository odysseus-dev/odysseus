"""Reject skipped or empty native CI runs without conflating driver child counts."""
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('report', type=Path, help='Path to JUnit XML report')
parser.add_argument('--minimum', type=int, default=1, help='Minimum expected test cases')
args = parser.parse_args()

if not args.report.exists():
    raise SystemExit(f'Report file does not exist: {args.report}')

root = ET.parse(args.report).getroot()
cases = list(root.iter('testcase'))

if len(cases) < args.minimum:
    raise SystemExit(f'Incomplete execution: {len(cases)} cases, expected at least {args.minimum}')

for case in cases:
    if any(case.find(tag) is not None for tag in ('skipped', 'failure', 'error')):
        raise SystemExit(f'Case did not pass: {case.get("classname")}::{case.get("name")}')

print(f'Verified {len(cases)} executed cases; no skipped, failed or errored cases')
