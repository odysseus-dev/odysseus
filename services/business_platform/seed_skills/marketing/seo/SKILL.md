---
name: seo
description: SEO audit and optimization: technical SEO, on-page optimization, content quality assessment, search performance. Use for SEO audits, meta tags, indexation, rankings, organic traffic.
version: 1.0.0
category: marketing
tags: [seo, marketing, audit, on-page]
status: published
confidence: 0.9
source: imported
created: 2026-06-13T01:30:00Z
---

> Source: jikime-adk marketing-seo (SkillsMP, vetted 2026-06-13)

# SEO Audit & Optimization

## Quick Reference (30 seconds)

SEO Audit Specialist - Identify SEO issues and provide actionable recommendations to improve organic search performance.

Core Philosophy:
- **Technical First**: Can Google find and index your pages?
- **Content Quality**: Does your content deserve to rank?
- **User Intent**: Does it answer what searchers want?

Audit Priority Order:
1. Crawlability & Indexation
2. Technical Foundations
3. On-Page Optimization
4. Content Quality
5. Authority & Links

---

## Implementation Guide (5 minutes)

### Audit Priority Framework

```
┌─────────────────────────────────────────────────────────────────┐
│                    SEO AUDIT PRIORITY                            │
├─────────────────────────────────────────────────────────────────┤
│  P1: CRAWLABILITY      Can Google find your pages?              │
│  ──────────────────────────────────────────────────────────     │
│  P2: INDEXATION        Are they being indexed?                  │
│  ──────────────────────────────────────────────────────────     │
│  P3: TECHNICAL         Is the site fast and functional?        │
│  ──────────────────────────────────────────────────────────     │
│  P4: ON-PAGE           Is content optimized?                    │
│  ──────────────────────────────────────────────────────────     │
│  P5: CONTENT           Does it deserve to rank?                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Technical SEO Audit

### Crawlability Checklist

| Check | What to Verify | Common Issues |
|-------|----------------|---------------|
| **robots.txt** | No unintentional blocks, sitemap referenced | Important pages blocked |
| **XML Sitemap** | Exists, submitted, canonical URLs only | Missing, outdated, non-indexable URLs |
| **Site Architecture** | Important pages < 3 clicks from home | Orphan pages, poor linking |
| **Crawl Budget** | Parameterized URLs controlled | Infinite scroll, session IDs in URLs |

### Indexation Checklist

| Check | What to Verify | Common Issues |
|-------|----------------|---------------|
| **Index Status** | site:domain.com, Search Console coverage | Missing pages, soft 404s |
| **Canonical Tags** | All pages have correct canonicals | Wrong direction, missing |
| **Noindex Tags** | Not on important pages | Accidental noindex |
| **Redirect Chains** | No loops, minimal chain length | Chains > 3 hops |

### Core Web Vitals Targets

| Metric | Good | Needs Improvement | Poor |
|--------|------|-------------------|------|
| **LCP** (Largest Contentful Paint) | < 2.5s | 2.5s - 4.0s | > 4.0s |
| **INP** (Interaction to Next Paint) | < 200ms | 200ms - 500ms | > 500ms |
| **CLS** (Cumulative Layout Shift) | < 0.1 | 0.1 - 0.25 | > 0.25 |

### Speed Optimization Factors

```
┌─────────────────────────────────────────────────────────────────┐
│  SPEED OPTIMIZATION CHECKLIST                                    │
├─────────────────────────────────────────────────────────────────┤
│  □ Server response time (TTFB) < 200ms                          │
│  □ Images optimized (WebP, lazy loading)                        │
│  □ JavaScript deferred/async                                     │
│  □ CSS delivery optimized (critical CSS inlined)                │
│  □ Caching headers set                                          │
│  □ CDN in use                                                    │
│  □ Font loading optimized (font-display: swap)                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## On-Page SEO Audit

### Title Tags

| Requirement | Best Practice | Common Issues |
|-------------|---------------|---------------|
| Length | 50-60 characters | Too long (truncated), too short |
| Keywords | Primary keyword near beginning | Keyword stuffing, missing |
| Uniqueness | Unique for each page | Duplicates |
| Compelling | Click-worthy | Boring, no value prop |

### Meta Descriptions

| Requirement | Best Practice | Common Issues |
|-------------|---------------|---------------|
| Length | 150-160 characters | Too long, too short |
| Keywords | Include primary keyword | Missing, stuffed |
| CTA | Clear call to action | No reason to click |
| Uniqueness | Unique per page | Duplicates, auto-generated |

### Heading Structure

```
┌─────────────────────────────────────────────────────────────────┐
│  CORRECT HEADING HIERARCHY                                       │
├─────────────────────────────────────────────────────────────────┤
│  H1: Page Title (one per page, contains primary keyword)        │
│  └── H2: Main Section (logical section breaks)                  │
│      └── H3: Subsection (details within section)                │
│          └── H4: Sub-subsection (if needed)                     │
│                                                                  │
│  AVOID:                                                          │
│  × Multiple H1s on same page                                    │
│  × Skipping levels (H1 → H3)                                    │
│  × Using headings for styling only                              │
└─────────────────────────────────────────────────────────────────┘
```

### Image Optimization

| Requirement | Best Practice |
|-------------|---------------|
| File names | Descriptive (blue-widget.jpg not IMG_123.jpg) |
| Alt text | Describes image content, includes keyword if natural |
| Compression | Optimized file size |
| Format | WebP with fallbacks |
| Loading | Lazy loading for below-fold images |
| Responsive | srcset for different screen sizes |

### Internal Linking

| Check | What to Verify |
|-------|----------------|
| Important pages | Well-linked from multiple pages |
| Anchor text | Descriptive, varies naturally |
| Broken links | No 404s on internal links |
| Orphan pages | All pages have at least one internal link |
| Link equity | Distributed to priority pages |

---

## Content Quality Assessment

### E-E-A-T Signals

| Factor | Signals to Look For |
|--------|---------------------|
| **Experience** | First-hand experience, original insights, real examples |
| **Expertise** | Author credentials, accurate details, proper sourcing |
| **Authoritativeness** | Industry recognition, cited by others, credentials |
| **Trustworthiness** | Accurate info, transparent, contact info, HTTPS |

### Content Quality Checklist

```
□ Comprehensive coverage of topic
□ Answers user's likely follow-up questions
□ Better/deeper than top-ranking competitors
□ Updated and current information
□ Unique perspective or value-add
□ Proper grammar and readability
□ Supporting visuals where helpful
```

---

## Common Issues by Site Type

| Site Type | Common Issues |
|-----------|---------------|
| **SaaS** | Thin product pages, blog not linked to product, no comparison pages |
| **E-commerce** | Duplicate product descriptions, thin categories, faceted nav duplicates |
| **Blog/Content** | Outdated content, keyword cannibalization, poor internal linking |
| **Local Business** | Inconsistent NAP, missing local schema, no location pages |

---

## Audit Report Format

### Executive Summary
- Overall health score
- Top 3-5 priority issues
- Quick wins identified

### Issue Documentation Format

```
Issue: [What's wrong]
Impact: HIGH / MEDIUM / LOW
Evidence: [How you found it]
Fix: [Specific recommendation]
Priority: P1 / P2 / P3
```

### Action Plan Structure

```
1. CRITICAL (Blocking indexation/ranking)
   └── Fix immediately

2. HIGH-IMPACT (Significant improvement potential)
   └── Address within 2 weeks

3. QUICK WINS (Easy, immediate benefit)
   └── Implement as time allows

4. LONG-TERM (Strategic improvements)
   └── Plan for next quarter
```

---

## Recommended Tools

### Free Tools

| Tool | Purpose |
|------|---------|
| Google Search Console | Index status, performance, issues |
| PageSpeed Insights | Core Web Vitals, speed |
| Rich Results Test | Schema validation |
| Mobile-Friendly Test | Mobile usability |

### Paid Tools

| Tool | Purpose |
|------|---------|
| Screaming Frog | Technical crawl audit |
| Ahrefs / Semrush | Backlinks, keyword tracking |
| Sitebulb | Visual technical audit |

---

## Works Well With

Skills:
- `jikime-marketing-page-cro` - Optimize pages for conversion after ranking
- `jikime-marketing-copywriting` - Write SEO-optimized copy
- `jikime-marketing-analytics` - Measure SEO performance

---

Version: 1.0.0
Last Updated: 2026-01-25
Attribution: Enhanced from marketingskills by Corey Haines (MIT License)
