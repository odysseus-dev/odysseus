#!/usr/bin/env python3
"""Reproducible benchmark for document persistence lifecycle browser tests.

Measures the 7 lifecycle components under /tmp/odysseus-test-perf-benchmark.lock:
1. Python subprocess startup
2. Node initialization
3. Chromium launch
4. Browser context creation
5. Scenario execution
6. Context teardown
7. Browser shutdown

Compares baseline (per-test subprocess & Chromium launch) vs optimized
(persistent worker reusing Chromium instance across isolated contexts).
"""

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def measure_lifecycle_breakdown(iterations=3):
    """Measure exact Playwright and Node process lifecycle steps."""
    js_code = r"""
    import { performance } from 'node:perf_hooks';
    import { chromium } from 'playwright';

    const t_node_start = performance.timeOrigin;
    const t_script_start = performance.now();

    const t0_launch = performance.now();
    const browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage', '--no-sandbox'] });
    const t1_launch = performance.now();

    const contextCreationTimes = [];
    const contextTeardownTimes = [];
    const scenarioTimes = [];

    for (let i = 0; i < 5; i++) {
        const t0_ctx = performance.now();
        const ctx = await browser.newContext({ viewport: { width: 900, height: 700 } });
        const page = await ctx.newPage();
        const t1_ctx = performance.now();
        contextCreationTimes.push(t1_ctx - t0_ctx);

        const t0_scen = performance.now();
        await page.setContent('<div>Benchmark Scenario</div>');
        await page.evaluate(() => document.querySelector('div').textContent);
        const t1_scen = performance.now();
        scenarioTimes.push(t1_scen - t0_scen);

        const t0_close = performance.now();
        await ctx.close();
        const t1_close = performance.now();
        contextTeardownTimes.push(t1_close - t0_close);
    }

    const t0_shutdown = performance.now();
    await browser.close();
    const t1_shutdown = performance.now();

    console.log(JSON.stringify({
        nodeInitMs: t_script_start,
        chromiumLaunchMs: t1_launch - t0_launch,
        contextCreateMs: contextCreationTimes.reduce((a, b) => a + b, 0) / contextCreationTimes.length,
        scenarioExecMs: scenarioTimes.reduce((a, b) => a + b, 0) / scenarioTimes.length,
        contextTeardownMs: contextTeardownTimes.reduce((a, b) => a + b, 0) / contextTeardownTimes.length,
        browserShutdownMs: t1_shutdown - t0_shutdown,
    }));
    """

    results = []
    py_spawn_times = []

    for _ in range(iterations):
        t0_py = time.perf_counter()
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", js_code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        t1_py = time.perf_counter()
        py_spawn_times.append((t1_py - t0_py) * 1000)
        data = json.loads(proc.stdout.strip())
        results.append(data)

    avg_py_spawn = sum(py_spawn_times) / len(py_spawn_times)
    avg_node_init = sum(r["nodeInitMs"] for r in results) / len(results)
    avg_chromium_launch = sum(r["chromiumLaunchMs"] for r in results) / len(results)
    avg_ctx_create = sum(r["contextCreateMs"] for r in results) / len(results)
    avg_scenario_exec = sum(r["scenarioExecMs"] for r in results) / len(results)
    avg_ctx_teardown = sum(r["contextTeardownMs"] for r in results) / len(results)
    avg_browser_shutdown = sum(r["browserShutdownMs"] for r in results) / len(results)

    return {
        "python_subprocess_startup_ms": avg_py_spawn,
        "node_initialization_ms": avg_node_init,
        "chromium_launch_ms": avg_chromium_launch,
        "browser_context_creation_ms": avg_ctx_create,
        "scenario_execution_ms": avg_scenario_exec,
        "context_teardown_ms": avg_ctx_teardown,
        "browser_shutdown_ms": avg_browser_shutdown,
    }


def main():
    print("=" * 68)
    print("ODYSSEUS DOCUMENT PERSISTENCE LIFECYCLE BENCHMARK")
    print("=" * 68)

    print("\nMeasuring low-level Playwright and process lifecycle components...")
    metrics = measure_lifecycle_breakdown(iterations=3)

    print(f"1. Python subprocess startup:     {metrics['python_subprocess_startup_ms']:>8.2f} ms")
    print(f"2. Node initialization:           {metrics['node_initialization_ms']:>8.2f} ms")
    print(f"3. Chromium launch:               {metrics['chromium_launch_ms']:>8.2f} ms")
    print(f"4. Browser context creation:      {metrics['browser_context_creation_ms']:>8.2f} ms")
    print(f"5. Scenario execution (probe):    {metrics['scenario_execution_ms']:>8.2f} ms")
    print(f"6. Context teardown:              {metrics['context_teardown_ms']:>8.2f} ms")
    print(f"7. Browser shutdown:              {metrics['browser_shutdown_ms']:>8.2f} ms")

    n_scenarios = 16

    # In baseline: steps 1, 2, 3, 4, 6, 7 are repeated for ALL 16 testcases.
    baseline_overhead = n_scenarios * (
        metrics["python_subprocess_startup_ms"]
        + metrics["chromium_launch_ms"]
        + metrics["browser_shutdown_ms"]
    )

    # In optimized: steps 1, 2, 3, 7 occur ONCE. Only steps 4 and 6 repeat per testcase.
    optimized_overhead = (
        metrics["python_subprocess_startup_ms"]
        + metrics["chromium_launch_ms"]
        + metrics["browser_shutdown_ms"]
        + n_scenarios * (metrics["browser_context_creation_ms"] + metrics["context_teardown_ms"])
    )

    savings_ms = baseline_overhead - optimized_overhead
    savings_pct = (savings_ms / baseline_overhead) * 100 if baseline_overhead else 0

    print("\n" + "-" * 68)
    print("OVERHEAD COMPARISON ACROSS 16 TESTCASES")
    print("-" * 68)
    print(f"Baseline startup & shutdown overhead:   {baseline_overhead/1000:>6.2f} s")
    print(f"Optimized startup & shutdown overhead:  {optimized_overhead/1000:>6.2f} s")
    print(f"Overhead eliminated:                   {savings_ms/1000:>6.2f} s ({savings_pct:.1f}%)")
    print("-" * 68)


if __name__ == "__main__":
    main()
