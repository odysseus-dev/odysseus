package main

import "testing"

func TestAnalyzeFlagsMissingTestsAndSecurity(t *testing.T) {
	out := Analyze([]GitHubFile{
		{
			Filename:  "routes/auth_routes.py",
			Status:    "modified",
			Additions: 42,
			Deletions: 8,
			Changes:   50,
			Patch:     "+ token = request.cookies.get(\"session\")\n+ subprocess.run(cmd, shell=True)",
		},
	})
	if out.RiskLevel != "medium" && out.RiskLevel != "high" {
		t.Fatalf("expected medium/high risk, got %q score=%d", out.RiskLevel, out.RiskScore)
	}
	var sawMissingTests, sawSecurity, sawDanger bool
	for _, signal := range out.RiskSignals {
		if signal.Label == "No test files changed" {
			sawMissingTests = true
		}
		if signal.Label == "Security-sensitive surface" {
			sawSecurity = true
		}
		if signal.Label == "Dangerous API usage" {
			sawDanger = true
		}
	}
	if !sawMissingTests || !sawSecurity || !sawDanger {
		t.Fatalf("missing expected signals: tests=%v security=%v danger=%v all=%v", sawMissingTests, sawSecurity, sawDanger, out.RiskSignals)
	}
}

func TestAnalyzeCountsLanguagesAndTests(t *testing.T) {
	out := Analyze([]GitHubFile{
		{Filename: "static/js/engineeringMissions.ts", Status: "added", Additions: 120, Changes: 120},
		{Filename: "tests/test_engineering_missions.py", Status: "added", Additions: 20, Changes: 20},
	})
	if out.Languages["TypeScript"] != 1 {
		t.Fatalf("expected TypeScript count, got %#v", out.Languages)
	}
	if out.Totals.TestFiles != 1 {
		t.Fatalf("expected one test file, got %d", out.Totals.TestFiles)
	}
}
