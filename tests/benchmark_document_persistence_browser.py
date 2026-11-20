#!/usr/bin/env python3
"""Reproducible benchmark for document persistence lifecycle browser tests.

Measures cleanly separated low-level lifecycle components, provides an equivalent
analytical model across 16 scenarios, and supports direct end-to-end pytest measurement
under /tmp/odysseus-test-perf-benchmark.lock.

Lifecycle components:
1. Node process execution (node -e ''): measures process execution including Node startup and VM bootstrap
2. Node bootstrap to script start (timeOrigin to script execution)
3. Dynamic Playwright import
4. Chromium browser launch (headless)
5. Browser context creation
6. Scenario DOM probe execution
7. Browser context teardown
8. Chromium browser shutdown
"""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def measure_isolated_components(iterations=3):
    """Measure cleanly separated lifecycle steps without double-counting."""
    # 1. Process execution overhead: node process spawn and VM bootstrap
    spawn_times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        subprocess.run(["node", "-e", ""], cwd=ROOT, check=True)
        t1 = time.perf_counter()
        spawn_times.append((t1 - t0) * 1000)
    avg_spawn_ms = sum(spawn_times) / len(spawn_times)

    # 2-8. In-process Playwright lifecycle breakdown
    js_code = r"""
    import { performance } from 'node:perf_hooks';

    const t_script_start = performance.now();
    const nodeInitMs = t_script_start; // time from timeOrigin (process start) to script start

    const { chromium } = await import('playwright');
    const t_after_import = performance.now();
    const playwrightImportMs = t_after_import - t_script_start;

    const t0_launch = performance.now();
    const browser = await chromium.launch({ headless: true });
    const t1_launch = performance.now();
    const chromiumLaunchMs = t1_launch - t0_launch;

    const ctxCreateTimes = [];
    const ctxTeardownTimes = [];
    const scenExecTimes = [];

    for (let i = 0; i < 5; i++) {
        const t0_c = performance.now();
        const ctx = await browser.newContext({ viewport: { width: 900, height: 700 } });
        const page = await ctx.newPage();
        const t1_c = performance.now();
        ctxCreateTimes.push(t1_c - t0_c);

        const t0_s = performance.now();
        await page.setContent('<div>Probe Content</div>');
        await page.evaluate(() => document.querySelector('div').textContent);
        const t1_s = performance.now();
        scenExecTimes.push(t1_s - t0_s);

        const t0_t = performance.now();
        await ctx.close();
        const t1_t = performance.now();
        ctxTeardownTimes.push(t1_t - t0_t);
    }

    const t0_close = performance.now();
    await browser.close();
    const t1_close = performance.now();
    const browserShutdownMs = t1_close - t0_close;

    console.log(JSON.stringify({
        nodeInitMs,
        playwrightImportMs,
        chromiumLaunchMs,
        contextCreateMs: ctxCreateTimes.reduce((a, b) => a + b, 0) / ctxCreateTimes.length,
        scenarioExecMs: scenExecTimes.reduce((a, b) => a + b, 0) / scenExecTimes.length,
        contextTeardownMs: ctxTeardownTimes.reduce((a, b) => a + b, 0) / ctxTeardownTimes.length,
        browserShutdownMs,
    }));
    """

    res = subprocess.run(
        ["node", "--input-type=module", "-e", js_code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(res.stdout.strip())

    return {
        "process_spawn_ms": avg_spawn_ms,
        "node_init_ms": data["nodeInitMs"],
        "playwright_import_ms": data["playwrightImportMs"],
        "chromium_launch_ms": data["chromiumLaunchMs"],
        "context_create_ms": data["contextCreateMs"],
        "scenario_exec_ms": data["scenarioExecMs"],
        "context_teardown_ms": data["contextTeardownMs"],
        "browser_shutdown_ms": data["browserShutdownMs"],
    }


def measure_pytest_suite():
    """Measure actual complete wall-clock execution of the persistence lifecycle test suite."""
    t0 = time.perf_counter()
    subprocess.run(
        ["pytest", "tests/test_document_persistence_lifecycle.py", "-q"],
        cwd=ROOT,
        check=True,
    )
    t1 = time.perf_counter()
    return t1 - t0


def main():
    parser = argparse.ArgumentParser(description="Document persistence browser test benchmark.")
    parser.add_argument(
        "--suite",
        action="store_true",
        help="Execute actual pytest test suite to record complete end-to-end wall-clock time.",
    )
    args = parser.parse_args()

    print("=" * 76)
    print("ODYSSEUS DOCUMENT PERSISTENCE LIFECYCLE BENCHMARK")
    print("=" * 76)

    print("\n1. DIRECTLY MEASURED COMPONENT TIMINGS")
    print("-" * 76)
    comp = measure_isolated_components(iterations=3)

    print(f"Node process execution (node -e ''):       {comp['process_spawn_ms']:>8.2f} ms  (includes Node VM startup)")
    print(f"Node bootstrap to script start:            {comp['node_init_ms']:>8.2f} ms")
    print(f"Playwright module dynamic import:          {comp['playwright_import_ms']:>8.2f} ms")
    print(f"Chromium browser launch (headless):        {comp['chromium_launch_ms']:>8.2f} ms")
    print(f"Browser context creation:                  {comp['context_create_ms']:>8.2f} ms")
    print(f"Scenario DOM probe execution:              {comp['scenario_exec_ms']:>8.2f} ms")
    print(f"Browser context teardown:                  {comp['context_teardown_ms']:>8.2f} ms")
    print(f"Chromium browser shutdown:                 {comp['browser_shutdown_ms']:>8.2f} ms")

    print("\n2. ANALYTICAL LIFECYCLE MODEL (EQUIVALENT COMPARISON ACROSS 16 SCENARIOS)")
    print("-" * 76)
    # Equivalent comparison:
    # Baseline launches Node, imports Playwright, launches Chromium, creates/tears down context, and closes browser for every testcase.
    # Optimized launches Node, imports Playwright, launches Chromium, closes browser ONCE, and creates/tears down context per testcase.
    per_test_baseline_lifecycle_ms = (
        comp["process_spawn_ms"]
        + comp["playwright_import_ms"]
        + comp["chromium_launch_ms"]
        + comp["context_create_ms"]
        + comp["context_teardown_ms"]
        + comp["browser_shutdown_ms"]
    )

    one_time_optimized_worker_lifecycle_ms = (
        comp["process_spawn_ms"]
        + comp["playwright_import_ms"]
        + comp["chromium_launch_ms"]
        + comp["browser_shutdown_ms"]
    )
    per_test_optimized_lifecycle_ms = comp["context_create_ms"] + comp["context_teardown_ms"]

    n = 16
    baseline_total_overhead_s = (n * per_test_baseline_lifecycle_ms) / 1000.0
    optimized_total_overhead_s = (
        one_time_optimized_worker_lifecycle_ms + (n * per_test_optimized_lifecycle_ms)
    ) / 1000.0
    saved_overhead_s = baseline_total_overhead_s - optimized_total_overhead_s

    print(f"Baseline repeated lifecycle overhead (16x):    {baseline_total_overhead_s:>6.2f} s")
    print(f"Optimized consolidated lifecycle overhead (16x): {optimized_total_overhead_s:>6.2f} s")
    print(f"Analytical overhead eliminated:                 {saved_overhead_s:>6.2f} s ({((saved_overhead_s)/baseline_total_overhead_s)*100:.1f}%)")

    print("\n3. ACTUAL END-TO-END SUITE EXECUTION")
    print("-" * 76)
    if args.suite:
        print("Running pytest tests/test_document_persistence_lifecycle.py...")
        suite_time = measure_pytest_suite()
        print(f"Complete suite wall-clock execution time:       {suite_time:>6.2f} s")
    else:
        print("Tip: Run with --suite to measure actual complete pytest wall-clock execution.")
    print("=" * 76)


if __name__ == "__main__":
    main()
