package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

type Input struct {
	Files []GitHubFile `json:"files"`
}

type GitHubFile struct {
	Filename  string `json:"filename"`
	Status    string `json:"status"`
	Additions int    `json:"additions"`
	Deletions int    `json:"deletions"`
	Changes   int    `json:"changes"`
	Patch     string `json:"patch"`
}

type Signal struct {
	Severity string `json:"severity"`
	Label    string `json:"label"`
	Detail   string `json:"detail"`
	File     string `json:"file,omitempty"`
}

type FileBreakdown struct {
	Filename  string `json:"filename"`
	Status    string `json:"status"`
	Additions int    `json:"additions"`
	Deletions int    `json:"deletions"`
	Changes   int    `json:"changes"`
	Language  string `json:"language"`
	Kind      string `json:"kind"`
}

type Totals struct {
	Files      int `json:"files"`
	Additions  int `json:"additions"`
	Deletions  int `json:"deletions"`
	TestFiles  int `json:"test_files"`
	Source     int `json:"source_files"`
	Docs       int `json:"docs_files"`
	Config     int `json:"config_files"`
	Dependency int `json:"dependency_files"`
}

type Output struct {
	Engine          string          `json:"engine"`
	RiskScore       int             `json:"risk_score"`
	RiskLevel       string          `json:"risk_level"`
	Totals          Totals          `json:"totals"`
	Languages       map[string]int  `json:"languages"`
	RiskSignals     []Signal        `json:"risk_signals"`
	FileBreakdown   []FileBreakdown `json:"file_breakdown"`
	Recommendations []string        `json:"recommendations"`
}

var (
	testRe        = regexp.MustCompile(`(?i)(^|/)(__tests__|tests?|spec)/|(_test|\.test|\.spec)\.`)
	configRe      = regexp.MustCompile(`(?i)(^|/)(Dockerfile|docker-compose.*\.ya?ml|\.github/|nginx|caddy|k8s|helm|terraform|\.env|config/)`)
	dependencyRe  = regexp.MustCompile(`(?i)(^|/)(package-lock\.json|package\.json|pnpm-lock\.yaml|yarn\.lock|requirements.*\.txt|pyproject\.toml|poetry\.lock|go\.mod|go\.sum|Cargo\.toml|Cargo\.lock|Gemfile|Gemfile\.lock)$`)
	securityRe    = regexp.MustCompile(`(?i)\b(auth|token|secret|password|cookie|jwt|session|csrf|oauth|permission|privilege|encrypt|decrypt)\b`)
	dangerousAPIRe = regexp.MustCompile(`(?i)\b(eval|exec|subprocess|shell=True|innerHTML|dangerouslySetInnerHTML|os\.system|child_process|pickle\.loads|yaml\.load)\b`)
	sqlRe         = regexp.MustCompile(`(?i)\b(ALTER TABLE|CREATE TABLE|DROP TABLE|DELETE FROM|INSERT INTO|UPDATE .+ SET|SELECT .+ FROM)\b`)
)

func main() {
	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		fail(err)
	}
	var input Input
	if err := json.Unmarshal(raw, &input); err != nil {
		fail(err)
	}
	out := Analyze(input.Files)
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(out); err != nil {
		fail(err)
	}
}

func Analyze(files []GitHubFile) Output {
	out := Output{
		Engine:        "go-diff-analyzer",
		Languages:     map[string]int{},
		RiskSignals:   []Signal{},
		FileBreakdown: []FileBreakdown{},
	}

	for _, file := range files {
		kind := classifyKind(file)
		lang := languageFor(file.Filename)
		out.Totals.Files++
		out.Totals.Additions += file.Additions
		out.Totals.Deletions += file.Deletions
		if lang != "" {
			out.Languages[lang]++
		}
		switch kind {
		case "test":
			out.Totals.TestFiles++
		case "docs":
			out.Totals.Docs++
		case "config":
			out.Totals.Config++
		case "dependency":
			out.Totals.Dependency++
		default:
			out.Totals.Source++
		}
		out.FileBreakdown = append(out.FileBreakdown, FileBreakdown{
			Filename: file.Filename, Status: file.Status, Additions: file.Additions,
			Deletions: file.Deletions, Changes: file.Changes, Language: lang, Kind: kind,
		})
		out.RiskSignals = append(out.RiskSignals, signalsFor(file, kind)...)
	}

	if out.Totals.Source > 0 && out.Totals.TestFiles == 0 {
		out.RiskSignals = append(out.RiskSignals, Signal{
			Severity: "high",
			Label:    "No test files changed",
			Detail:   "Source files changed without an obvious test or spec update.",
		})
	}
	if out.Totals.Dependency > 0 {
		out.RiskSignals = append(out.RiskSignals, Signal{
			Severity: "medium",
			Label:    "Dependency surface changed",
			Detail:   "Dependency or lock files changed; verify supply-chain and runtime impact.",
		})
	}
	sort.Slice(out.FileBreakdown, func(i, j int) bool {
		return out.FileBreakdown[i].Changes > out.FileBreakdown[j].Changes
	})

	out.RiskScore = score(out)
	out.RiskLevel = riskLevel(out.RiskScore)
	out.Recommendations = recommendations(out)
	return out
}

func classifyKind(file GitHubFile) string {
	name := file.Filename
	lower := strings.ToLower(name)
	switch {
	case testRe.MatchString(name):
		return "test"
	case dependencyRe.MatchString(name):
		return "dependency"
	case configRe.MatchString(name):
		return "config"
	case strings.HasSuffix(lower, ".md") || strings.HasSuffix(lower, ".rst") || strings.HasPrefix(lower, "docs/"):
		return "docs"
	default:
		return "source"
	}
}

func languageFor(filename string) string {
	ext := strings.ToLower(filepath.Ext(filename))
	switch ext {
	case ".py":
		return "Python"
	case ".ts", ".tsx":
		return "TypeScript"
	case ".js", ".jsx", ".mjs":
		return "JavaScript"
	case ".go":
		return "Go"
	case ".rs":
		return "Rust"
	case ".sql":
		return "SQL"
	case ".html":
		return "HTML"
	case ".css", ".scss":
		return "CSS"
	case ".yml", ".yaml":
		return "YAML"
	case ".json":
		return "JSON"
	case ".md":
		return "Markdown"
	case ".toml":
		return "TOML"
	case ".sh":
		return "Shell"
	default:
		return strings.TrimPrefix(ext, ".")
	}
}

func signalsFor(file GitHubFile, kind string) []Signal {
	var signals []Signal
	name := file.Filename
	patch := file.Patch
	if file.Changes >= 500 {
		signals = append(signals, Signal{"high", "Large file delta", fmt.Sprintf("%d changed lines need careful review.", file.Changes), name})
	} else if file.Changes >= 250 {
		signals = append(signals, Signal{"medium", "Large file delta", fmt.Sprintf("%d changed lines may hide multiple concerns.", file.Changes), name})
	}
	if strings.EqualFold(file.Status, "removed") && kind == "test" {
		signals = append(signals, Signal{"high", "Test removed", "A test file was deleted; confirm coverage remains intact.", name})
	}
	if kind == "config" {
		signals = append(signals, Signal{"medium", "Runtime/config touched", "Infrastructure or runtime configuration changed.", name})
	}
	if kind == "dependency" {
		signals = append(signals, Signal{"medium", "Dependency file touched", "Dependency, lockfile, or build metadata changed.", name})
	}
	if securityRe.MatchString(name) || securityRe.MatchString(patch) {
		signals = append(signals, Signal{"high", "Security-sensitive surface", "Auth, token, session, encryption, or permission logic appears in the diff.", name})
	}
	if dangerousAPIRe.MatchString(patch) {
		signals = append(signals, Signal{"high", "Dangerous API usage", "Diff mentions dynamic execution, shell execution, unsafe parsing, or raw HTML injection.", name})
	}
	if sqlRe.MatchString(patch) {
		signals = append(signals, Signal{"medium", "Database behavior changed", "SQL/schema-like changes appear in the patch.", name})
	}
	if strings.Contains(strings.ToLower(name), "migration") {
		signals = append(signals, Signal{"medium", "Migration touched", "Migration files require rollback and compatibility review.", name})
	}
	return signals
}

func score(out Output) int {
	points := 0
	for _, signal := range out.RiskSignals {
		switch signal.Severity {
		case "high":
			points += 20
		case "medium":
			points += 11
		default:
			points += 5
		}
	}
	if out.Totals.Files > 10 {
		points += (out.Totals.Files - 10) * 2
	}
	points += (out.Totals.Additions + out.Totals.Deletions) / 120
	if out.Totals.Config > 0 {
		points += 8
	}
	if out.Totals.Dependency > 0 {
		points += 8
	}
	if points > 100 {
		return 100
	}
	return points
}

func riskLevel(score int) string {
	if score >= 65 {
		return "high"
	}
	if score >= 35 {
		return "medium"
	}
	return "low"
}

func recommendations(out Output) []string {
	recs := []string{"Run the focused tests for the changed language/package before approval."}
	if out.Totals.TestFiles == 0 && out.Totals.Source > 0 {
		recs = append(recs, "Ask for test evidence or add tests that exercise the changed source paths.")
	}
	if out.Totals.Dependency > 0 {
		recs = append(recs, "Review dependency and lockfile changes for supply-chain, license, and runtime impact.")
	}
	if out.Totals.Config > 0 {
		recs = append(recs, "Verify configuration changes in a staging-like environment.")
	}
	for _, signal := range out.RiskSignals {
		if signal.Label == "Security-sensitive surface" {
			recs = append(recs, "Manually review auth/security paths and confirm no secrets or privilege boundaries changed unexpectedly.")
			break
		}
	}
	return unique(recs)
}

func unique(values []string) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, value := range values {
		if value == "" || seen[value] {
			continue
		}
		seen[value] = true
		out = append(out, value)
	}
	return out
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
